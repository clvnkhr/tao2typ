# tao2typ

Convert [Terence Tao’s blog](https://terrytao.wordpress.com/) through a versioned
intermediate representation into readable Typst and PDF. This is a successor to
[tao2tex](https://github.com/clvnkhr/tao2tex), with parsing, rendering, fetching,
and scheduling kept independent.

```text
WordPress HTML + source metadata → structured JSON IR → Typst → PDF
                                  ↑                     ↑
                             parser.py              typst.py
```

The IR retains paragraphs, rich inline text, original LaTeX mathematics,
headings, links, named anchors, images, lists, tables, quotations, and threaded
comments. Maths are typeset through pinned `@preview/mitex:0.2.7`; they are not
screenshots. PDFs include the source link and publication date, serif text,
page numbers, and styled theorem quotations. Unknown HTML can produce explicit
IR warnings. Unsupported TeX or images fail visibly and retain the previous PDF.

## Run locally

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and
[Typst 0.15.1](https://github.com/typst/typst/releases/tag/v0.15.1), then:

```sh
uv sync --locked
uv run tao2typ convert 'https://terrytao.wordpress.com/2022/10/03/what-are-the-odds/' --stem odds
uv run tao2typ batch
```

For the first local batch, `bash scripts/first-batch.sh` also commits and pushes
the generated archive when it finishes. Progress is checkpointed after each
post in `archive/state.json`; the final command report is in
`.cache/first-batch.log`. Re-running resumes posts that still need work.

Convert saved HTML (source attribution is required):

```sh
uv run tao2typ convert example.html --url 'https://terrytao.wordpress.com/2022/10/03/what-are-the-odds/' --published-at '2022-10-03T00:00:00-07:00' --stem example
```

Local HTML conversion leaves remote images as linked placeholders. URL conversion
downloads image assets and follows all comment pagination links. Rerender saved
IR without fetching or reparsing HTML:

```sh
uv run tao2typ render archive/ir/2022-10-03-123.json --output archive/typst/2022-10-03-123.typ --pdf archive/pdfs/2022-10-03-123.pdf
```

Use the actual filenames listed in `archive/README.md`. To attempt an entire
initial archive in one batch, use the default `--max-posts 0`. To refresh the entire archive
regardless of schedule, add `--force`. Run the full first batch locally to avoid
using Actions minutes for the initial archive. Local batches have a five-hour soft
time limit and checkpoint after each post. The daily workflow is limited to 25
due posts or ten minutes, with a fifteen-minute job timeout; any remaining backlog
resumes on the next run. Use `--max-posts` or `--max-seconds` to adjust a local
batch. The converter waits one second between requests and
retries transient errors with backoff. `--interval` adjusts this locally.

## Daily updates

[Daily PDFs](.github/workflows/daily.yml) runs at **06:23 UTC every day**
(06:23 in UK winter, 07:23 in UK summer), and supports manual **Run workflow**.
Every run scans all WordPress post metadata, retaining the original publication
and modification timestamps; it fetches full HTML only for posts needing work.

| Trigger | Refresh policy |
| --- | --- |
| Previously unconverted post | Convert at the next batch with available capacity |
| New publication | Check at publication + 7 days, + 1 calendar month, + 1 year |
| Established post | Check on every subsequent annual birthday |
| New permalink in the homepage’s Recent Comments widget | Refresh its parent post and comments |
| Source modification timestamp changes | Refresh even if it is an old post |
| Parser, IR, or rendering code changes | Refresh outputs made with older converter code |
| Missing PDF or failed conversion | Remains due until a successful conversion |

Calendar arithmetic preserves the source publication offset; job/check timestamps
use UTC. A month ending before the original day
clamps to its last day; February 29 birthdays use February 28 in nonleap years,
then return to February 29 in leap years. Importing an older post performs one
initial check and places its next check on the original publication timeline;
it does not restart the week/month schedule from the import date. A delayed job
catches up in one refresh and skips milestones already covered by that check.

Recent comment events are keyed by their full permalink (including the comment
ID), queued durably, and acknowledged only after PDF compilation succeeds.
Repeated appearances of the same comment do not trigger another conversion.
The widget is a bounded signal: comments that appear and disappear between daily
polls can be missed. Birthday checks still refresh those posts.

Healthy due posts precede failed retries, and failed posts rotate by attempt
time. Timestamped job reports record all attempted IDs, reasons, errors, start
and finish times, conversion counts, and the remaining backlog. Failed jobs
still commit successful outputs and state; their GitHub Actions status remains
failed so errors are visible. Requests and individual compilations have timeouts.

Generated files are committed directly to this private repository:

```text
archive/
  state.json        # source dates, check dates, pending/seen comments, hashes
  jobs/*.json       # one timestamped report per batch
  ir/*.json         # versioned intermediate documents
  typst/*.typ       # escaped, independently compilable Typst source
  pdfs/*.pdf        # stable filenames: publication date + WordPress ID
  assets/*          # image assets addressed by URL + content hashes
  README.md         # chronological PDF index
```

An unchanged IR and rendered Typst reuse existing files, reducing binary history
growth. Images are re-fetched on each due refresh to detect replacements at the
same URL. Their content hashes preserve older Typst source reproducibility even
when a conversion fails. Git still retains older PDF revisions when content changes. The workflow
serializes archive jobs and rebases its commit over concurrent source edits.

The workflow needs `contents: write` and must be on the default branch. Repository
or organization policies may need to permit Actions and bot pushes. It uses the
normal `GITHUB_TOKEN`, with no custom secret. GitHub schedules can be delayed;
manual dispatch catches up. Public repositories’ schedules can be disabled after
60 days without activity; this repository is intended to be private.
See [GitHub’s scheduled workflow documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

## Development

Python dependencies are locked with uv. Ruff handles lint and formatting; ty
handles type checking. Tests cover scheduling boundaries, HTML/IR fidelity,
Typst compilation, discovery, comments, and durable batch failures.

```sh
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
```

Change HTML handling in `parser.py`, output styling in `typst.py`, source/network
behaviour in `source.py`, and refresh policy in `schedule.py`. A future output
format can consume saved IR without coupling itself to WordPress markup.

The software is MIT licensed; the source blog posts and comments retain their
authors’ rights. Keep source attribution. Tao asks readers to contact him before
redistributing a substantial fraction of the blog; see his
[copyright notice](https://terrytao.wordpress.com/about/). Keep a bulk archive private
unless permission to distribute it has been obtained.
