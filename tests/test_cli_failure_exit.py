"""Individual queued failures are distinguishable from fatal batch failures."""

import sys
from pathlib import Path

import pytest

from tao2typ import cli


@pytest.mark.parametrize(
    ("allow", "failed", "errors", "exit_code"),
    [(False, 1, [], 1), (True, 1, [], 0), (True, 0, ["Discovery failed"], 1), (False, 0, [], 0)],
)
def test_batch_failure_exit_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow: bool,
    failed: int,
    errors: list[str],
    exit_code: int,
) -> None:
    args = ["tao2typ", "batch", "--output", str(tmp_path / "archive")]
    if allow:
        args.append("--allow-post-failures")
    monkeypatch.setattr(sys, "argv", args)
    monkeypatch.setattr(
        cli, "run_batch", lambda *args, **kwargs: {"failed": failed, "errors": errors}
    )
    assert cli.main() == exit_code
