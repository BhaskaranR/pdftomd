# Fold sheet2md.py into pdf2md.py

Main's rule: one `pdf2md.py`. Spreadsheets live in `sheet2md.py` (2,040 lines). Move it in as
section 13; no behaviour change. pdf2md.py goes 2,683 → ~4,700 lines.

## What changes for the user
- `python pdf2md.py budget.xlsx [--include-hidden] [--charts]` — same as today.
- `python sheet2md.py …` goes away (pdf2md.py already does everything it did).
- `from pdf2md import scrape` — same. Skill folder ships one script again.

## Name clashes (only 4) → rename the spreadsheet side
| sheet2md | in pdf2md |
|---|---|
| `Result` | `SheetResult` |
| `convert()` | `convert_sheet()` |
| `classify()` (header guess) | `classify_value()` |
| `main()` | dropped (pdf2md's CLI covers it) |

Everything else keeps its name (no other overlap; checked with ast).

## Tasks (split: >3 files)
- [x] 1. Code (pdf2md.py, sheet2md.py): imports (bisect, codecs, csv, posixpath, unicodedata, ET, zipfile,
      Decimal, Fraction) to the top; sheet2md body → `# ──── 13. spreadsheets (port of anydoc) ────` before
      `def main`, its sub-headers as `# ---- name ----`, its docstring as the section's comment; 4 renames;
      `scrape()` calls them directly (no `import sheet2md`); module docstring gets a spreadsheet line;
      `git rm sheet2md.py`.
- [x] 2. Tests (test_sheet.py, compare_anydoc.py, make_workbooks.py docstring): `import pdf2md as sheet2md`
      style aliasing, renamed names; CLI test via `pdf2md.main`.
- [x] 3. Docs + packaging (make_skill.py, skill/SKILL.md, README, CLAUDE.md): drop sheet2md copy and CLI line;
      fingerprints.txt + 2col regenerated, `fingerprints_sheet.txt` removed. Delete shipped plans
      `tasks/excel-to-md.md`, `tasks/workbook-charts.md`.

## Proof it changed nothing
- Before the move, save outputs from main: every synthetic workbook/CSV (charts on and off), Firecrawl sample,
  report.pdf + scanned.pdf JSON (markdown, pages, blocks).
- After: same bytes. Plus test_sheet 200/200, test_contract, test_layout 107/107, test_vision,
  compare_anydoc 26/26, make_skill folder runs a spreadsheet from another directory.
- Fingerprints: every sheet2md function keeps its old fingerprint except the 4 renamed + scrape.

## Decided
- `python sheet2md.py` dropped; only the 4 clashes renamed (user, 2026-10-26).

## Review (2026-10-26)
- 118 snapshots (every synthetic workbook/CSV + sample + smoke + openpyxl chart file, charts × include_hidden,
  report.pdf + scanned.pdf JSON with pages/blocks) byte-identical to main.
- test_sheet 199/199 (sheet2md CLI test dropped), test_contract, test_layout 107/107, test_vision all passed;
  compare_anydoc 26/26 + sample.
- Fingerprints vs before: only scrape, the 3 renames, their callers (_dominant, header_rows), sheet main dropped.
- make_skill folder = SKILL.md, NOTICE, scripts/pdf2md.py; converts a workbook from another directory.
- pdf2md.py 4,697 lines. Shipped plans excel-to-md.md / workbook-charts.md deleted.
