"""Permalink-based names and offline, lossless migration of existing files."""

from pathlib import Path

import pytest

from tao2typ import migrate
from tao2typ.naming import artifact_stem, artifact_stems
from tao2typ.state import ArchiveState, PostState
from tao2typ.storage import read_bytes, write_bytes


def post(identifier: int, slug: str, title: str = "A title") -> PostState:
    return PostState(
        identifier,
        f"https://terrytao.wordpress.com/2026/10/08/{slug}/",
        title,
        "2026-10-08T12:00:00+00:00",
        "2026-10-08T12:00:00+00:00",
    )


def test_name_uses_permalink_and_survives_title_edits() -> None:
    url = "https://terrytao.wordpress.com/2026/10/08/what-should-we-tell-our-students/"
    assert artifact_stem("A changed title", url, 18384) == "what-should-we-tell-our-students"
    assert artifact_stem("Title", url + "comment-page-2/#comment-42", 18384) == (
        "what-should-we-tell-our-students"
    )


@pytest.mark.parametrize(
    ("title", "url", "expected"),
    [
        ("", "https://example.com/Caf%C3%A9-%CE%B1-%E2%88%9E/", "cafe-alpha-infinity"),
        ("Einstein’s E=mc²", "", "einsteins-e-equals-mc2"),
        ("A/B: C+D? <proof>|*", "", "a-b-c-plus-d-proof"),
        ("CON", "", "post-con"),
        ("ß œ Ø", "", "ss-oe-o"),
        ("☃", "", "post-42"),
    ],
)
def test_names_are_lower_ascii_kebabcase(title: str, url: str, expected: str) -> None:
    assert artifact_stem(title, url, 42) == expected


def test_collisions_use_id_only_where_needed_and_do_not_steal_other_slugs() -> None:
    posts = [post(2, "same"), post(1, "same"), post(3, "same-2"), post(4, "unique")]
    expected = {1: "same", 2: "same-2-2", 3: "same-2", 4: "unique"}
    assert artifact_stems(posts) == expected
    assert artifact_stems(reversed(posts)) == expected
    assert artifact_stems([post(1, "same"), post(8, "same")])[1] == "same"


def test_long_names_keep_space_for_collision_suffix() -> None:
    stems = artifact_stems([post(1, "a" * 300), post(1234, "a" * 300)])
    assert len(stems[1]) == len(stems[1234]) == 180
    assert stems[1234].endswith("-1234")


def test_rename_preserves_gzip_suffix_and_compressed_bytes(tmp_path: Path) -> None:
    record = post(42, "better-name")
    record.typst = "typst/old.source.typ.gz"
    write_bytes(tmp_path / record.typst, b"exact Typst source")
    original_gzip = (tmp_path / record.typst).read_bytes()
    ArchiveState(posts={"42": record}).save(tmp_path / "state.json")
    migrate.migrate_existing_archive(tmp_path)
    result = ArchiveState.load(tmp_path / "state.json").posts["42"]
    assert result.typst == "typst/better-name.typ.gz"
    assert (tmp_path / result.typst).read_bytes() == original_gzip
    assert read_bytes(tmp_path / result.typst) == b"exact Typst source"


def populated_archive(output: Path) -> tuple[ArchiveState, dict[str, bytes]]:
    original = post(42, "better-post-name")
    contents = {
        "pdfs/2026-10-08-42.pdf": b"%PDF-original",
        "typst/2026-10-08-42.typ": b'#image("../assets/original.png")',
        "ir/2026-10-08-42.json": b'{"title":"Original"}',
        "assets/original.png": b"image-data",
        "jobs/old-report.json": b'{"pdf":"pdfs/2026-10-08-42.pdf"}',
    }
    for relative, data in contents.items():
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    original.pdf = "pdfs/2026-10-08-42.pdf"
    original.typst = "typst/2026-10-08-42.typ"
    original.ir = "ir/2026-10-08-42.json"
    state = ArchiveState(posts={"42": original})
    state.save(output / "state.json")
    return state, contents


