# FAQ

## Who made this?

This site is by [Calvin Khor](https://clvnkhr.github.io/), **not Terence Tao**.
It is an unofficial project. The original posts and comments are on
[Tao’s blog](https://terrytao.wordpress.com/).

## Why do downloads ask for a password?

Downloads require a password to discourage mass distribution.

## How are posts downloaded and queued?

A daily GitHub Actions job checks the blog for new or edited posts and looks at
Recent Comments for discussions that need refreshing. It downloads the article
and its comment pages, and keeps compressed copies of the input it parsed.

Posts are checked a week and a calendar month after publication, then on their
annual birthdays. Older posts join that same timeline. These dates put a post
in the queue; a busy queue can delay its check.

There is a limit of **20 post attempts per UTC day**, shared by scheduled and
manual runs. Failed attempts count too. Work left after the daily limit or the
job’s time limit stays in the [queue](./#queue). A queued post can already have a
PDF: it may just be due for an update.

## What does the parser do?

The Python parser reads the HTML and builds an intermediate representation:
paragraphs, headings, lists, figures, maths, and comments with their replies.
Original LaTeX formulas and equation numbers are kept in this representation.
Each post’s JSON download contains it.

Keeping this step separate from typesetting means the PDF layout can change
without downloading the blog again or rewriting the HTML parser.

## Is there a Typst template?

The layout is written in
[`src/tao2typ/typst.py`](https://github.com/clvnkhr/tao2typ/blob/main/src/tao2typ/typst.py).
It generates a complete Typst document for each post, rather than using a
separate template package. You can download that source from the `.typ.gz` link.

The documents use A4 pages, 12-point Libertinus Serif text, justified paragraphs
and first-line indents. The renderer lays out headings, figures, numbered
statements and threaded comments. MiTeX converts the LaTeX maths; equate helps
with aligned displays. Typst then compiles the result to PDF.

## What do the coloured dots mean?

- **Green:** a PDF was produced without a recorded source issue.
- **Amber:** the PDF needed a fallback, such as a missing-image placeholder or a
  note preserving a broken formula from the source.
- **Red:** the PDF is missing, or its latest conversion failed.

The conversion is imperfect. If something looks wrong, compare it with the
original post.
