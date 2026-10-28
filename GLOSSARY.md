# pdftomd

Turns documents (PDFs, spreadsheets, emails) into Markdown that reads the same as Firecrawl's output.

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

## Emails

**Email**:
One message file (Outlook .msg or .eml): its header, message text and attachments; becomes one Markdown document.
_Avoid_: Mail, msg file

**Message text**:
The words the sender wrote, including any quoted earlier replies below them.
_Avoid_: Content, body (fine in code)

**Attachment**:
A file the email carries that Outlook shows with a paperclip; read into its own section when it is a PDF, picture, workbook or attached email.
_Avoid_: Enclosure, file

**Inline picture**:
A picture placed inside the message text rather than attached; shown where it sits, never listed as an attachment.
_Avoid_: Embedded image, CID image

**Meeting invite**:
An email that carries a meeting's time and place; shows attendees, start, end and location instead of To and Cc.
_Avoid_: Appointment, calendar item

**Attached email**:
An email carried as an attachment of another; read the same way, at most three levels deep.
_Avoid_: Forwarded mail, nested msg
