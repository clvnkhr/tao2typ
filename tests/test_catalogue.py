"""Metadata builds stay small; explicit public releases export only downloads."""

import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup, Tag

from tao2typ.catalogue import _failure_summary, generate_site
from tao2typ.storage import write_bytes


def selected(parent: Tag, selector: str) -> Tag:
    element = parent.select_one(selector)
    assert isinstance(element, Tag), f"Missing element: {selector}"
    return element


def attribute(element: Tag, name: str) -> str:
    value = element[name]
    assert isinstance(value, str), f"Expected a text attribute: {name}"
    return value


def fixture_archive(tmp_path: Path) -> Path:
    archive = tmp_path / "archive"
    (archive / "pdfs").mkdir(parents=True)
    (archive / "typst").mkdir()
    (archive / "ir").mkdir()
    (archive / "pdfs" / "einstein-e-mc2.pdf").write_bytes(b"PRIVATE-PDF")
    (archive / "typst" / "einstein-e-mc2.typ").write_text("PRIVATE-TYPST")
    (archive / "ir" / "einstein-e-mc2.json").write_text('{"body":"PRIVATE-BODY"}')
    (archive / "state.json").write_text(
        json.dumps(
            {
                "posts": {
                    "1": {
                        "title": 'Einstein’s <derivation> & "E=mc²"',
                        "published_at": "2007-12-28T12:00:00+00:00",
                        "converted_at": "2026-10-14T01:15:00+02:00",
                        "url": "https://terrytao.wordpress.com/2007/12/28/einstein/",
                        "pdf": "pdfs/einstein-e-mc2.pdf",
                        "typst": "typst/einstein-e-mc2.typ",
                        "ir": "ir/einstein-e-mc2.json",
                        "tags": [{"id": 2, "name": "math.GM", "slug": "mathgm"}],
                        "categories": [
                            {"id": 3, "name": "Expository", "slug": "expository"},
                            {
                                "id": 4,
                                "name": "Physics",
                                "slug": "physics",
                                "parent_id": 3,
                            },
                        ],
                        "pending_comments": ["PRIVATE-COMMENT"],
                        "last_error": "PRIVATE-ERROR",
                        "body": "PRIVATE-BODY",
                    },
                    "2": {
                        "title": "Queued post",
                        "published_at": "2026-10-13T12:00:00Z",
                        "converted_at": "2026-10-12T12:00:00Z",
                        "url": "https://terrytao.wordpress.com/2026/10/13/queued/",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    return archive


def test_catalogue_counts_private_links_and_no_document_copies(tmp_path: Path) -> None:
    archive = fixture_archive(tmp_path)
    output = tmp_path / "site"
    counts = generate_site(archive, output)
    assert counts == {
        "total": 2,
        "converted": 1,
        "queued": 1,
        "failed": 1,
        "tags": 1,
        "categories": 2,
    }
    assert {file.name for file in output.iterdir()} == {
        "index.html",
        "style.css",
        "app.js",
        ".nojekyll",
    }
    page = (output / "index.html").read_text()
    assert "PRIVATE-" not in page
    assert "pending_comments" not in page and "last_error" not in page
    soup = BeautifulSoup(page, "html.parser")
    assert attribute(selected(soup, "a.pdf"), "href") == (
        "https://github.com/clvnkhr/tao2typ/blob/main/archive/pdfs/einstein-e-mc2.pdf"
    )
    assert attribute(selected(soup, "a.typst"), "href") == (
        "https://github.com/clvnkhr/tao2typ/blob/main/archive/typst/einstein-e-mc2.typ"
    )
    assert attribute(selected(soup, "a.ir"), "href") == (
        "https://github.com/clvnkhr/tao2typ/blob/main/archive/ir/einstein-e-mc2.json"
    )
    assert selected(soup, "header nav a").get_text() == "Tao’s Blog"
    assert attribute(soup.select("header nav a")[1], "href") == (
        "https://github.com/clvnkhr/tao2typ"
    )
    assert soup.select(".post")[0]["data-title"] == "Queued post"
    assert "archive" not in soup.get_text().lower()
    assert selected(soup, ".site-note").get_text() == (
        "An unofficial personal site converting Terence Tao’s blog posts to PDFs with Typst."
    )
    assert selected(soup, "a.pdf").get_text() == "PDF"
    assert selected(soup, "a.typst").get_text() == ".typ"
    assert "Pending" not in soup.get_text()
    assert selected(soup.select(".post")[0], ".availability").get_text() == ""
    assert selected(soup.select(".post")[1], ".availability").get_text() == ("Converted 2026-10-13")
    assert attribute(selected(soup.select(".post")[1], ".availability time"), "datetime") == (
        "2026-10-13"
    )
    ready = soup.select(".post")[1]
    assert ready["data-id"] == "1"
    assert ready["data-failed"] == "true"  # A failed refresh can retain an older PDF.
    assert selected(ready, ".failure-details summary").text == "Failed · Conversion failed"
    assert selected(soup, "#failed")["type"] == "checkbox"
    assert not soup.select('a[data-route="failures"]')


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (
            "Client error '404 Not Found' for url 'https://example.com/image.png'",
            "Download failed (HTTP 404)",
        ),
        ("Client error '429 Too Many Requests'", "Rate limited (HTTP 429)"),
        ("Server error '503 Service Unavailable'", "Download failed (HTTP 503)"),
        ("[Errno 8] nodename nor servname provided, or not known", "DNS lookup failed"),
        ("The read operation timed out", "Download timed out"),
        (
            r"Typst compilation failed: unknown command: \rightarow",
            r"Unrecognized TeX command: \rightarow",
        ),
        (r"Typst compilation failed: unknown command: \/", r"Unrecognized TeX command: \/"),
        ("Typst compilation failed: error: unclosed delimiter", "Unclosed math delimiter"),
        ("Typst compilation failed: error: unknown alignment", "Unsupported TeX alignment"),
        ("Typst compilation failed: unknown function", "PDF compilation failed"),
    ],
)
def test_failure_reasons(error: str, reason: str) -> None:
    assert _failure_summary(error) == reason


def test_local_error_details_are_escaped_and_timestamp_is_utc(tmp_path: Path) -> None:
    archive = fixture_archive(tmp_path)
    state = json.loads((archive / "state.json").read_text())
    error = 'Typst compilation failed: unclosed delimiter\n<script>alert("PRIVATE")</script>'
    state["posts"]["1"]["last_error"] = error
    state["posts"]["1"]["last_attempted_at"] = "2026-10-14T12:35:10+02:00"
    (archive / "state.json").write_text(json.dumps(state))
    output = tmp_path / "site"
    generate_site(archive, output, diagnostics_base_url="http://127.0.0.1:8765")
    soup = BeautifulSoup((output / "index.html").read_text(), "html.parser")
    row = selected(soup, '.post[data-id="1"]')
    assert row["data-attempted"] == "2026-10-14T10:35:10+00:00"
    assert selected(row, ".failure-details pre").text == error
    assert selected(row, ".failure-details time").text == "2026-10-14 10:35"
    assert len(soup.select("script")) == 1
    generate_site(archive, output)
    public_page = (output / "index.html").read_text()
    assert "Unclosed math delimiter" in public_page
    assert "PRIVATE" not in public_page and "__tao2typ_failure" not in public_page


def test_queue_includes_refreshes_in_processing_order_without_diagnostics(tmp_path: Path) -> None:
    archive = fixture_archive(tmp_path)
    (archive / "queue.json").write_text(
        json.dumps(
            {
                "updated_at": "2026-10-14T09:00:00+00:00",
                "posts": [
                    {"id": 1, "reasons": ["recent-comment"], "error": "PRIVATE-ERROR"},
                    {"id": 2, "reasons": ["initial", "source-modified"]},
                    {"id": 1, "reasons": ["birthday"]},
                    {"id": 999, "reasons": ["initial"]},
                ],
            }
        )
    )
    output = tmp_path / "site"
    counts = generate_site(archive, output)
    assert counts["queued"] == 2  # A queued refresh can already have a PDF.
    page = (output / "index.html").read_text()
    assert "PRIVATE-" not in page
    soup = BeautifulSoup(page, "html.parser")
    rows = soup.select(".post")
    assert rows[0]["data-queue-position"] == "1"
    assert rows[1]["data-queue-position"] == "0"
    assert rows[1]["data-queue-status"] == "retry"
    assert selected(rows[1], ".queue-status").get_text() == "Retry · Recent comments"
    assert selected(rows[0], ".queue-status").get_text() == "Waiting · First conversion"
    assert attribute(selected(soup, 'a[data-route="queue"]'), "href") == "#queue"
    assert "2026-10-14T09:00:00+00:00" in page


def test_empty_queue_does_not_list_every_unconverted_post(tmp_path: Path) -> None:
    archive = fixture_archive(tmp_path)
    (archive / "queue.json").write_text('{"posts": [], "updated_at": ""}')
    output = tmp_path / "site"
    assert generate_site(archive, output)["queued"] == 0
    soup = BeautifulSoup((output / "index.html").read_text(), "html.parser")
    assert all(row["data-queue-position"] == "" for row in soup.select(".post"))


@pytest.mark.parametrize(
    "converted_at", [None, "", "not a date", "2026-02-30T12:00:00Z", ["2026-10-13"]]
)
def test_missing_or_invalid_conversion_date_stays_blank(
    tmp_path: Path, converted_at: object
) -> None:
    archive = fixture_archive(tmp_path)
    raw = json.loads((archive / "state.json").read_text())
    raw["posts"]["1"]["converted_at"] = converted_at
    (archive / "state.json").write_text(json.dumps(raw))
    output = tmp_path / "site"
    generate_site(archive, output)
    soup = BeautifulSoup((output / "index.html").read_text(), "html.parser")
    ready_post = selected(soup, '.post[data-ready="true"]')
    assert selected(ready_post, ".availability").get_text() == ""
    assert selected(ready_post, ".post-meta > time").get_text() == "2007-12-28"


def test_titles_and_taxonomy_are_escaped_and_original_names_retained(tmp_path: Path) -> None:
    output = tmp_path / "site"
    generate_site(fixture_archive(tmp_path), output)
    soup = BeautifulSoup((output / "index.html").read_text(), "html.parser")
    assert len(soup.select("script")) == 1
    assert soup.select("h3")[1].get_text() == 'Einstein’s <derivation> & "E=mc²"'
    assert selected(soup, "a.tag").get_text() == "math.GM"
    assert attribute(selected(soup, "a.tag"), "href") == "#tag=mathgm"
    category = soup.select_one('.taxonomy a[data-route="category=physics"]')
    assert category is not None and "depth-1" in category["class"]
    assert json.loads(attribute(soup.select(".post")[1], "data-tags")) == ["mathgm"]
    assert json.loads(attribute(soup.select(".post")[1], "data-categories")) == [
        "expository",
        "physics",
    ]


def test_paths_are_encoded_and_missing_or_unsafe_documents_not_linked(tmp_path: Path) -> None:
    archive = fixture_archive(tmp_path)
    filename = "space & symbol#.pdf"
    (archive / "pdfs" / filename).write_bytes(b"PRIVATE-PDF")
    raw = json.loads((archive / "state.json").read_text())
    raw["posts"]["1"]["pdf"] = f"pdfs/{filename}"
    raw["posts"]["1"]["typst"] = "../private.typ"
    raw["posts"]["2"]["pdf"] = "pdfs/missing.pdf"
    raw["posts"]["2"]["url"] = "javascript:alert(1)"
    (archive / "state.json").write_text(json.dumps(raw))
    output = tmp_path / "site"
    generate_site(archive, output, repo_url="https://github.com/example/repo/", branch="pages/test")
    soup = BeautifulSoup((output / "index.html").read_text(), "html.parser")
    assert attribute(selected(soup, "a.pdf"), "href") == (
        "https://github.com/example/repo/blob/pages%2Ftest/archive/pdfs/space%20%26%20symbol%23.pdf"
    )
    assert not soup.select("a.typst")
    assert attribute(selected(soup.select(".post")[0], "a.original"), "href") == "#"
    assert len(soup.select("a.pdf")) == 1


def test_invalid_repository_url_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Repository URL"):
        generate_site(fixture_archive(tmp_path), tmp_path / "site", repo_url="javascript:alert(1)")


def test_taxonomy_route_encodes_special_characters(tmp_path: Path) -> None:
    archive = fixture_archive(tmp_path)
    raw = json.loads((archive / "state.json").read_text())
    raw["posts"]["1"]["tags"] = [{"id": 8, "name": 'Sets <script> & "spaces"', "slug": "a & b/#"}]
    (archive / "state.json").write_text(json.dumps(raw))
    output = tmp_path / "site"
    generate_site(archive, output)
    soup = BeautifulSoup((output / "index.html").read_text(), "html.parser")
    assert attribute(selected(soup, "a.tag"), "href") == "#tag=a%20%26%20b%2F%23"
    assert selected(soup, "a.tag").get_text() == 'Sets <script> & "spaces"'
    assert len(soup.select("script")) == 1


def test_parent_category_filter_includes_descendant_posts(tmp_path: Path) -> None:
    archive = fixture_archive(tmp_path)
    raw = json.loads((archive / "state.json").read_text())
    parent, child = raw["posts"]["1"]["categories"]
    raw["posts"]["1"]["categories"] = [child]
    raw["posts"]["2"]["categories"] = [parent]
    (archive / "state.json").write_text(json.dumps(raw))
    output = tmp_path / "site"
    generate_site(archive, output)
    soup = BeautifulSoup((output / "index.html").read_text(), "html.parser")
    assert selected(soup, '.taxonomy a[data-route="category=expository"] span').text == "2"
    assert json.loads(attribute(soup.select(".post")[1], "data-categories")) == [
        "expository",
        "physics",
    ]
    # The post itself still shows only the category assigned by the source.
    assert selected(soup.select(".post")[1], ".post-taxonomy a.category").text == "Physics"


def test_public_download_base_exports_referenced_documents_only(tmp_path: Path) -> None:
    archive = fixture_archive(tmp_path)
    (archive / "queue.json").write_text('{"posts": [], "internal": "PRIVATE-QUEUE"}')
    (archive / "jobs").mkdir()
    (archive / "jobs" / "job.json").write_text("PRIVATE-JOB")
    (archive / "failures").mkdir()
    (archive / "failures" / "failed.typ").write_text("PRIVATE-FAILURE")
    (archive / "pdfs" / "unreferenced.pdf").write_bytes(b"PRIVATE-UNREFERENCED")
    output = tmp_path / "site"
    result = generate_site(archive, output, download_base_url="https://clvnkhr.github.io/tao2typ/")
    soup = BeautifulSoup((output / "index.html").read_text(), "html.parser")
    assert attribute(selected(soup, "a.pdf"), "href") == (
        "https://clvnkhr.github.io/tao2typ/pdfs/einstein-e-mc2.pdf"
    )
    assert attribute(selected(soup, "a.typst"), "href") == (
        "https://clvnkhr.github.io/tao2typ/typst/einstein-e-mc2.typ"
    )
    assert attribute(selected(soup, "a.ir"), "href") == (
        "https://clvnkhr.github.io/tao2typ/ir/einstein-e-mc2.json"
    )
    assert result["exported_files"] == 3
    assert (output / "pdfs" / "einstein-e-mc2.pdf").read_bytes() == b"PRIVATE-PDF"
    assert (output / "typst" / "einstein-e-mc2.typ").read_text() == "PRIVATE-TYPST"
    assert (output / "ir" / "einstein-e-mc2.json").read_bytes() == (
        archive / "ir" / "einstein-e-mc2.json"
    ).read_bytes()
    assert not (output / "state.json").exists()
    assert not (output / "queue.json").exists()
    assert not (output / "jobs").exists()
    assert not (output / "failures").exists()
    assert not (output / "pdfs" / "unreferenced.pdf").exists()


def test_public_export_keeps_typst_relative_images_and_ir_only_images(tmp_path: Path) -> None:
    archive = fixture_archive(tmp_path)
    (archive / "assets").mkdir()
    figure = "assets/figure space#.png"
    ir_only = "assets/comment-photo.jpg"
    (archive / figure).write_bytes(b"FIGURE")
    (archive / ir_only).write_bytes(b"COMMENT-PHOTO")
    (archive / "assets" / "unreferenced.png").write_bytes(b"PRIVATE-UNREFERENCED")
    (archive / "typst" / "einstein-e-mc2.typ").write_text(
        '#import "@preview/mitex:0.2.7": mi\n#image("../assets/figure space#.png", width: 90%)'
    )
    (archive / "ir" / "einstein-e-mc2.json").write_text(
        json.dumps(
            {
                "comments": [
                    {"replies": [{"body": [{"kind": "image", "attrs": {"asset_path": ir_only}}]}]}
                ]
            }
        )
    )
    output = tmp_path / "site"
    result = generate_site(archive, output, download_base_url="https://example.com/tao2typ/")
    assert result["exported_files"] == 5
    assert (output / "typst" / ".." / figure).read_bytes() == b"FIGURE"
    assert (output / ir_only).read_bytes() == b"COMMENT-PHOTO"
    assert not (output / "assets" / "unreferenced.png").exists()


@pytest.mark.parametrize("asset", ["../state.json", "assets/missing.png"])
def test_public_export_rejects_unsafe_or_missing_assets_before_copying(
    tmp_path: Path, asset: str
) -> None:
    archive = fixture_archive(tmp_path)
    (archive / "ir" / "einstein-e-mc2.json").write_text(
        json.dumps({"body": [{"kind": "image", "attrs": {"asset_path": asset}}]})
    )
    output = tmp_path / "site"
    with pytest.raises(ValueError, match="Missing or unsafe exported image asset"):
        generate_site(archive, output, download_base_url="https://example.com/tao2typ/")
    assert not output.exists()


def test_public_export_does_not_follow_artifact_symlinks(tmp_path: Path) -> None:
    archive = fixture_archive(tmp_path)
    source = archive / "pdfs" / "einstein-e-mc2.pdf"
    source.unlink()
    source.symlink_to(archive / "state.json")
    output = tmp_path / "site"
    result = generate_site(archive, output, download_base_url="https://example.com/tao2typ/")
    assert result["converted"] == 0
    assert not (output / "pdfs").exists()
    soup = BeautifulSoup((output / "index.html").read_text(), "html.parser")
    assert not soup.select("a.pdf")


def test_gzip_sources_keep_repository_links_and_export_native_downloads(tmp_path: Path) -> None:
    archive = fixture_archive(tmp_path)
    state = json.loads((archive / "state.json").read_text())
    originals = {}
    for kind in ("typst", "ir"):
        relative = state["posts"]["1"][kind]
        path = archive / relative
        originals[relative] = path.read_bytes()
        write_bytes(archive / (relative + ".gz"), originals[relative])
        path.unlink()
        state["posts"]["1"][kind] = relative + ".gz"
    (archive / "state.json").write_text(json.dumps(state))
    private = tmp_path / "private-site"
    generate_site(archive, private)
    soup = BeautifulSoup((private / "index.html").read_text(), "html.parser")
    assert attribute(selected(soup, "a.typst"), "href").endswith(".typ.gz")
    assert selected(soup, "a.typst").get_text() == ".typ.gz"
    public = tmp_path / "public-site"
    generate_site(archive, public, download_base_url="https://example.com/tao2typ")
    soup = BeautifulSoup((public / "index.html").read_text(), "html.parser")
    assert attribute(selected(soup, "a.typst"), "href").endswith(".typ")
    for relative, original in originals.items():
        assert (public / relative).read_bytes() == original
        assert not (public / (relative + ".gz")).exists()
