# pdf2md

PDF → Markdown in Python. A port of the pipeline behind Firecrawl's PDF scraping
(`firecrawl/apps/api/native/src/pdf.rs`, which is a thin wrapper around Firecrawl's
MIT-licensed [`pdf-inspector`](https://github.com/firecrawl/pdf-inspector) Rust crate),
rebuilt on pdfplumber, plus image extraction and optional OCR.

## Install

```bash
pip install -r requirements.txt
# Tesseract, for OCR of scanned pages / images; required for the vision step (prepare/merge):
#   brew install tesseract   (macOS)   |   apt install tesseract-ocr   (Linux)
```

## Firecrawl-style options

Same names and behaviour as Firecrawl's PDF parser
([docs](https://docs.firecrawl.dev/advanced-scraping-guide#scraping-pdfs)):

| Firecrawl option | CLI flag | Meaning |
|---|---|---|
| `parsers` (default `["pdf","image"]`) | `--parsers '<json>'` | `[]` returns the PDF base64'd in `markdown` instead of parsing (as Firecrawl does) |
| `mode: "fast"` | `--mode fast` | embedded text only; scanned pages are skipped, and a fully scanned PDF raises `SCRAPE_PDF_OCR_REQUIRED` (as Firecrawl does) |
| `mode: "auto"` (default) | `--mode auto` | embedded text, OCR only for pages with no text layer |
| `mode: "ocr"` | `--mode ocr` | OCR every page |
| `maxPages` | `--max-pages N` | parse at most N pages, 1–10000 (`numPages` vs `totalPages` in metadata) |
| `pages: true` | `--pages` | per-page markdown in `data.pages` |
| `blocks: true` | `--blocks` | typed layout blocks in `data.blocks` (bbox, type, reading order, `markdownSpan`) |
| `pageMarkers: true` | `--page-markers` | `---` and `<!-- page N -->` between pages (none before the first) |
| `pageMarkdown` (deprecated) | — | old name for `pages`; an explicit `pages` wins |
| `image` parser | (on by default) | OCR when the input is a PNG/JPG instead of a PDF; takes no options. Off (`["pdf"]` or `[]`) → an image file raises `SCRAPE_UNSUPPORTED_FILE_ERROR`. A PDF of scanned images is still a PDF |

As in Firecrawl, any parser other than `"pdf"` / `"image"` (e.g. `"images"`) is an error, `pages` /
`blocks` / `pageMarkers` must be `true` or `false`, a file counts as a PDF when `%PDF` appears in its
first 1 KB, and anything that is neither a PDF nor an image raises `SCRAPE_UNSUPPORTED_FILE_ERROR`. Options of Firecrawl's hosted service (such as
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

## Spreadsheets (xlsx, xlsm, csv)

Same output as Firecrawl, which converts spreadsheets with its MIT-licensed
[`anydoc`](https://github.com/firecrawl/anydoc) crate; `sheet2md.py` is a port of its sheet and CSV
readers (standard library only). `scrape()` and the CLI pick it automatically — from the URL path or
file name, then the Content-Type, then the file's bytes — so the same calls work:

```bash
python pdf2md.py budget.xlsx -o budget.md
python pdf2md.py https://site.com/export.csv
python sheet2md.py budget.xlsx --include-hidden      # spreadsheet-only CLI
python pdf2md.py budget.xlsx --charts                # also print chart data as tables
```

```python
resp = scrape("budget.xlsx", include_hidden=False)
resp["data"]["markdown"]     # one string for the whole workbook
resp["data"]["metadata"]     # sourceFile/sourceURL, contentType, numSheets, sheetNames
resp["data"].get("warning")  # e.g. formula cells with no saved value
```

| Rule | |
|---|---|
| Sheets | Every visible sheet, in workbook order. `## <sheet name>` before each table only when more than one sheet is visible. Empty sheets are skipped |
| Table | One per sheet, covering the area from the first to the last filled cell. Blank rows inside it stay |
| Hidden | Hidden sheets, rows and columns are left out; `include_hidden` / `--include-hidden` keeps them (not in Firecrawl) |
| Merged cells | Value in the top-left cell, the cells it covers blank |
| Header row | Guessed: the first row is a header when it reads as labels over typed columns (numbers, dates, TRUE/FALSE); otherwise the table gets an empty header row |
| Values | Saved formula results; number formats applied (`$1,234.50`, `7.5%`, `(2,500.00)`); dates as `2026-03-15`; 15 significant digits |
| Formulas with no saved value | Blank, and counted in `warning` (not in Firecrawl) — common in files written by scripts; re-save in Excel to fill them |
| CSV | Separator (`,` `;` tab `|`) and encoding (UTF-8, UTF-16, Windows-1252) detected |
| Charts | Ignored by default, like Firecrawl. `charts=True` / `--charts` (not in Firecrawl) adds each chart under its sheet's table: `**Chart: <title>** (column)` and a table of categories × series, copied from the numbers the chart saved (no guessing). A chart that saved no numbers (common in files written by scripts) is filled from the cells it points at — hidden rows and columns left out, as Excel does — with a warning saying so; if those cells can't be read (another workbook, a named range, unsaved formulas) it prints `: no saved values` |
| Not converted | Pictures inside workbooks. PDF options (`mode`, `pages`, `blocks`, `pageMarkers`, `maxPages`) are ignored; `parsers: []` still returns base64 |
| Errors | `SPREADSHEET_TOO_LARGE` (over 4,000,000 cells across the workbook), `SPREADSHEET_ENCRYPTED` (password-protected), `SPREADSHEET_UNSUPPORTED` (.xls, .xlsb — not yet), `SPREADSHEET_UNREADABLE` |

Tests: `python tests/test_sheet.py`. Checked byte-for-byte against Firecrawl's own converter (anydoc 0.2.4,
built locally — no API key) on a synthetic set of workbooks and CSV files: `python tests/compare_anydoc.py`.

## Chunks for embeddings

For search or an assistant that answers from these documents (RAG), the markdown is cut into pieces
that each make sense on their own and point back to the source. Not in Firecrawl.

```bash
python pdf2md.py report.pdf --chunks                     # JSON with data.chunks (default 2,000 chars each)
python pdf2md.py budget.xlsx --chunks --chunk-size 1000
python chunk2md.py notes.md                              # chunk markdown you already have
```

```python
resp = scrape("report.pdf", chunk={"maxChars": 2000})   # or chunk=True
for c in resp["data"]["chunks"]:
    c["text"]          # what to embed: "Annual Report > Loans" + the piece
    c["headingPath"]   # ["Annual Report", "Loans"]; a spreadsheet gives ["Sheet"] or ["Sheet", "Chart: X"]
    c["content"], c["markdownSpan"]   # the piece and where it sits in data.markdown
    c["pages"], c["bboxes"]           # PDFs: pages and boxes (0–1) it came from

from chunk2md import chunk                           # standard library only: no PDF libraries needed
chunk(convert("report.pdf"), max_chars=1000)        # also takes a spreadsheet result or plain markdown
```

| Rule | |
|---|---|
| Sections | A chunk never spans two headings; whole blocks are packed up to the size (measured on `text`) |
| Never cut | Paragraphs (a paragraph over the size is split at sentence ends), list items, code blocks, table rows |
| Tables | Whole if they fit; otherwise split by rows with the header row repeated (`split: true`) |
| Headings | A heading with nothing under it only appears in the paths; a heading never forms a chunk alone |
| Left out | Page headers, footers and numbers; page markers |
| `oversize: true` | A single row, list item or code block bigger than the size, kept whole |

## Emails (Outlook .msg, .eml)

Not in Firecrawl. `msg2md.py` (standard library only) reads Outlook `.msg` files and `.eml` files;
`scrape()` and the CLI pick it automatically (file name or URL path, Content-Type, then the bytes) and
read the attachments with the same code as everything else:

```bash
python pdf2md.py mail.msg -o mail.md                     # pictures -> mail_images/
python pdf2md.py mail.msg --password secret --mode fast  # PDF options apply to attached PDFs
python msg2md.py mail.eml                                # email-only CLI: attachments listed, not read
```

One email becomes one document:

```
# <subject>

**From:** Name <address>
**To:** ...   **Cc:** ...   **Bcc:** ... (when the file has them)
**Date:** 2026-10-26 14:03 UTC
**Attachments:** report.pdf, budget.xlsx

<message text>

## Attachment: report.pdf

<the PDF converted as on its own, its headings pushed down two levels>
```

| Part | How |
|---|---|
| People | `Name <address>`; Exchange-internal addresses (`/O=EXCHANGELABS/…`) give the name only |
| Meeting invites | Attendees, Optional, Start, End and Location lines instead of To and Cc |
| Message text | The HTML version when there is one, converted with the rules of Firecrawl's web-page converter (a port of [`firecrawl/html-to-markdown`](https://github.com/firecrawl/html-to-markdown) with its GitHub-flavored and code-block plugins; `<head>` dropped). Unlike Firecrawl, tables that only lay the email out (marked `role="presentation"`, one column, or holding tables, headings, lists or quotes) are unwrapped to their content and tracking pixels (0–1 px or hidden) are dropped; data tables stay tables. Else Outlook's RTF: HTML wrapped in RTF goes the same way; other RTF gives its words, paragraphs and table rows (no bold/italic). Else the plain text, as written. Quoted reply history is kept. Headings in HTML text go one level under the subject; plain and RTF text stay as written |
| Attachments read | PDFs (the `pdf` parser's options and `--password`), pictures (OCR, with the `image` parser), workbooks (xlsx, xlsm, csv; `--include-hidden`, `--charts`), attached emails up to 3 levels deep |
| Listed only | Everything else (old `.xls`, Word, text, zip, …); a PDF or picture when its parser is off |
| Pictures | Under 10 KB (logos, icons) are skipped. Pictures pasted into the text (hidden, or pointed at by `cid:`) are put where they sit (saved, embedded or left out per `images`) with their OCR text; they are not in the Attachments line |
| Unreadable attachments | A password-protected or damaged file gets `_Could not be read: <reason>._` in its section and a warning; the rest goes on |
| Metadata | `title`, `contentType`, `from`, `to`, `cc`, `bcc`, `date` (ISO, UTC), `attachments` (`name`, `size`, `contentType`, `read`); invites add `start`, `end`, `location` |
| Not returned | `pages` and `blocks` (a warning says so); `parsers: []` returns the file base64'd |
| Errors | `EMAIL_UNREADABLE` (damaged file), `EMAIL_UNSUPPORTED` (an old Office file that is not an email) |

Tests: `python tests/test_mail.py` (emails built by `tests/make_mail.py` with invented data; the HTML
converter is checked against html-to-markdown's own expected outputs in `tests/html2md/`).

## Charts and scanned pages (vision step)

The `prepare` and `merge` commands of `pdf2md.py` add chart data and scanned-page structure using the agent's own vision
model (e.g. GPT-4o inside a skill), so no API keys are needed. It needs Tesseract: numbers on scans and
pictures are checked against OCR text, and `prepare` stops if Tesseract is missing:

```bash
python pdf2md.py prepare report.pdf work/   # draft.md, images, tasks.json (one prompt per image)
# the agent answers each task into work/answers/<id>.md
python pdf2md.py merge work/ -o report.md   # every number checked against page text / OCR
```

Every number in an answer must match the page's own text in value, sign (`-4.5`, `(1,200)`) and, for
table rows, row: a number not found, with the other sign, or sitting next to another row's label is
marked ⚠. Numbers whose row label can't be located on the page are listed as `ROW NOT CONFIRMED`.
Values read off a drawing are written `~42`.
OCR on JPEG scans sometimes misreads a digit (`17.5` as `1735`); the model's correct value is then
flagged ⚠, never passed silently.
Missing answers keep the plain pdf2md output.

To install it as an agent skill, build the skill folder (SKILL.md + copies of `pdf2md.py`, `sheet2md.py` and `msg2md.py`):

```bash
python make_skill.py          # -> dist/pdf-to-markdown/   (--zip for a .zip)
```

Only `skill/SKILL.md` is kept in git; the scripts are copied in by `make_skill.py`.
Tests: `python tests/test_vision.py` (checks that need Tesseract are skipped without it).

## Limits

- Speed: pdfminer is pure Python — roughly 0.1–0.7 s per page depending on density, vs. milliseconds in Rust. Use `--max-pages` or `--page-range` for huge files, or `pip install pdf-inspector` (Firecrawl's own Python bindings) if you only need text and tables.
- Without the vision step, charts are pictures plus their labels, not data.
- Image-only (scanned) pages: OCR gives the words, but table and chart structure inside the image is lost.
- Side-by-side tables are only split when rule lines separate them; two borderless tables side by side on one row grid still merge.
- Section labels on a page that is almost all tables may come out as plain lines rather than headings.
- OCR'd pages give headings and paragraphs, but tables on scanned pages come out as text lines.
- Block `confidence.layout` is always null (no layout model is used).
- `formats` other than `markdown` (e.g. `html`) are ignored with a warning; Firecrawl also builds `html` for PDFs.
- If two `pdf` parsers are given, the first is used (as in Firecrawl); every one is still checked.
- CLI errors print the Firecrawl code (`error: SCRAPE_PDF_OCR_REQUIRED: …`); with `--json` the error is
  also printed as `{"success": false, "code": …, "error": …}`.
- Right-to-left scripts, 3+ column layouts, and complex merged-cell tables are handled more simply than in pdf-inspector.

## Tests

```bash
python tests/make_samples.py tests/samples   # builds report.pdf + scanned.pdf
python pdf2md.py tests/samples/report.pdf -o tests/out/report.md
python tests/test_layout.py                  # side-by-side tables, charts, and must-not-change cases
python tests/test_contract.py                # Firecrawl options and PDF detection
python tests/test_sheet.py                   # spreadsheets: Firecrawl sample + anydoc cases + synthetic set
python tests/test_chunk.py                   # chunks for embeddings
python tests/compare_anydoc.py               # sheet2md vs Firecrawl's converter built locally (needs Rust; see tests/anyref)
python tests/test_mail.py                    # emails: .msg/.eml, attachments, HTML/RTF message text
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
Python 3.11–3.13. `fingerprints_2col.txt` is the same list laid out for screenshots. `fingerprints_sheet.txt`,
`fingerprints_chunk.txt` and `fingerprints_msg.txt` are the lists for `sheet2md.py`, `chunk2md.py` and `msg2md.py`.
If nearly every line differs, recheck `verify_transcription.py` itself first.

## License

MIT — see `LICENSE`. Code ported from Firecrawl's MIT-licensed `pdf-inspector` and `anydoc`
is credited in `NOTICE`.
