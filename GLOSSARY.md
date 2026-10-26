# pdftomd

Turns documents (PDFs, spreadsheets) into Markdown that reads the same as Firecrawl's output.

## Spreadsheets

**Workbook**:
One spreadsheet file (xlsx, xlsm, csv) holding one or more sheets.
_Avoid_: Spreadsheet (for the file), Excel file

**Sheet**:
One named grid in a workbook that a person can see when opening it; becomes one Markdown table. Hidden sheets are not sheets unless hidden content is asked for.
_Avoid_: Tab, worksheet, page

**Hidden content**:
Sheets, rows or columns the workbook marks as hidden; left out of the output by default.
_Avoid_: Secret, invisible data

**Populated area**:
The rectangle from the first to the last non-empty visible cell of a sheet, widened to cover merged cells touching it. Only this area becomes the table.
_Avoid_: Used range, data range

**Header row**:
The first row of a sheet's table when it reads as column labels over typed data; guessed, since workbooks never mark one. When the guess fails the table gets an empty header row.
_Avoid_: Title row, column names

**Saved value**:
The result of a formula as stored in the file the last time it was calculated. Shown in place of the formula; a formula with no saved value shows as blank.
_Avoid_: Cached value, computed value

**Number format**:
The workbook's instruction for how a stored number is displayed (currency, percent, thousands, date).
_Avoid_: Cell format, style
