---
name: pdf-to-markdown
description: Convert a PDF to Markdown, including tables, scanned pages, and chart data. Also converts Excel and CSV spreadsheets (xlsx, xlsm, csv) to Markdown tables, and Outlook emails (.msg, .eml) with their PDF, picture and spreadsheet attachments.
---

# PDF to Markdown

The script is `scripts/pdf2md.py` (built into this folder by `make_skill.py` in the pdf2md repo). It needs Python with `pdfplumber`, `pypdfium2` and `Pillow`;
Tesseract (`pytesseract` + the `tesseract` program) is required: `prepare` stops with an
install hint without it, because every number is checked against OCR text.

## Steps

1. Prepare. Run:

   ```
   python scripts/pdf2md.py prepare <input.pdf> <work_folder>
   ```

   It converts the PDF to `<work_folder>/draft.md` and writes `<work_folder>/tasks.json`:
   one task per chart, picture or scanned page, each with an `id`, an `image` path
   (relative to the work folder) and a `prompt`.
   If it prints `0 image task(s)`, skip to step 3.

2. Read each image. For every task in `tasks.json`, in order:
   - open the image file `<work_folder>/<image>` and look at it;
   - follow the task's `prompt` exactly;
   - save your reply, and nothing else, to `<work_folder>/answers/<id>.md`.

   Rules for your replies:
   - Copy printed numbers exactly as printed. Never round, convert, or calculate.
   - A value you read from a bar length or an axis, rather than a printed label,
     gets a leading `~` (for example `~42`). If you cannot read a value, leave it empty.
     Never fill a gap with a plausible number.
   - Do not summarize or leave out text on a scanned page.
   - For a picture that is not a chart or a table, the reply is just `NONE`.
   - `tables` and `layout` tasks are pages with a real text layer: copy every word and
     number exactly, never use `~`, never reword. An answer that differs from the page's
     text is not used.
   - Do not edit `draft.md` or `tasks.json` yourself.

3. Merge. Run:

   ```
   python scripts/pdf2md.py merge <work_folder> -o <output.md>
   ```

   It checks every number in your answers against the page's own text (or OCR): the value,
   its sign (`-4.5`, `(1,200)`) and, in tables, its row. It marks every mismatch with ⚠
   and writes the final Markdown.
   It prints one line per task.

4. Report to the user:
   - where the Markdown file is;
   - every line from step 3 that says `NOT FOUND`, `SIGN DIFFERS`, `IN ANOTHER ROW`,
     `ROW NOT CONFIRMED`, `kept rule-based output`, `no answer`, or `unchecked`, because those numbers need a person
     to check them against the image;
   - how many values are estimates (`~`).
   Do not say the numbers are verified when the merge output says otherwise. A clean merge
   report means every number was found on the page with its sign and row; it is not proof
   that every cell is right.

## Options

- `prepare --page-range 1-5,8` or `--max-pages N` for part of a large PDF.
- `prepare --mode ocr` treats every page as a scan (for a PDF whose text layer is garbage).
- `prepare --hard-pages` also sends text pages with tables or columns (`--max-hard-pages N`
  to cap how many), for when the plain conversion gets those pages wrong.
- `merge --page-markers` puts `---` and `<!-- page N -->` between pages (none before the first).
- Plain conversion without the image step: `python scripts/pdf2md.py <input.pdf> -o <output.md>`.
- Pieces for search/embeddings: add `--chunks` (JSON output with `data.chunks`; `--chunk-size N`, default 2000).

## Spreadsheets (xlsx, xlsm, csv)

No image step; `scripts/pdf2md.py` hands the file to `scripts/sheet2md.py` next to it. Run:

```
python scripts/pdf2md.py <input.xlsx> -o <output.md>
```

Each visible sheet becomes one table, headed `## <sheet name>` when there is more than one.
Hidden sheets, rows and columns are left out; add `--include-hidden` to keep them.
Add `--charts` to also print each Excel chart's saved numbers as a table under its sheet.
Report to the user any `warning:` line it prints (for example, formula cells that had no
saved value and came out blank) and any `error:` line, word for word.

## Emails (Outlook .msg, .eml)

No image step; `scripts/pdf2md.py` hands the file to `scripts/msg2md.py` next to it and reads the
attachments itself. Run:

```
python scripts/pdf2md.py <input.msg> -o <output.md>
```

Add `--password <password>` if attached PDFs are password-protected. The output starts with the
subject, From/To/Cc/Date and the list of attachments, then the message text, then one
`## Attachment: <name>` section per attachment that was read. PDFs, pictures, spreadsheets and attached
emails are read; other files are only listed by name.
Report to the user every attachment whose section says `Could not be read`, and any `warning:`
or `error:` line, word for word. Do not say an attachment was read when its section says otherwise.
