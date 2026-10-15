"""The production runner passes bounded daily and elapsed-time budgets."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("force", [False, True])
def test_daily_runner_limits_attempts_and_keeps_soft_time_budget(
    tmp_path: Path, force: bool
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    runner = scripts / "daily-batch.sh"
    shutil.copyfile(Path(__file__).parents[1] / "scripts/daily-batch.sh", runner)
    (tmp_path / "archive").mkdir()
    (tmp_path / "archive/state.json").write_text("{}")
    binary = tmp_path / "bin"
    binary.mkdir()
    uv = binary / "uv"
    uv.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\0" "$@" >> "$TEST_DAILY_CALLS"\n'
        'printf "\\n" >> "$TEST_DAILY_CALLS"\nprintf "{}\\n"\n'
    )
    uv.chmod(0o755)
    calls = tmp_path / "calls"
    subprocess.run(
        ["bash", str(runner)],
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "PATH": str(binary) + os.pathsep + os.environ.get("PATH", ""),
            "RUNNER_TEMP": str(tmp_path / "runner"),
            "TEST_DAILY_CALLS": str(calls),
            "FORCE": str(force).lower(),
        },
    )
    invocations = [
        line.rstrip(b"\0").decode().split("\0") for line in calls.read_bytes().splitlines()
    ]
    expected = [
        "run",
        "--frozen",
        "--no-sync",
        "tao2typ",
        "batch",
        "--output",
        "archive",
        "--max-posts",
        "20",
        "--daily-limit",
        "20",
        "--max-seconds",
        "600",
        "--allow-post-failures",
    ]
    if force:
        expected.append("--force")
    assert invocations[0] == expected
    assert invocations[1][:5] == [
        "run",
        "--frozen",
        "--no-sync",
        "python",
        "scripts/action-summary.py",
    ]
