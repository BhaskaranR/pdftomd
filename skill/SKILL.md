---
name: pdf-to-markdown
description: Convert a PDF to Markdown, including tables, scanned pages, and chart data.
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
   - Do not edit `draft.md` or `tasks.json` yourself.

3. Merge. Run:

   ```
   python scripts/pdf2md.py merge <work_folder> -o <output.md>
   ```

   It checks every number in your answers against the page's own text (or OCR),
   marks any number it can't find with ⚠, and writes the final Markdown.
   It prints one line per task.

4. Report to the user:
   - where the Markdown file is;
   - every line from step 3 that says `NOT FOUND`, `no answer`, or `unchecked`,
     because those numbers need a person to check them against the image;
   - how many values are estimates (`~`).
   Do not say the numbers are verified when the merge output says otherwise.

## Options

- `prepare --page-range 1-5,8` or `--max-pages N` for part of a large PDF.
- `prepare --mode ocr` treats every page as a scan (for a PDF whose text layer is garbage).
- `merge --page-markers` puts `---` and `<!-- page N -->` between pages (none before the first).
- Plain conversion without the image step: `python scripts/pdf2md.py <input.pdf> -o <output.md>`.
