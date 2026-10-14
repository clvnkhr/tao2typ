"""CLI gzip inputs remain readable without changing explicit plaintext outputs."""

import sys
from pathlib import Path

import pytest

from tao2typ import cli, pipeline
from tao2typ.ir import Node, Post
from tao2typ.storage import read_bytes, read_json, read_text, write_json, write_text


@pytest.mark.parametrize("suffix", [".typ", ".typ.gz"])
def test_render_compressed_ir_and_compile_requested_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    archive = tmp_path / "archive"
    ir = archive / "ir/post.json.gz"
    post = Post("Example", "https://example.com/post/", body=[Node("text", "café")])
    write_json(ir, post.to_dict())
    output = archive / "typst" / ("example" + suffix)
    pdf = archive / "pdfs/example.pdf"
    compiled: list[Path] = []

    def compiler(source: Path, pdf: Path, root: Path) -> None:
        assert source.suffix == ".typ"
        assert root == archive.resolve()
        compiled.append(source)
        pdf.write_bytes(b"%PDF-test\n" + source.read_bytes())

    monkeypatch.setattr(cli, "compile_typst", compiler)
    monkeypatch.setattr(
        sys,
        "argv",
        ["tao2typ", "render", str(ir), "--output", str(output), "--pdf", str(pdf)],
    )
    assert cli.main() == 0
    assert len(compiled) == 1
    assert "café" in read_text(output)
    assert pdf.read_bytes() == b"%PDF-test\n" + read_bytes(output)
    assert output.read_bytes().startswith(b"\x1f\x8b") == (suffix == ".typ.gz")
    assert not list(archive.rglob("*.tmp*"))


def test_convert_saved_gzip_html_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    html = tmp_path / "input.html.gz"
    write_text(
        html,
        '<h1 class="entry-title">Local article</h1>'
        '<div class="entry-content"><p>Some café text.</p></div>',
    )
    output = tmp_path / "archive"

    def compiler(source: Path, pdf: Path, root: Path) -> None:
        assert "Some café text." in source.read_text()
        pdf.write_bytes(b"%PDF-test")

    monkeypatch.setattr(pipeline, "compile_typst", compiler)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tao2typ",
            "convert",
            str(html),
            "--url",
            "https://terrytao.wordpress.com/2026/10/14/local-article/",
            "--output",
            str(output),
        ],
    )
    assert cli.main() == 0
    assert "local-article.typ.gz" in capsys.readouterr().out
    post = read_json(output / "ir/local-article.json.gz")
    assert post["title"] == "Local article"
    assert post["source_manifest"].endswith(".json.gz")
