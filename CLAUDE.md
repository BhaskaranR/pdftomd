# pdftomd — notes for Claude Code

Single-file PDF → Markdown converter (`pdf2md.py`) with a Firecrawl-compatible API.
Heuristics are ported from Firecrawl's MIT-licensed `pdf-inspector` Rust crate
(https://github.com/firecrawl/pdf-inspector) and rebuilt on pdfplumber.

## Layout
- `pdf2md.py` — everything: extraction, routing, tables, layout, headings, blocks, OCR, `convert()`, `scrape()`, CLI,
  and the vision step (section 12: `pdf2md.py prepare` / `merge` around the agent's vision model for charts, pictures,
  scanned pages; number check against page text/OCR). Keep it one file. `skill/SKILL.md` = the skill's instructions; `python make_skill.py` builds the
  installable folder in `dist/pdf-to-markdown/` (SKILL.md + scripts/pdf2md.py copy). Never commit a copy of pdf2md.py.
  Tests: `tests/test_vision.py` (OCR checks print SKIP without Tesseract).
- `verify_transcription.py` + `fingerprints.txt` / `fingerprints_2col.txt` — per-function fingerprints for checking a retyped copy.
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
`scrape(url, formats, parsers)`: parsers default `["pdf","image"]`, `[]` → base64. PDF options `mode` fast|auto|ocr,
`maxPages` (1–10000), `pages`, `blocks`, `pageMarkers` (booleans only; markers = `---` + `<!-- page N -->` between
pages, none before the first). `fast` on a Scanned/ImageBased PDF raises `SCRAPE_PDF_OCR_REQUIRED`; non-PDF that
isn't an image, or an image with the image parser off, raises `SCRAPE_UNSUPPORTED_FILE_ERROR`. Vision step needs Tesseract.
Response shape and block fields are documented in README.md.

## Rules when changing code
- Run before committing:
  - `python tests/test_layout.py` and `python tests/test_contract.py` (must print all passed)
  - `python tests/make_samples.py tests/samples && python pdf2md.py tests/samples/report.pdf --images none -o /tmp/r.md && diff /tmp/r.md tests/out/report.md`
  - compare old vs new output on a corpus of real PDFs and review every changed file
  - check that every block's `markdownSpan` slices to its `content` (`--blocks` JSON).
- Any code change invalidates the fingerprints: regenerate with
  `python verify_transcription.py pdf2md.py > fingerprints.txt` and rebuild `fingerprints_2col.txt`.
- Don't change thresholds without a test that shows the improvement.

## Decisions
- Never use real bank documents as test data; build synthetic look-alikes (tests/make_scorecard.py).
- Chart images go to GPT-4o for data; don't re-add heuristic chart-to-number extraction.
- MinerU is not allowed in the target environment — don't add it.
- Fallback for hard pages (scanned, complex tables, multi-column) will be GPT-4o, not MinerU.

## Open work
1. LICENSE (owner to choose; MIT suggested) + NOTICE crediting Firecrawl's MIT notice.
2. Done for charts/pictures/scans via `pdf2md.py prepare`/`merge` (the office runs skills + MCP only, so the agent's
   model reads the images; no API calls from Python). Not yet: sending hard *text* pages (complex tables,
   multi-column) through the same step.
3. Wingdings/symbol-font arrows (e.g. rank up/down) come out as unreadable glyphs; needs the font name from a real file.
4. `chunk()` for embeddings: split by heading path, keep tables/code whole, attach page number and bbox.
