# pdftomd — notes for Claude Code

PDF → Markdown converter (`pdf2md.py`) with a Firecrawl-compatible API; one file per document format.
Heuristics are ported from Firecrawl's MIT-licensed `pdf-inspector` Rust crate
(https://github.com/firecrawl/pdf-inspector) and rebuilt on pdfplumber.
Spreadsheets (xlsx, xlsm, csv) go through `sheet2md.py`, a port of Firecrawl's MIT-licensed `anydoc`
crate (https://github.com/firecrawl/anydoc). Credits in `NOTICE`; terms in `GLOSSARY.md`.

## Layout
- One file per document format, each self-contained: `pdf2md.py` (PDFs, vision step, `scrape()` routing),
  `sheet2md.py` (spreadsheets); a future Word/PowerPoint converter gets its own file. `scrape()` imports the other
  files lazily. Don't fold formats into one file. Never commit copies: `skill/SKILL.md` = the skill's instructions;
  `python make_skill.py` builds the installable folder in `dist/pdf-to-markdown/` from the real files (add new ones to its FILES).
- `pdf2md.py` — extraction, routing, tables, layout, headings, blocks, OCR, `convert()`, `scrape()`, CLI,
  and the vision step (section 12: `pdf2md.py prepare` / `merge` around the agent's vision model for charts, pictures,
  scanned pages; number check against page text/OCR; opt-in `--hard-pages`: text pages with tables/columns, used only
  if numbers and words match the text layer, else rule-based output kept). Tests: `tests/test_vision.py` (OCR checks print SKIP without Tesseract).
- `chunk2md.py` — `chunk()`: the final markdown cut into pieces for embeddings, for every format (reads markdown
  structure; PDF blocks add pages/bboxes via markdownSpan). Stdlib only, so search code can import it without PDF
  libraries. `scrape(chunk=…)` / `--chunks` load it lazily. Tests: `tests/test_chunk.py`.
- `sheet2md.py` — spreadsheets: zip+XML reader (stdlib only), number formats, header-row guess, GFM tables, CSV,
  opt-in chart tables. `convert()`, CLI. Tests: `tests/test_sheet.py`.
- `tests/make_workbooks.py` — synthetic workbooks + CSVs; `tests/out/workbooks/*.md` = anydoc's output for them.
  `tests/anyref/` builds anydoc locally; `tests/compare_anydoc.py [--save] [files]` diffs sheet2md against it.
- `verify_transcription.py` + `fingerprints.txt` / `fingerprints_2col.txt` (pdf2md.py), `fingerprints_sheet.txt`
  (sheet2md.py), `fingerprints_chunk.txt` (chunk2md.py) — per-function fingerprints for checking a retyped copy.
- `tests/make_samples.py` — builds `tests/samples/report.pdf` and `scanned.pdf` (needs reportlab).
- `tests/fonts.py` — `font()` for drawing synthetic scans (DejaVu on Linux, Arial/Times on macOS).
- `tests/out/report.md`, `report.json` — expected output for the sample report.

## Pipeline (in order)
1. Page routing per page: text / hybrid (scan + a little native text) / scan / blank. A little native text on a
   plain background image (no ink once the text is painted out, `image_ink`) is text, not hybrid. Document type TextBased / Scanned / ImageBased / Mixed / Empty.
2. Words → lines (page-relative coordinates via `page.bbox`).
3. Tables: ruled (`find_tables`, lines strategy) then borderless (text alignment).
4. Vector charts detected (filled shapes) and saved as images only — never converted to numbers (tried;
   too unreliable across layouts). Side-by-side blocks split into regions (rules that stop at a gutter,
   rule heights mostly different on each side, both sides text) and read separately; two-column reading
   order; repeated headers/footers and page numbers stripped.
5. Body size + heading tiers (H1–H4), then typed blocks: paragraphs, lists, code, tables, figures, captions.
6. OCR (Tesseract) for scan/hybrid pages in `auto`, every page in `ocr`; sideways/upside-down scans rotated upright first (Tesseract OSD).
7. Document markdown assembled fragment by fragment so every block's `markdownSpan` is exact.

## Firecrawl contract (keep it exact)
`scrape(url, formats, parsers)`: parsers default `["pdf","image"]`, `[]` → the PDF base64'd in `markdown`. PDF options `mode` fast|auto|ocr,
`maxPages` (1–10000), `pages`, `blocks`, `pageMarkers` (booleans only; markers = `---` + `<!-- page N -->` between
pages, none before the first). `fast` on a Scanned/ImageBased PDF raises `SCRAPE_PDF_OCR_REQUIRED`; non-PDF that
isn't an image, or an image with the image parser off, raises `SCRAPE_UNSUPPORTED_FILE_ERROR`. Vision step needs Tesseract.
Response shape and block fields are documented in README.md.
Spreadsheets: found by URL path/file name → Content-Type → bytes (PDF bytes always win). Output must stay
byte-equal to anydoc's (visible sheets only, `## name` only when >1, header row guessed, number formats,
ISO dates). Extras: `include_hidden`, `filename`, unsaved-formula count in `data.warning`, `charts` (off by
default: chart caches → tables; no cache → the cells its c:f points at, hidden rows/cols left out unless
plotVisOnly=0, warning says so; default output must stay byte-equal to anydoc). PDF options ignored;
`parsers: []` → base64 in `markdown`. Errors carry `code`: `SPREADSHEET_TOO_LARGE|ENCRYPTED|UNSUPPORTED|UNREADABLE`.

## Rules when changing code
- Run before committing:
  - `python tests/test_layout.py` and `python tests/test_contract.py` (must print all passed)
  - `python tests/make_samples.py tests/samples && python pdf2md.py tests/samples/report.pdf -o /tmp/report.md && diff /tmp/report.md tests/out/report.md`
    (the expected file links its image as `report_images/…`, so the output must be named `report.md`)
  - compare old vs new output on a corpus of real PDFs and review every changed file
  - check that every block's `markdownSpan` slices to its `content` (`--blocks` JSON).
  - `python tests/test_sheet.py` (must print all passed)
  - `python tests/test_chunk.py` (must print all passed)
  - `python tests/test_fonts.py` (must print all passed)
- Any code change invalidates the fingerprints: regenerate with
  `python verify_transcription.py pdf2md.py > fingerprints.txt` and rebuild `fingerprints_2col.txt`
  (first half left, second half right, left column padded to its widest entry + 2);
  `python verify_transcription.py sheet2md.py > fingerprints_sheet.txt`.
  `python verify_transcription.py chunk2md.py > fingerprints_chunk.txt`.
- Don't change thresholds without a test that shows the improvement.

## Decisions
- Never use real bank documents as test data; build synthetic look-alikes (tests/make_scorecard.py).
- Chart images go to GPT-4o for data; don't re-add heuristic chart-to-number extraction.
- MinerU is not allowed in the target environment — don't add it.
- Fallback for hard pages (scanned, complex tables, multi-column) will be GPT-4o, not MinerU.
- Spreadsheets copy anydoc one-for-one; anydoc itself (built locally) is the reference. No API keys, no
  uploads to Firecrawl — check against local copies of the open-source code only.
- Reference = anydoc 0.2.4, the release Firecrawl's API resolves to (it pins "0.2.2", no lockfile). Unreleased
  anydoc weighs a repeated header label less; when a new 0.2.x ships, bump tests/anyref, rerun compare, port diffs.
  No openpyxl — it would change values (its own date detection); read the XML directly like anydoc.
- One file per format, not one file for everything (2026-10-26): folding spreadsheets in made pdf2md.py ~4,700 lines
  and growing with each format (PR #3, closed). The old one-file rule was about committed copies drifting;
  make_skill.py building the skill from the real files solves that with several files too.

## Open work
1. Done: LICENSE (MIT) + NOTICE (pdf-inspector + anydoc).
2. Done: charts/pictures/scans via `pdf2md.py prepare`/`merge` (the office runs skills + MCP only, so the agent's
   model reads the images; no API calls from Python), and hard *text* pages (tables, columns) via opt-in
   `prepare --hard-pages` (PR #6).
3. Done: symbol fonts (Symbol/ZapfDingbats built-in encodings; Word's private-use symbol codes; Wingdings 1-3 via
   Unicode's mapping, beyond pdf-inspector, which turns them into letters).
4. Done: `chunk()` for embeddings (`chunk2md.py`; `scrape(chunk=…)`, `--chunks`).
5. Spreadsheets: .xls, .xlsb (anydoc reads both in-house; .xls parked 2026-10-26 until real files need it —
   relevant if broker-vote exports come from older systems). Pictures in workbooks → vision step.

Found by running Firecrawl's public test files (64 files; 18 compared with pdf-inspector's snapshots, 2026-10-26),
ranked for the office's documents — mostly broker votes (equity, FICC): tables of brokers, scores, ranks, footnotes:
6. Superscripts are flattened into the text, so footnote marks stick to numbers: `8.4¹` → `8.41`, `H₂O` → `H2O`
   (pdf-inspector writes `<sup>…</sup>` / Unicode superscripts; see its `author_block_superscripts` snapshot).
   Highest priority: a silent wrong value in scorecard tables.
7. Password-protected PDF through `scrape()`: the error message is empty (`PdfminerException:`). The CLI already
   says "pass --password" and works with it; make `scrape()` raise the same clear error.
8. Lower: side-by-side prose or a sidebar next to body text is sometimes read as a 2-column table (pdf-inspector
   `real-estate-pricing`, `thermo-freon12`).

Won't do (user, 2026-10-26; not relevant to broker votes — don't propose): embedded TrueType fonts that name glyphs
only inside the font program (pdf-inspector `glyph_names_in_embedded_fonts.pdf`); Hebrew/Arabic reading order;
`.ods` support.
