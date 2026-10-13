// Generated from tao2typ IR schema 1.
// Original article: https://example.org/tao2typ/converter-demonstration/
#import "@preview/mitex:0.2.7": mi, mitex
#set document(title: "Converter demonstration", author: "tao2typ fixture")
#set page(paper: "a4", margin: (x: 24mm, y: 23mm), numbering: "1", number-align: center)
#set text(font: ("Libertinus Serif", "New Computer Modern"), size: 11pt, lang: "en")
#set par(justify: true, leading: 0.65em, spacing: 0.9em)
#set heading(numbering: none)
#show heading.where(level: 1): set text(size: 15pt, weight: "semibold")
#show heading.where(level: 2): set text(size: 12pt, weight: "semibold")
#show link: set text(fill: rgb("275d8c"))
#set list(indent: 1.2em, body-indent: 0.6em, spacing: 0.6em)
#set enum(indent: 1.2em, body-indent: 0.6em, spacing: 0.6em)
#set math.equation(numbering: none)
#show raw.where(block: true): it => block(
  width: 100%, breakable: true, fill: rgb("f5f6f8"), inset: 9pt, radius: 3pt,
)[#text(size: 9pt)[#it]]

#text(size: 9pt, fill: rgb("596477"))[#text("tao2typ · Converter demonstration")]
#v(5pt)
#text(size: 23pt, weight: "semibold")[#text("Converter demonstration")]
#v(7pt)
#text(size: 10pt)[#text("tao2typ fixture · 13 October 2026")]
#v(3pt)
#text(size: 8pt)[#link("https://example.org/tao2typ/converter-demonstration/")[#text("https://example.org/tao2typ/converter-demonstration/")]]
#v(9pt)
#line(length: 100%, stroke: 0.7pt + rgb("d9dfe8"))
#v(10pt)

#text("A self-authored fixture demonstrating mathematics, structure, assets, and comments.")

#heading(level: 1)[#text("Mathematics and structure")]

#text("Let ")#mi("f(x) = e^{-x^2}")#text(". Its Gaussian integral is")

#align(center)[#metadata(none) <tao-c7c5f1441e059892c19e>#grid(columns: (1fr, auto), align: (center, right + horizon), column-gutter: 10pt, [#mitex("\\int_{-\\infty}^{\\infty} e^{-x^2}\\,dx = \\sqrt{\\pi}")], [#text("(1)")])]

#text("Equation ")#link(<tao-c7c5f1441e059892c19e>)[#text("(1)")]#text(" retains its original number and link. Unicode prose also survives: α, β, and the relation x ≥ 0.")

#block(width: 100%, breakable: true, inset: (left: 12pt, right: 10pt, top: 5pt, bottom: 5pt), stroke: (left: 1.5pt + rgb("98a4b6")))[
#strong[#text("Proposition 1 (A simple bound).")]#text(" For every real ")#mi("x")#text(", ")#mi("0 < e^{-x^2} \\leq 1")#text(".")

#emph[#text("Proof.")]#text(" The exponent is nonpositive, and the exponential is positive and increasing.")
]

#heading(level: 1)[#text("Lists, tables, and images")]

#enum(start: 1, 
[#text("Parse HTML into a versioned intermediate representation.")

#list(
[#text("Keep original LaTeX and source links.")],
[#text("Keep dates and threaded comments.")]
)],
[#text("Render the same representation into readable Typst.")],
[#text("Compile to a PDF and record the outcome.")]
)

#table(columns: 2, inset: 7pt, stroke: 0.5pt + rgb("d9dfe8"),
table.cell(colspan: 1, rowspan: 1)[#strong[#text("Input")]],
table.cell(colspan: 1, rowspan: 1)[#strong[#text("Preserved information")]],
table.cell(colspan: 1, rowspan: 1)[#text("Mathematics")],
table.cell(colspan: 1, rowspan: 1)[#text("Original LaTeX, display mode, equation number")],
table.cell(colspan: 1, rowspan: 1)[#text("Images")],
table.cell(colspan: 1, rowspan: 1)[#text("Source URL, alternative text, local asset mapping")],
table.cell(colspan: 1, rowspan: 1)[#text("Comments")],
table.cell(colspan: 1, rowspan: 1)[#text("Author, timestamp, permalink, reply structure")]
)

#align(center)[
#image("demo-diagram.svg", width: 90%, alt: "HTML to intermediate representation to Typst to PDF")

#align(center)[#text(size: 9pt, fill: rgb("596477"))[#text("A small local asset accompanies the generated document.")]]
]

#heading(level: 1)[#text("Literal text remains literal")]

#text("Literal text: ")#raw("#panic(\"example\")")#text(", [brackets], $currency, and ")#raw("\\frac12")#text(". Formatting: ")#strong[#text("strong")]#text(", ")#emph[#text("emphasis")]#text(", ")#strike[#text("deleted")]#text(", H")#sub[#text("2")]#text("O, and x")#super[#text("2")]#text(".")

#raw("post = parse_post(html, source_url)\nir_json = post.to_dict()\ntypst_source = render_post(post, assets)\n", block: true, lang: "python")

#pagebreak(weak: true)
#heading(level: 1)[#text("Comments (2)")]

#block(width: 100%, breakable: true, above: 10pt, below: 6pt)[
#link("https://example.org/tao2typ/converter-demonstration/#comment-demo-1")[#strong[#text("Example reader")]]#text(size: 9pt, fill: rgb("596477"), " · 13 October 2026")

#text("The derivative ")#mi("f'(x) = -2xe^{-x^2}")#text(" should be preserved too.")
]

#pad(left: 10pt)[
#block(width: 100%, breakable: true, above: 10pt, below: 6pt)[
#link("https://example.org/tao2typ/converter-demonstration/#comment-demo-2")[#strong[#text("Example author")]]#text(size: 9pt, fill: rgb("596477"), " · 13 October 2026")

#text("This reply stays beneath its parent.")
]
]
