# --check: per-page self-check, run at the office, no document text printed

Why: user can't send pages out each time. `pdf2md.py FILE.pdf -o out.md --check` prints one line per page
(counts only), so a bad page is found without sharing it. Branch on top of broker-profile-pages (PR #15).

Per page, markdown of that page vs the PDF's own text layer:
- missing numbers: numbers on the page not in the markdown (page-number lines skipped)
- merged cells: table cells holding 2+ numbers (`5 -1 100%`)
- unlabelled rows: table rows with numbers but an empty first cell (bar % rows, unfilled group labels)
- glued words: letters the page shows with a gap (> 0.2 x size) run together in the markdown (`seen_gaps`)
- scanned/OCR page: numbers not checked (no text layer to compare)
Output e.g. `page 2: OK` / `page 7: 3 numbers missing, 1 merged cell` + summary line. Never prints words or values.

- [x] tests first (test_layout.py): sample = all OK; old-style bad markdown counted right; output has no doc text
- [x] `check_page()`, `check_pdf()`, `--check` flag (stderr; exit code unchanged)
- [x] README: --check
- [x] all suites, report.md diff, corpus: what --check says on the 47 PDFs (false alarms reviewed)
- [x] --refresh, ASCII
- [x] commit + PR

Next (PR 2): made-up variations (sidebar right/white, vertical bars, no title rules, 1-4 label cols, sizes,
Grand Total, 3-page table).

## Review
- main's output of the made-up copy: 0/3 pages OK (merged cells, unlabelled rows, glued words); branch: 3/3 OK
- corpus 789 pages: 770 OK, 3 scanned (not checked); all 16 flagged pages are real faults: `481.11` (page
  number glued onto a section number), `substantiaalmount` (watermark), a whole column in one cell (thermo),
  3-score cells (bits_pilani), price rows with the label on the row above (nexo)
- first glued-word rule (long letter runs / CamelCase) flagged code names and chemistry; replaced by gaps
  measured on the page. Dot leaders (`.....1.9`) hid numbers from the reader; treated as spaces.
- markdown output of all 51 PDFs unchanged vs PR #15 (check only reads)

## Unresolved
- page numbers in footers: skipped by PAGE_NUM_RE; other running numbers (dates in headers) may show as missing
