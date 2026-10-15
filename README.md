# tao2typ

[Browse posts](https://clvnkhr.github.io/tao2typ/)

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
comments. Maths are typeset through pinned `@preview/mitex:0.2.7`. PDFs include
the source link and publication date, serif text,
page numbers, and styled theorem quotations. Unknown HTML can produce explicit
IR warnings. Unavailable images become visible placeholders linked to the original
image. Reviewed malformed source formulas retain their exact TeX with a source-error
note. Other unsupported TeX fails visibly and retains the previous PDF.

Each PDF and Typst source has a small `tao2typ` conversion-date line near the
top. Conversion timestamps are retained in the IR and shown in the catalogue;
an unchanged refresh keeps the existing date and avoids recompilation. The
catalogue identifies this as an unofficial personal site.

Replies follow the site's nesting, including its older sibling-list markup.
Commenter avatars appear beside author metadata and are optional: an unavailable
avatar is recorded as a warning without discarding the discussion. Comments follow
the article on the current page when space permits, without a forced page break.
Equations
retain their source labels, including `\tag` and `\tag*`; theorem numbers and
reference text are preserved rather than assigned new counters.

Paired `|...|` and `\|...\|` render as native Typst `abs` and `norm` atoms,
so indices attach to the whole expression. TeX script arguments retain their
boundaries before following parentheses, and prime shorthand retains its
superscript attachment. These conversions affect the rendering copy only;
the IR and captured HTML preserve the original source.

Fence recognition tracks nested delimiters, including half-open intervals.
Plain bars still require a heuristic: ambiguous sequences and arbitrary TeX
macro argument syntax cannot be resolved universally by this lexer. Explicit
`\left`/`\right` or `\lvert`/`\rvert` directions make the intended pairing clear.

Consecutive display rows align at top-level relations; leading `+`/`-`
continuations can share a binary-operator alignment. Prose and structural block
boundaries end the group. Pinned `@preview/equate:0.3.3` shares the alignment
while retaining each original equation label, including sublabels such as
`(3a)` and `(3b)`. The source TeX in the IR is unchanged.

## Run locally

Body text uses a 12pt serif font and a 1.5em indent for consecutive paragraphs.
Line, paragraph and block spacing use Typst's defaults. Justification can adjust tracking by
up to ±0.01em. Titles and headings use optimized line breaks; comments use
ragged-right text without paragraph indents. Punctuation overhang, kerning,
standard ligatures and body hyphenation use Typst's defaults.

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
On macOS the script prevents idle system sleep for the duration of the batch.

Convert saved HTML (source attribution is required):

```sh
uv run tao2typ convert example.html --url 'https://terrytao.wordpress.com/2022/10/03/what-are-the-odds/' --published-at '2022-10-03T00:00:00-07:00' --stem example
```

Local HTML conversion leaves remote images as linked placeholders. URL conversion
downloads image assets and follows all comment pagination links. Rerender saved
IR without fetching or reparsing HTML:

```sh
uv run tao2typ render archive/ir/what-are-the-odds.json.gz --output archive/typst/what-are-the-odds.typ.gz --pdf archive/pdfs/what-are-the-odds.pdf
```

Regenerate the whole local collection using the current converter:

```sh
uv run --frozen tao2typ regenerate --output archive --workers 2
```

Regeneration reads saved IR for converted posts and verified complete raw captures
for failed posts. It uses cached image assets, with linked placeholders for
unavailable images. Each successful regeneration recompiles the PDF and updates its
conversion date. Existing source check dates, pending comment events and daily
Actions quotas are preserved. If an initial fetch succeeded but its conversion
failed, a successful offline repair records that verified capture's original
completion date and schedules the next check from the post's publication date.
To reconcile already-repaired posts without fetching or recompiling, run
`uv run --frozen tao2typ reconcile-checks --output archive`.

Progress is checkpointed in `archive/jobs/`. Repeating the command resumes the
newest unfinished regeneration job. Use `--resume JOB` to choose a particular job,
`--max-posts N` for a bounded inspection run, or `--ids ID [ID …]` to select posts.
On macOS, prefix the command with `caffeinate -i` to prevent idle sleep. The local
preview described below shows progress while the job runs.

Use the actual filenames listed in `archive/README.md`. To attempt an entire
initial archive in one batch, use the default `--max-posts 0`. To refresh the entire archive
regardless of schedule, add `--force`. Run the full first batch locally to avoid
using Actions minutes for the initial archive. Local batches have a five-hour soft
time limit and checkpoint after each post. The daily workflow is limited to twenty
PDF attempts per UTC day or ten minutes, with a fifteen-minute job timeout; any remaining backlog
resumes on the next run. Use `--max-posts` or `--max-seconds` to adjust a local
batch. The converter waits one second between requests and
retries transient errors with backoff. `--interval` adjusts this locally.
Successful image bytes are reused within a job in a bounded 64 MiB cache;
the next job fetches them again. Failed downloads are not cached.
Job reports include response codes by host, rate-limit responses, retries and
wait times, transport failures, and image-cache hits.

The daily quota survives manual reruns and counts failed attempts too. Remaining
posts stay in `archive/queue.json`; the next day receives a fresh budget. The first
local batch has no daily quota and does not consume the Actions allowance.
The site's [Queue view](https://clvnkhr.github.io/tao2typ/#queue) shows the saved
processing order, waiting/retry status, and last checkpoint time. Search works
within the queue; posts awaiting a refresh can still link to their existing PDF.

## Daily updates

[Daily PDFs](.github/workflows/daily.yml) runs at **06:23 UTC every day**
(06:23 in UK winter, 07:23 in UK summer), and supports manual **Run workflow**.
Every run scans all WordPress post metadata, retaining the original publication
and modification timestamps; it fetches full HTML only for posts needing work.
If WordPress blocks a runner's HTML requests with HTTP 403, the converter uses
its official public API for rendered content and the complete threaded comments.
It checks comment totals and pagination before accepting the capture. The Recent
Comments signal has the same fallback, using stable comment permalinks.
The **smoke_only** workflow option tests source access and the catalogue without
making PDFs or consuming the daily quota. Individual failed posts remain queued
and appear in the job summary; source-wide errors and failed checks still fail
the workflow.

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
Birthday checks become due at source-date midnight, so publication later in the
day does not defer its check until the next day’s scan.

Recent comment events are keyed by their full permalink (including the comment
ID), queued durably, and acknowledged only after PDF compilation succeeds.
Repeated appearances of the same comment do not trigger another conversion.
The widget is a bounded signal: comments that appear and disappear between daily
polls can be missed. Birthday checks still refresh those posts.

Healthy due posts precede failed retries, and failed posts rotate by attempt
time. Timestamped job reports record all attempted IDs, reasons, errors, start
and finish times, conversion counts, and the remaining backlog. Individual failures
appear in the job summary and remain queued; source-wide errors and failed checks
fail the workflow. Completed outputs and state are committed after partial failure.
Requests and individual compilations have timeouts.

Generated files are committed directly to this repository:

```text
archive/
  state.json        # source dates, check dates, pending/seen comments, hashes
  queue.json        # due posts awaiting successful conversion
  jobs/*.json.gz    # completed timestamped reports; active reports stay plain
  ir/*.json.gz      # versioned intermediate documents
  typst/*.typ.gz    # escaped Typst source; decompress to compile independently
  pdfs/*.pdf        # stable lower kebab case filenames from post permalinks
  assets/*          # image assets addressed by URL + content hashes
  raw/pages/*.html.gz # exact decoded HTML parser input, addressed by SHA-256
  raw/api/*.json.gz  # exact API responses when HTML requests are blocked
  raw/<post>/<job>/manifest.json.gz # pages, URLs, fetch times, source dates
  failures/*        # diagnostic IR/Typst for failed compilations
  README.md         # chronological PDF index
```

PDFs, `.typ` sources and IR JSON share readable lower kebab case names derived
from the original post permalink, such as `what-should-we-tell-our-students.pdf`.
Post IDs are appended only to resolve collisions. `uv run tao2typ migrate-names`
renames existing outputs without downloading or recompiling them.

Every fetch saves its HTML before parsing, including all comment pages. Each
attempt has a timestamped manifest; unchanged HTML shares a content-addressed
file. A failed fetch retains any pages already received, and a parsing or
compilation failure retains its complete source capture. IR records the manifest
used to create it. Refreshing raw provenance alone does not recompile an unchanged
PDF. Older conversions are backfilled as their queued refreshes run.
API captures also retain every exact JSON response, its source URL, fetch time,
byte count and checksum, alongside the HTML passed to the parser. Offline replay
verifies both the retained JSON and the parser input.

Reviewed formulas already broken in original comments keep their exact TeX with
a small source-error note. The registry matches the post, comment and full source
formula; corrected formulas and identical text elsewhere are unaffected. A valid
source-rendered image preserves the unsupported `centerline` formula's layout.
Unavailable images use a visible placeholder linking to the original image URL;
the retained IR and job report record the download failure. Missing images do not
prevent conversion of the article or its comments.

Retained HTML, manifests, IR, Typst, failure diagnostics and completed reports
use deterministic gzip. PDF bytes and conversion dates are unchanged. Native
image formats and small working indexes remain directly usable. The parser and
renderer accept both legacy plain files and gzip files. After a batch has stopped,
compress existing artifacts with `uv run tao2typ compress --output archive`;
the migration verifies raw-page checksums and compressed bytes before relinking
records and removing the redundant plain copies.

Reparse a complete capture offline, checking its page checksums first:

```sh
uv run tao2typ parse-raw archive/raw/<post>/<job>/manifest.json.gz --output replay.ir.json
```

Raw HTML is kept in the repository; public download exports include PDF, Typst,
IR and referenced images. Titles retain the source formatting: plain `L^p` stays
plain when that is how the site presents it.

## Website

The catalogue is hosted at [clvnkhr.github.io/tao2typ/](https://clvnkhr.github.io/tao2typ/).
Original WordPress tags and categories are stored separately, including category
parent IDs. Filters preserve these names and category hierarchies. The page
contains post metadata and links to PDFs and `.typ` sources. With a public
repository, the default GitHub file links are accessible without signing in.
Default builds contain metadata and GitHub file links. Optional download exports use
`--download-base-url` to export existing PDFs, `.typ` sources, IR JSON and only
their referenced image assets beside the page. Relative Typst image paths are
preserved. Queue membership, readable refresh reasons and brief failure reasons
appear in the page; full state, job reports, diagnostic files and caches are excluded. Public
release exports decompress Typst and IR to ordinary `.typ` and `.json` files.

Each post has a tiny status dot: green for a clean PDF, amber for source notes,
original-formula image fallbacks or missing-image placeholders, and red for a
missing PDF or current failure; optional avatars do not affect its colour.

Build or refresh the site without generating PDFs:

```sh
uv run tao2typ sync-metadata
uv run tao2typ site --output docs
bash scripts/publish-catalogue.sh /path/to/clvnkhr.github.io
```

Watch a running local batch with `uv run tao2typ serve`, then open
[the local queue](http://127.0.0.1:8765/#queue). It refreshes every 15 seconds
when the checkpoint changes, preserving search and filters. PDF and source links
open the local files immediately. The preview reads checkpoints without changing
batch state or generating PDFs; public Pages remains a published snapshot.
Use the **Failures only** checkbox in [the queue](http://127.0.0.1:8765/#queue)
to filter posts with an unresolved error, including failed refreshes with an older
PDF. Expand a failure for its reason and last attempt. The local preview also
shows the complete error and links to available failed Typst, IR and captured
HTML. Open errors survive automatic refreshes. Captured HTML is served as plain
text; these diagnostic files are never included in public exports.

Only five allowlisted files are published under the personal site's
`public/tao2typ/` directory: `index.html`, `faq.html`, `style.css`, `app.js` and `.nojekyll`. The existing
website's build copies that directory to GitHub Pages.

The FAQ's text lives in **`web/faq.md`**, an ordinary Markdown file. Edit that file,
not the generated `docs/faq.html`. Pushing a change to it automatically publishes
the catalogue without running PDF conversions or using the daily PDF allowance.
The FAQ also has an **Edit this page** link to that file on GitHub.
For a local preview, save the Markdown and reload `http://127.0.0.1:8765/faq.html`.
The catalogue and FAQ follow the system's light or dark colour preference.

Prepare a public release without fetching or converting posts:

```sh
uv run tao2typ site --output /private/tmp/tao2typ-release --download-base-url https://clvnkhr.github.io/tao2typ/
```

Preparing this directory locally does not publish it or change repository visibility.
When ready to release, the explicit public publish flag rebuilds `docs/` and also
copies the exported download folders:

```sh
bash scripts/publish-catalogue.sh /path/to/clvnkhr.github.io --public-downloads
```

Daily conversion also refreshes `docs/`. Automatic cross-repository publication
uses `PAGES_DEPLOY_KEY`, a write-enabled SSH deploy key scoped to
**clvnkhr.github.io only**. An optional `PAGES_PUBLISH_TOKEN` with Contents
read/write permission for that repository remains supported. Without either,
use the local publish command above. The daily twenty-attempt PDF budget still applies.
Set the `PAGES_PUBLIC_DOWNLOADS` repository variable to `true` to also host PDFs,
sources and required assets on the personal site. This is optional when the
catalogue links directly to files in the public repository.

An unchanged IR and rendered Typst reuse existing files, reducing binary history
growth. Images are re-fetched on each due refresh to detect replacements at the
same URL. Their content hashes preserve older Typst source reproducibility even
when a conversion fails. Git still retains older PDF revisions when content changes. The workflow
serializes archive jobs and rebases its commit over concurrent source edits.

The workflow needs `contents: write` and must be on the default branch. Repository
or organization policies may need to permit Actions and bot pushes. It uses the
normal `GITHUB_TOKEN` for archive commits; the separate site publisher uses the
encrypted Actions secret described above. Only the secret reference appears in
the workflow, and its private key is not stored in Git. GitHub schedules can be delayed;
manual dispatch catches up. Public repositories’ schedules can be disabled after
60 days without activity.
See [GitHub’s scheduled workflow documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

## Development

Python dependencies are locked with uv. Ruff handles lint and formatting; ty
handles type checking. Tests cover scheduling boundaries, HTML/IR fidelity,
Typst compilation, discovery, comments, and durable batch failures. Install Poppler
locally to run PDF text and geometry checks. Check and normal Daily workflows
install `poppler-utils`; the source-only smoke check skips PDF tooling and compilation.

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
