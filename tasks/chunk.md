# chunk(): markdown → pieces for embeddings

Output feeds search/RAG; it must be cut into pieces that each make sense alone and point back to
the source. One chunker for every format: it reads the final markdown's structure (headings, paragraphs,
lists, tables, code), so PDFs and spreadsheets chunk the same way. PDFs add page + position from blocks.

## What the user gets
`chunk(result_or_markdown, max_chars=…)` → list of:
```json
{"id": "c3", "text": "Annual Report > Loans\n\n| Branch | Amount |\n| --- | --- |\n| North | 10 |",
 "headingPath": ["Annual Report", "Loans"], "content": "<the markdown slice>",
 "markdownSpan": [1200, 1388], "pages": [2], "bboxes": [{"page": 2, "bbox": [0.1, 0.2, 0.9, 0.5]}],
 "types": ["table"]}
```
- `text` = heading path line + the slice (what gets embedded); `content` = exact slice of `markdown`.
- Spreadsheets: `headingPath` = [sheet name] (+ chart title); no pages/bboxes.
- `scrape(..., chunk={"maxChars": N})` adds `data.chunks`; CLI `--chunks` (JSON). Not in Firecrawl.

## Rules
- Split at headings first: a chunk never spans two sections. Within a section, pack whole blocks up to `max_chars`.
- Never split a paragraph, list item, code block or table row. A paragraph longer than the limit is split at
  sentence ends.
- Tables: whole if they fit; else split by rows, header row repeated in every piece.
- Page headers/footers/numbers aren't in `markdown`, so never in chunks. Page markers dropped.
- Empty sections (heading only) attach to the next chunk's path, not a chunk of their own.

## Tasks
- [x] 1. Tests first (`tests/test_chunk.py`): sample report.pdf (paths, pages, bbox, spans slice exactly),
      long paragraph, big table split with header, list kept whole, spreadsheet workbook (sheet path, chart tables),
      every chunk ≤ max_chars except an unsplittable single row/line (flagged).
- [x] 2. `chunk()` in pdf2md.py (markdown block reader + packer + page/bbox from blocks via markdownSpan).
- [x] 3. `scrape()` option + CLI `--chunks`; README; CLAUDE.md; fingerprints; SKILL.md mention.

## Decided (user, 2026-10-26)
- Size in characters, default `max_chars=2000` (≈ 500 tokens); no tokenizer dependency.
- No overlap; the heading path line gives context.
- Spreadsheet chunks name the sheet (and chart title) only; no cell ranges.

## Unresolved
- none

## Review (2026-10-26)
- tests/test_chunk.py 68/68: exact spans, paths, packing, sentence split, table split with header, lists,
  code (oversize), page markers, report.pdf (pages/bbox, no furniture, nothing lost at 300/1000/2000),
  chart workbook paths, scrape option + CLI.
- Fixed while testing: a heading followed by an oversize block was emitted alone; now rides with it.
- Could break: markdown the converters don't produce (nested blockquotes, HTML blocks) is treated as text;
  a table without a header rule splits without repeating a header.
- Moved out of pdf2md.py into its own `chunk2md.py` (user, 2026-10-26): it isn't PDF-specific and a search
  pipeline shouldn't need PDF libraries to import it. scrape()/--chunks load it lazily. Same chunks before/after.
