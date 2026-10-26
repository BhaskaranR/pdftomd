# Workbook charts → tables (opt-in)

Firecrawl/anydoc ignore charts. Excel charts store title, series, categories + saved numbers
(`xl/charts/chartN.xml`, `c:strCache` / `c:numCache`). Print them as tables. Exact copy, no guessing.
Default off → output unchanged, still byte-equal to anydoc.

## What the user sees (`charts=True` / `--charts`)
After the sheet's table, one block per chart, top-to-bottom, left-to-right as placed on the sheet:

```
**Chart: Loans by branch** (bar)

| Branch | 2025 | 2026 |
| --- | --- | --- |
| North | 10 | 12 |
| South | 8 | 9 |
```
- first column = categories (header = category axis title, else blank); one column per series (header = series name)
- numbers through the chart's saved number format (existing `NumberFormat`)
- scatter: X column then Y per series; pie: one series
- combo (bar + line): one table, series joined on categories
- chart sheet (whole sheet is a chart): `## <name>` + chart block
- hidden sheet's charts follow `include_hidden`
- no saved numbers → title + empty table skipped + `warning` ("chart 'X' has no saved values")

## Tasks
- [x] 1. Synthetic chart workbooks in `tests/make_workbooks.py` (bar, line, pie, scatter, combo, no-cache, chart sheet, two charts placed out of order, title from cell ref). Tests first in `tests/test_sheet.py`.
- [x] 2. `sheet2md.py`: sheet → drawing → chart rels; read anchors, title, type, series caches; render block. Option through `convert()`, `scrape()`, both CLIs.
- [x] 3. Default-off check: `tests/compare_anydoc.py` still 25/25 + sample. README rule row, CLAUDE.md, skill sync, fingerprints.

## Decided
- Untitled → Excel's own auto-title for a one-series chart (the series name), else "Chart N" on that sheet.
- Multi-level categories → "North / Branch A".
- No saved numbers → `**Chart: X** (kind): no saved values` + warning.

## Review (2026-10-26)
- test_sheet.py 200/200 (bar, line w/ title from cell, pie auto-title, combo, scatter, multi-level, no-cache,
  chart sheet, order by position, hidden sheet, CLIs, scrape). Default output still 26/26 + sample identical to anydoc.
- openpyxl-written charts (no caches) → "no saved values" + warning, no invented numbers.
- Could break: charts in group shapes beyond nesting tested, pivot charts (cache only), date category axes
  (rendered via numCache format code — untested on a real Excel file), chart sheets in .xlsb (unsupported).
- Later: fill cache-less charts from the cells their c:f points at; pictures → vision step.
