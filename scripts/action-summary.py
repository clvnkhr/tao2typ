"""Summarize the completed batch without changing its state or exit status."""

import json
import os
import sys
from pathlib import Path


def markdown(value: str) -> str:
    return value.replace("|", "\\|").replace("`", "\\`").replace("\n", " ")


def main() -> None:
    report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    converted, failed = report["converted"], report["failed"]
    results, errors = report["results"], report["errors"]
    if (
        any(type(count) is not int or count < 0 for count in (converted, failed))
        or not isinstance(results, list)
        or not isinstance(errors, list)
        or not report.get("finished_at")
    ):
        raise ValueError("Expected a finished batch report")
    remaining = report.get("remaining", max(report.get("due", 0) - len(results), 0))
    lines = [
        f"Batch: **{converted} converted · {failed} failed · {remaining} remaining**",
        "",
        "[Queue](https://clvnkhr.github.io/tao2typ/#queue)",
        "",
    ]
    if failed:
        print("::warning::Some posts remain queued for retry; see the batch summary.")
        lines += ["These posts remain queued for retry:", "", "| Post | Reason |", "| --- | --- |"]
        for result in results:
            if result.get("status") != "failed":
                continue
            ident = result.get("id", "?")
            url = result.get("url", "")
            reason = str(result.get("error", "Unknown error")).splitlines()[0]
            lines.append(f"| [Post {ident}]({url}) | {markdown(reason)} |")
        lines.append("")
    if errors:
        print("::error::A source or discovery check failed; see the batch summary.")
        lines += ["Source errors:", ""]
        for error in errors:
            stage = markdown(str(error.get("stage", "unknown")))
            reason = markdown(str(error.get("error", "Unknown error")).splitlines()[0])
            lines.append(f"- {stage}: {reason}")
        lines.append("")
    summary = "\n".join(lines)
    print(summary)
    if destination := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(destination).open("a", encoding="utf-8") as output:
            output.write(summary + "\n")


if __name__ == "__main__":
    main()
