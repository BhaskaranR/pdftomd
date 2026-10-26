# Charts from cells (charts=True)

Charts written by scripts (openpyxl & co.) save no numbers, so today they print
`**Chart: X** (column): no saved values`. Each chart still records where its data lives
(`<c:f>'Data'!$B$2:$B$3</c:f>`). Read those cells from the workbook instead. Exact, no guessing.
Only with `charts=True`; default output unchanged (still byte-equal to anydoc).

## What the user sees
- Chart that saved its numbers: unchanged (saved numbers win; they are what the chart showed).
- Chart with no saved numbers but readable cell references: a normal chart table, values as the cells display
  them (the cell's own number format), plus a note in `warning`: "chart 'X' on sheet 'S': values read from its cells".
- References that can't be followed (another workbook `[1]Sheet!…`, a defined name, cells that are themselves
  unsaved formulas): stays `: no saved values` + warning, as today.

## How (sheet2md.py)
- [x] 1. Tests first (`tests/make_workbooks.py` + `tests/test_sheet.py`): chart with refs but no caches —
      bar (cats + 2 series + name refs), title from a cell ref, data on a hidden sheet, quoted sheet name with
      `''`, single cell ref, ref into a hidden row (see unresolved), multi-area `(A!$B$2,A!$B$4)`, external ref
      (stays no saved values). Plus the openpyxl-written file. Default output still = anydoc (compare_anydoc).
- [x] 2. Read every sheet's cells once (hidden ones too — chart data often sits on a hidden sheet), keyed by
      sheet name; `parse_ref`/range parser for `'Name'!$A$1:$B$9`, whole rows/columns clipped to the used area.
- [x] 3. In `_axis_values`: no cache → resolve `c:f` → texts in range order (rows then columns); series name
      and title from their refs too. `has_values` true when any value came from cells.
- [x] 4. README rule row, CLAUDE.md, fingerprints_sheet.txt.

## Decided (user, 2026-10-26)
- Hidden rows/columns in the range left out (Excel's plotVisOnly default); shown when plotVisOnly=0.
- Warning says when values came from cells.

## Review (2026-10-26)
- test_sheet 210/210: new workbook (refs only: title from cell, names from cells, hidden row skipped, data on a
  hidden sheet named with `''`, two-area ref, single cell, external ref → no saved values, unsaved formula cell →
  no saved values) + ref-parser cases. Default output 27/27 identical to anydoc (new file included).
- Real openpyxl-written file: both charts now tables (pie without categories numbered 1, 2 as Excel does).
- Could break: refs to defined names (stay "no saved values"), 2-D value ranges (unsupported → none), very large
  whole-column refs on big sheets (clipped to the used area).