def test_migration_is_lossless_offline_and_idempotent(tmp_path: Path) -> None:
    _, contents = populated_archive(tmp_path)
    assert migrate.migrate_existing_archive(tmp_path) == {"renamed": 3, "missing": 0, "posts": 1}
    state = ArchiveState.load(tmp_path / "state.json")
    for attribute in ("pdf", "typst", "ir"):
        relative = getattr(state.posts["42"], attribute)
        assert relative is not None and "better-post-name" in relative
    for relative, data in contents.items():
        if relative.startswith(("pdfs/", "typst/", "ir/")):
            assert not (tmp_path / relative).exists()
            target = Path(relative).with_name("better-post-name" + Path(relative).suffix)
            assert (tmp_path / target).read_bytes() == data
        else:
            assert (tmp_path / relative).read_bytes() == data
    assert migrate.migrate_existing_archive(tmp_path) == {"renamed": 0, "missing": 0, "posts": 0}


def test_migration_missing_files_keeps_original_repair_path(tmp_path: Path) -> None:
    _, _ = populated_archive(tmp_path)
    (tmp_path / "pdfs/2026-10-08-42.pdf").unlink()
    assert migrate.migrate_existing_archive(tmp_path) == {"renamed": 2, "missing": 1, "posts": 1}
    state = ArchiveState.load(tmp_path / "state.json")
    assert state.posts["42"].pdf == "pdfs/2026-10-08-42.pdf"


def test_migration_preserves_quota_queue_and_diagnostic_source(tmp_path: Path) -> None:
    state, _ = populated_archive(tmp_path)
    state.daily_attempts = {"2026-10-13": 10}
    state.posts["42"].last_error = "unsupported math"
    state.posts["42"].last_checked_at = "2026-10-13T10:00:00+00:00"
    state.save(tmp_path / "state.json")
    queue = b'{"posts":[{"id":42,"reasons":["converter-changed"]}]}'
    (tmp_path / "queue.json").write_bytes(queue)
    failures = tmp_path / "failures"
    failures.mkdir()
    (failures / "2026-10-08-42.typ").write_bytes(b"diagnostic source")
    assert migrate.migrate_existing_archive(tmp_path) == {"renamed": 4, "missing": 0, "posts": 1}
    result = ArchiveState.load(tmp_path / "state.json")
    assert result.daily_attempts == state.daily_attempts
    assert result.posts["42"].last_error == state.posts["42"].last_error
    assert result.posts["42"].last_checked_at == state.posts["42"].last_checked_at
    assert (tmp_path / "queue.json").read_bytes() == queue
    assert (failures / "better-post-name.typ").read_bytes() == b"diagnostic source"
    assert migrate.migrate_existing_archive(tmp_path) == {"renamed": 0, "missing": 0, "posts": 0}


def test_migration_recovers_target_after_interrupted_file_move(tmp_path: Path) -> None:
    populated_archive(tmp_path)
    (tmp_path / "pdfs/2026-10-08-42.pdf").rename(tmp_path / "pdfs/better-post-name.pdf")
    assert migrate.migrate_existing_archive(tmp_path) == {"renamed": 2, "missing": 0, "posts": 1}
    assert ArchiveState.load(tmp_path / "state.json").posts["42"].pdf == "pdfs/better-post-name.pdf"


def test_migration_refuses_conflicts_before_changing_anything(tmp_path: Path) -> None:
    populated_archive(tmp_path)
    original_state = (tmp_path / "state.json").read_bytes()
    (tmp_path / "typst/better-post-name.typ").write_bytes(b"unrelated source")
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        migrate.migrate_existing_archive(tmp_path)
    assert (tmp_path / "pdfs/2026-10-08-42.pdf").exists()
    assert (tmp_path / "state.json").read_bytes() == original_state


def test_migration_rolls_back_on_move_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    populated_archive(tmp_path)
    original_state = (tmp_path / "state.json").read_bytes()
    real_move = migrate._move_without_overwrite
    calls = 0

    def fail_second(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated disk failure")
        real_move(source, destination)

    monkeypatch.setattr(migrate, "_move_without_overwrite", fail_second)
    with pytest.raises(OSError, match="simulated disk failure"):
        migrate.migrate_existing_archive(tmp_path)
    assert (tmp_path / "pdfs/2026-10-08-42.pdf").exists()
    assert not (tmp_path / "pdfs/better-post-name.pdf").exists()
    assert (tmp_path / "state.json").read_bytes() == original_state


def test_migration_rejects_paths_outside_archive(tmp_path: Path) -> None:
    original = post(42, "name")
    original.pdf = "../outside.pdf"
    ArchiveState(posts={"42": original}).save(tmp_path / "state.json")
    with pytest.raises(ValueError, match="escapes the archive"):
        migrate.migrate_existing_archive(tmp_path)
