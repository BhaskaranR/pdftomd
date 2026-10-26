# pdf2md

PDF → Markdown in Python. A port of the pipeline behind Firecrawl's PDF scraping
(`firecrawl/apps/api/native/src/pdf.rs`, which is a thin wrapper around Firecrawl's
MIT-licensed [`pdf-inspector`](https://github.com/firecrawl/pdf-inspector) Rust crate),
rebuilt on pdfplumber, plus image extraction and optional OCR.

## Install

```bash
pip install -r requirements.txt
# for OCR of scanned pages / images:  apt install tesseract-ocr   (or brew install tesseract)
```

## Firecrawl-style options

Same names and behaviour as Firecrawl's PDF parser
([docs](https://docs.firecrawl.dev/advanced-scraping-guide#scraping-pdfs)):

| Firecrawl option | CLI flag | Meaning |
|---|---|---|
| `parsers` (default `["pdf","image"]`) | `--parsers '<json>'` | `[]` returns the file as base64 instead of parsing |
| `mode: "fast"` | `--mode fast` | embedded text only; scanned pages are skipped, and a fully scanned PDF raises `SCRAPE_PDF_OCR_REQUIRED` (as Firecrawl does) |
| `mode: "auto"` (default) | `--mode auto` | embedded text, OCR only for pages with no text layer |
| `mode: "ocr"` | `--mode ocr` | OCR every page |
| `maxPages` | `--max-pages N` | parse at most N pages (`numPages` vs `totalPages` in metadata) |
| `pages: true` | `--pages` | per-page markdown in `data.pages` |
| `blocks: true` | `--blocks` | typed layout blocks in `data.blocks` (bbox, type, reading order, `markdownSpan`) |
| `pageMarkers: true` | `--page-markers` | `<!-- page N -->` between pages |
| `pageMarkdown` (deprecated) | — | old name for `pages`; an explicit `pages` wins |
| `image` parser | (on by default) | OCR when the input is a PNG/JPG instead of a PDF; takes no options |

As in Firecrawl, any parser other than `"pdf"` / `"image"` (e.g. `"images"`) is an error, and a file
counts as a PDF when `%PDF` appears in its first 1 KB. Options of Firecrawl's hosted service (such as
`refresh`, which skips its cache) are not accepted: this runs locally, with no API key and no cache.

Extras not in Firecrawl: `images` (`save` / `embed` / `none`), `--page-range 1-5,8`,
`--ocr-lang eng+hin`, `--password`.

### Python

```python
from pdf2md import scrape

resp = scrape("https://example.com/report.pdf",          # or a path, or bytes
              formats=["markdown"],
              parsers=[{"type": "pdf", "mode": "auto", "maxPages": 10,
                        "pages": True, "blocks": True, "pageMarkers": True}])
data = resp["data"]
data["markdown"]                 # whole document
data["pages"]                    # [{"pageNumber": 1, "markdown": "..."}, ...]
data["blocks"]                   # [{"pageNumber", "width", "height", "status", "items": [...]}]
data["metadata"]                 # title, numPages, totalPages, sourceURL/sourceFile, pdfType, ...
```

Each block item: `id` (`p1.b0`), `type` (`title`, `section_header`, `text`, `table`,
`figure`, `caption`, `page_header`, `page_footer`, `page_number`), `bbox` (0–1, `[x0, y0, x1, y1]`),
`content`, `markdownSpan` (`[start, end)` into `markdown`; `null` for headers/footers that were
removed from the markdown), `readingOrder`, `source` (`native_text` or `ocr`),
`confidence` (`{"layout": null, "ocr": 0-1 or null}`).

Lower level: `convert(path, mode=..., max_pages=..., page_markers=..., images=...)` returns a
`Result` with `.markdown`, `.pages`, `.blocks`, `.pdf_type`, `.title`, `.images`.

### CLI

```bash
python pdf2md.py report.pdf -o report.md                    # markdown, images -> report_images/
python pdf2md.py report.pdf --mode fast --max-pages 10
python pdf2md.py scan.pdf --mode ocr -o scan.md
python pdf2md.py report.pdf --pages --blocks -o report.json # Firecrawl-style JSON
python pdf2md.py report.pdf --parsers '[{"type":"pdf","mode":"fast","maxPages":5,"pages":true}]'
python pdf2md.py https://site.com/file.pdf --images embed -o out.md
python pdf2md.py receipt.jpg                                # image parser (OCR)
```

## What it handles

| Element | How |
|---|---|
| Headings H1–H4 | Font-size tiers vs. body size (≥1.2×, sizes within 0.5pt share a tier), bold fallback, base-size correction — ported from `markdown/analysis.rs` |
| Paragraphs | Line grouping, paragraph breaks by spacing / short last line / indent, de-hyphenation |
| Bold, italic, links | From font names and link annotations |
| Lists | Bullets (incl. unmapped bullet glyphs), `1.` `1)` `(a)` markers, hanging-indent numbers, wrapped items |
| Code | Monospace lines → fenced blocks with indentation kept |
| Tables | Ruled/boxed tables from drawn lines, then borderless tables from text alignment; lists and two-column prose are rejected. A header row just above a banded (shaded) table is attached to it; a header + one row counts as a table when the header is underlined |
| Side-by-side tables | Independent blocks next to each other (e.g. two tables exported from one spreadsheet, sharing a row grid) are split into regions when their rule lines stop at the gutter, and each region is read on its own |
| Charts | Charts drawn as vector graphics are detected and, with images on, saved as a PNG next to their labels. Numbers are never read out of chart drawings (too unreliable across PDFs); chart text stays in the output as-is |
| Images | Embedded images cropped and saved as PNG (or base64); tiny icons and full-page backgrounds skipped |
| Layout | Two-column detection and reading order; running headers/footers and page numbers stripped (`markdown/furniture.rs` rules); drop caps re-attached |
| Scanned PDFs | Classified like `detector.rs`; `auto`/`ocr` modes run Tesseract (paragraphs, headings by text height, confidence). Pages scanned sideways or upside down are detected and turned upright first. Without Tesseract the page image is kept |

## Charts and scanned pages (vision step)

The `prepare` and `merge` commands of `pdf2md.py` add chart data and scanned-page structure using the agent's own vision
model (e.g. GPT-4o inside a skill), so no API keys are needed:

```bash
python pdf2md.py prepare report.pdf work/   # draft.md, images, tasks.json (one prompt per image)
# the agent answers each task into work/answers/<id>.md
python pdf2md.py merge work/ -o report.md   # every number checked against page text / OCR
```

Numbers not found in the page's own text are marked ⚠; values read off a drawing are written `~42`.
Missing answers keep the plain pdf2md output.

To install it as an agent skill, build the skill folder (SKILL.md + a copy of `pdf2md.py`):

```bash
python make_skill.py          # -> dist/pdf-to-markdown/   (--zip for a .zip)
```

Only `skill/SKILL.md` is kept in git; there is one `pdf2md.py` to edit.
Tests: `python tests/test_vision.py` (checks that need Tesseract are skipped without it).

## Limits

- Speed: pdfminer is pure Python — roughly 0.1–0.7 s per page depending on density, vs. milliseconds in Rust. Use `--max-pages` or `--page-range` for huge files, or `pip install pdf-inspector` (Firecrawl's own Python bindings) if you only need text and tables.
- Without the vision step, charts are pictures plus their labels, not data.
- Image-only (scanned) pages: OCR gives the words, but table and chart structure inside the image is lost.
- Side-by-side tables are only split when rule lines separate them; two borderless tables side by side on one row grid still merge.
- Section labels on a page that is almost all tables may come out as plain lines rather than headings.
- OCR'd pages give headings and paragraphs, but tables on scanned pages come out as text lines.
- Block `confidence.layout` is always null (no layout model is used). The base64 field name for `parsers: []` is `base64`; Firecrawl's docs don't name it.
- Right-to-left scripts, 3+ column layouts, and complex merged-cell tables are handled more simply than in pdf-inspector.

## Tests

```bash
python tests/make_samples.py tests/samples   # builds report.pdf + scanned.pdf
python pdf2md.py tests/samples/report.pdf -o tests/out/report.md
python tests/test_layout.py                  # side-by-side tables, charts, and must-not-change cases
python tests/test_contract.py                # Firecrawl options and PDF detection
python tests/make_scorecard.py tests/samples/scorecard.pdf   # synthetic scorecard (invented data)
```

## Transcription check

If you retype or OCR this code into another environment, verify the copy:

```bash
python verify_transcription.py pdf2md.py > mine.txt
diff fingerprints.txt mine.txt     # no output = identical code
```

Each line is a fingerprint of one function or constant. Whitespace and comments
don't count; any change to code, numbers, or regexes does. Works the same on
Python 3.11–3.13. `fingerprints_2col.txt` is the same list laid out for screenshots.
If nearly every line differs, recheck `verify_transcription.py` itself first.
