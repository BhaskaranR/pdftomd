# Symbol fonts: Greek, check marks, arrows, bullets

Found on Firecrawl's public test PDFs (2026-10-26). Text in symbol fonts comes out as the wrong
characters: Greek `αβγδ` → `abgd`, ZapfDingbats `✓✔` → `34`, angle brackets/arrow → `Ææ fi`.
Word-made PDFs put bullets and checkboxes in Unicode's private-use area (`U+F0B7`, Wingdings
`U+F06F` ×230 in one file) — unreadable glyphs. This is open-work item 3 (Wingdings arrows).

## Cause
pdfminer reads a Symbol/ZapfDingbats font without its own map (built-in encoding) through the
ordinary Latin map, so code 0x61 reads `a` instead of `α`. Word's symbol fonts map to F000+code.

## Fix (port of pdf-inspector, MIT: base14.rs, fonts.rs)
- When `pdf2md.py` opens a PDF, wrap that document's font loader (`pdf.rsrcmgr.get_font`, both open
  sites: `convert()` and `prepare()`); no global patch.
- `Symbol` / `ZapfDingbats` (names: symbol, symbolmt, symbolitc; zapfdingbats, dingbats, itczapfdingbats,
  zapfdingbatsitc; subset prefix stripped): use the built-in map unless `/Encoding` names another
  encoding; `/Differences` still apply on top; the name `SymbolEncoding`/`ZapfDingbatsEncoding` keeps it.
  A font's ToUnicode map still wins (as now).
- Private-use F000–F0FF from any font: F0A1/F0A7/F0B7 → `•`, F0FC → `✓`, other F020–F0FF → the plain
  character (pdf-inspector's rule).
- Tables: Symbol and ZapfDingbats encodings copied from pdf-inspector `base14.rs`.

## Not in this fix
- Embedded TrueType fonts that name their glyphs only inside the font program
  (`glyph_names_in_embedded_fonts.pdf`: `αβγω` → `ABCD`). Needs reading the font file itself; later.

## Tasks
- [x] 1. Tests first: pdf-inspector's `symbol_builtin_encoding.pdf` (1.2 KB, MIT, credited) vs its snapshot;
      a made-up Word-style PDF (Identity-H font whose ToUnicode gives F0B7, F06E, F06F, F0FC, F0E9);
      a Latin font is unchanged; report.pdf unchanged.
- [x] 2. Font-loader wrapper + tables + private-use cleanup in pdf2md.py.
- [x] 3. Rerun the 64 public files: symbol fixture matches its snapshot; private-use count 0; every other
      file's output unchanged except the remapped characters (diff checked). Docs, fingerprints.

## Decided (user, 2026-10-26)
- Fonts named Wingdings (incl. subset-prefixed, Wingdings-Regular, Wingdings2/3 only if a table is known):
  a real Wingdings table (□ ■ ☑ ☒ ✓ ✗ ↑ ↓ ← → ★ …). A model or search can't tell `o` from a checkbox.
- Every other font keeps pdf-inspector's private-use rule exactly (strip F000 → the plain letter), which is
  right for text fonts that store ordinary letters in that range.

## Unresolved
- none

## Review (2026-10-26)
- tests/test_fonts.py 23/23: pdf-inspector fixture = its snapshot lines; made-up Word-style PDF (Wingdings,
  Wingdings 2, SymbolMT, a text font with letters in private use, plain TrueType Wingdings, Helvetica); names;
  the pdf-inspector private-use rule; tick list rule.
- 64 public files rerun: 4 changed — exactly the 4 with symbol fonts; same words in the same order in each,
  only symbols/list structure differ (251 •, 8 ◼ in bits_pilani; 230 🞏 checkboxes in the CIS benchmark; Word
  bullets now real lists). Symbol fixture vs Firecrawl snapshot 0.48 → 0.95 (left: a heading mark).
- Also changed: ✓/✔ start a list item only before a space and stay in the item (`- ✓ Done`), like
  pdf-inspector's bullets (it doesn't treat ticks as bullets at all); report.pdf/scanned.pdf identical to main.
- Fixed while testing: Wikipedia marks the space as a control cell; first parse shifted rows by one (asserted
  16 cells per row now).
- Could break: a non-standard Wingdings variant name, a font named exactly "Symbol" that isn't Adobe Symbol.
