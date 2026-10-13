# Converter audit

Reviewed the parser, IR, Typst renderer, fetching, asset handling, scheduling,
state, CLI and public export. Compared live HTML and regenerated PDFs for
`what-should-we-tell-our-students` and `245b-notes-3-lp-spaces`, including all
reachable comment pages. The bulk conversion remains stopped.
Also checked the Navier-Stokes quantitative-bounds post for norm, script and
prime rendering.

## Fixed

- Tao's sibling `ul.children` reply groups now retain their parents. Standard
  nested lists, repeated pagination wrappers and orphan replies also work.
  The two live samples retain 116 and 144 comments across four and five levels.
- A parent's missing author, time or avatar no longer comes from a descendant
  reply. Links within a comment cannot replace its own permalink.
- Avatars are separate IR metadata and render beside the author. Missing avatars
  warn without losing comments; article diagrams remain required.
- Comment headers stay with the first body line, and every reply depth remains
  visibly indented.
- Centered prose and section titles keep inline maths. Displayed equations retain
  external labels with punctuation, decimal labels, embedded legacy labels,
  `\tag`, `\tag*`, and explicitly tagged alignment rows.
- Equation labels use the original source number at the right margin. Theorem,
  lemma, definition and exercise captions retain their original text and numbers;
  repeated source numbers are not silently changed.
- Percent-encoded internal fragments resolve to their source anchors.
- Supported legacy TeX aliases and numeric `\raisebox{...ex}` offsets compile;
  `ex` uses the actual font x-height. Original TeX remains unchanged in JSON IR.
- Paired absolute-value and norm fences use native Typst atoms, including fixed
  source sizes. Scripts attach to the whole fenced expression. Conditional,
  relation and evaluation bars retain their roles.
- Prime shorthand keeps primes and indices on their original base. Compiled
  regressions check TeX script boundaries before domains, mixed norm indices,
  Lorentz/Besov exponents, fractions, limits and nested scripts.
- Nested fence scopes include half-open intervals, floor/ceiling delimiters and
  arbitrary explicit left/right glyph pairs. Standard functions and binary
  operators keep inner absolute-value and norm pairs nested correctly.
- Failed WordPress image proxies and obsolete Wikimedia thumbnails can recover
  their explicitly encoded original image. Download limits and delays still apply.
- HTML error responses are rejected even when their URL ends in `.png`.
- Missing Typst or IR files trigger repair even when a PDF still exists. Fetching
  and asset-pipeline fixes now invalidate old converter fingerprints too.
- Failed or interrupted output promotion restores the prior IR, Typst and PDF
  together. Tests inject failures both before and after file replacement.

## Verification and limits

Regression tests cover the live markup shapes, numbering, internal references,
avatar downloads, pagination, page breaks, source recovery and rollback. Rendered
sample pages were inspected for reply indentation, avatars, equations and theorem
captions; page thumbnails and text bounds were checked for clipping. Raw LaTeX
in the rebuilt samples matches their previous saved IR.

Only these three samples were built locally. Other existing PDFs retain their
older rendering until a queued refresh; the daily limit stays at ten attempts.

Genuinely unavailable source images and malformed source TeX remain explicit
failures with diagnostics. An abrupt process kill between three file replacements
is not a fully atomic transaction; a later retry repairs the output set. No audit
can establish that every future WordPress markup or TeX construct is supported.
Plain bar pairing is heuristic; ambiguous notation and arbitrary macro argument
syntax remain limits. Explicit directional fences avoid that ambiguity.
