# pdftomd — notes for Claude Code

Single-file PDF → Markdown converter (`pdf2md.py`) with a Firecrawl-compatible API.
Heuristics are ported from Firecrawl's MIT-licensed `pdf-inspector` Rust crate
(https://github.com/firecrawl/pdf-inspector) and rebuilt on pdfplumber.
Spreadsheets (xlsx, xlsm, csv) go through `sheet2md.py`, a port of Firecrawl's MIT-licensed `anydoc`
crate (https://github.com/firecrawl/anydoc). Credits in `NOTICE`; terms in `GLOSSARY.md`.

## Layout
- `pdf2md.py` — everything: extraction, routing, tables, layout, headings, blocks, OCR, `convert()`, `scrape()`, CLI.
- `pdf2md_vision.py` — prepare/merge around the agent's vision model (charts, pictures, scanned pages); number check against page text/OCR. `skill/` = SKILL.md + scripts copy. Tests: `tests/test_vision.py`.
- `sheet2md.py` — spreadsheets: zip+XML reader (stdlib only), number formats, header-row guess, GFM tables, CSV. `convert()`, CLI. Tests: `tests/test_sheet.py`.
- `tests/make_workbooks.py` — synthetic workbooks + CSVs; `tests/out/workbooks/*.md` = anydoc's output for them.
  `tests/anyref/` builds anydoc locally; `tests/compare_anydoc.py [--save] [files]` diffs sheet2md against it.
- `verify_transcription.py` + `fingerprints.txt` / `fingerprints_2col.txt` (pdf2md.py), `fingerprints_sheet.txt`, `fingerprints_vision.txt` — per-function fingerprints for checking a retyped copy.
- `tests/make_samples.py` — builds `tests/samples/report.pdf` and `scanned.pdf` (needs reportlab).
- `tests/out/report.md`, `report.json` — expected output for the sample report.

## Pipeline (in order)
1. Page routing per page: text / hybrid (scan + a little native text) / scan / blank. Document type TextBased / Scanned / ImageBased / Mixed / Empty.
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
`scrape(url, formats, parsers)`: parsers default `["pdf","image"]`, `[]` → base64. PDF options `mode` fast|auto|ocr,
`maxPages`, `pages`, `blocks`, `pageMarkers`. `fast` on a Scanned/ImageBased PDF raises `SCRAPE_PDF_OCR_REQUIRED`.
Response shape and block fields are documented in README.md.
Spreadsheets: found by URL path/file name → Content-Type → bytes (PDF bytes always win). Output must stay
byte-equal to anydoc's (visible sheets only, `## name` only when >1, header row guessed, number formats,
ISO dates). Extras: `include_hidden`, `filename`, unsaved-formula count in `data.warning`. PDF options ignored;
`parsers: []` → base64. Errors carry `code`: `SPREADSHEET_TOO_LARGE|ENCRYPTED|UNSUPPORTED|UNREADABLE`.

## Rules when changing code
- Run before committing:
  - `python tests/test_layout.py` (must print all passed)
  - `python tests/make_samples.py tests/samples && python pdf2md.py tests/samples/report.pdf --images none -o /tmp/r.md && diff /tmp/r.md tests/out/report.md`
  - compare old vs new output on a corpus of real PDFs and review every changed file
  - check that every block's `markdownSpan` slices to its `content` (`--blocks` JSON).
  - `python tests/test_sheet.py` (must print all passed)
- Any code change invalidates the fingerprints: regenerate with
  `python verify_transcription.py pdf2md.py > fingerprints.txt` and rebuild `fingerprints_2col.txt`
  (first half left, second half right, left column padded to its widest entry + 2);
  `python verify_transcription.py sheet2md.py > fingerprints_sheet.txt`.
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

## Open work
1. LICENSE (owner to choose; MIT suggested). NOTICE done (pdf-inspector + anydoc).
2. Done for charts/pictures/scans via pdf2md_vision.py (the office runs skills + MCP only, so the agent's
   model reads the images; no API calls from Python). Not yet: sending hard *text* pages (complex tables,
   multi-column) through the same step. Keep skill/scripts/ in sync with the root files.
3. Wingdings/symbol-font arrows (e.g. rank up/down) come out as unreadable glyphs; needs the font name from a real file.
4. `chunk()` for embeddings: split by heading path, keep tables/code whole, attach page number and bbox.
5. Spreadsheets: .xls, .xlsb, .ods (anydoc reads all three in-house). Charts/pictures in workbooks are ignored for now.
