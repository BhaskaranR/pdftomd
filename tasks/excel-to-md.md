# Excel → Markdown (Firecrawl-compatible)

Port of Firecrawl's `anydoc` sheet converter (MIT). Full decisions: grilling 2026-10-26, GLOSSARY.md.

## Rules (match Firecrawl)
- visible sheets, workbook order; `## <name>` only if >1 sheet; empty sheet skipped
- table = populated area; hidden rows/cols out; merges → top-left value, rest blank
- header row guessed (anydoc header.rs); else empty header row
- saved values; number formats applied; dates ISO; 15 sig digits
- >4M cells across workbook → named error; encrypted → named error
- charts/pictures ignored; PDF-only options ignored; `parsers: []` → base64

## Ours on top
- `includeHidden` / `--include-hidden`
- formula w/o saved value → blank + `data.warning`

## Tasks
- [x] 1. `sheet2md.py` + `tests/test_sheet.py`, tests first: Firecrawl sample.xlsx golden byte-equal; anydoc cases (hidden, merges, date1904, numfmt, single sheet, empty sheet, header guess, csv delimiters/encoding). `python sheet2md.py x.xlsx` prints md.
- [x] 2. Routing in `scrape()` + CLI (URL ext → content-type → bytes); metadata; README. (No new requirement: reader is stdlib zipfile + ElementTree, like anydoc's own XML reader — openpyxl dropped.)
- [x] 3. `tests/make_workbooks.py` synthetic set (written); compare vs Firecrawl's converter run locally (anydoc crate, no API key, no upload — user 2026-10-26); save expected under `tests/out/`.
- [x] 4. Fingerprints, `skill/scripts/` sync, CLAUDE.md notes, NOTICE credit for anydoc.
- [ ] Later: .xls (xlrd), .xlsb, .ods.

## Unresolved
- none

## Review
### Phase 1 (2026-10-26)
- `tests/test_sheet.py` 145/145. Real firecrawl `sample.xlsx` (scratch copy, not committed) byte-equal to Firecrawl's test string.
- Mutants caught: header guess off, banker's rounding, no text cleaning, hidden-merge remap, no escaping.
- openpyxl-written workbook (dates, %, accounting negatives, merge, hidden + empty sheet, unsaved formula) converts as expected; warning fires.
- PDF gates, pdf2md.py untouched: `test_layout.py` 105/107 (rotated-scan OCR 90/180) and report.md diff (extra image line) fail the same way at HEAD in a fresh venv (pdfplumber 0.11.10) — pre-existing, not from this work.
- Could break: Excel-only XML quirks not in tests (strict OOXML namespaces, chartsheets, shared formulas w/o values), huge sheets (whole-part XML parse, memory), CSV edge quoting where Python csv ≠ Rust csv crate.
### Phase 2 (2026-10-26)
- `scrape()` + `pdf2md.py` CLI route spreadsheets (path/URL ext → Content-Type → bytes; PDF bytes always win). New: `include_hidden`, `filename` (names a bytes source), `--include-hidden`; metadata `contentType`, `numSheets`, `sheetNames`; errors print their code.
- Unsaved-formula count moved to per-sheet visible cells (was only cells inside the table → a sheet of only formulas warned nothing).
- `test_sheet.py` 163/163; Content-Type-only URL checked against a local HTTP server.
- PDF unchanged: report.pdf + scanned.pdf JSON (markdown, pages, blocks) identical to HEAD. `test_vision.py` 10 FAILED and `test_layout.py` 105/107 identical on a clean HEAD copy → pre-existing OCR/env issue.
- Not done here (task 4): fingerprints, `skill/scripts/` copy (its pdf2md.py is now behind and lacks sheet2md.py), CLAUDE.md notes, NOTICE.
### Task 4 (2026-10-26)
- fingerprints.txt: only load_source/_fetch/scrape/main changed; 2col rebuilt (rule reproduces the committed file exactly); new fingerprints_sheet.txt; vision unchanged.
- skill/scripts: pdf2md.py + sheet2md.py copied; SKILL.md spreadsheet section + description; skill run from another folder converts sample.xlsx.
- NOTICE (root + skill/) with pdf-inspector and anydoc MIT notices; LICENSE still the owner's call.
- CLAUDE.md: layout, contract, run-before-commit, decisions, open work updated.
### Task 3 (2026-10-26)
- No API key / no upload (user). Built Firecrawl's converter locally instead: tests/anyref (anydoc =0.2.4; Firecrawl pins "0.2.2" with no lockfile → newest 0.2.x).
- 11 workbooks + 14 CSVs + Firecrawl sample + openpyxl smoke file: 27/27 byte-identical after one fix.
- Fix: header guess followed unreleased anydoc (repeat label weaker); 0.2.4 counts it as a data vote. Test first, then ported 0.2.4 rule.
- tests/out/workbooks/*.md saved from anydoc; test_sheet.py checks them (190/190). Generator deterministic.
