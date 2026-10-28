# Broker profile pages (dashboard export) → readable tables

Real file can't leave the office; 3-page synthetic look-alike (`broker_profile()` in tests/test_layout.py,
sample at tests/samples/broker_profile.pdf) built from photos of p1-p2 is the test. User checks the real file once.

Decisions (2026-10-28): group labels filled in (label cols only, any table, across page breaks); bar % → 2 columns,
no chart image; sidebar once, before tables, again if changed; stacked tables separate w/ title heading + italic
note; cut names as printed; month strip as text; images otherwise untouched; one PR.

- [x] fixture + failing tests (15 failing at start)
- [x] 1 word gaps: split where char gap > 0.15 x font size (`split_at_gaps`)
- [x] 2 data bars aren't charts (`data_bars`); label grab skips lines that carry on (`LABEL_ROW_GAP`)
- [x] 3 bar % row merges up; split +/- % columns joined (`join_split_columns`)
- [x] 4 sidebar = own region, read first (`side_panel`); repeats dropped (`drop_repeated_panels`), kept out of
      running-header stripping; ruled/unruled letter strips aren't tables
- [x] 5 title bands end tables (`on_band`: bold, band >= 2x line width); title/note split (`split_title_band`)
- [x] 6 group labels (`fill_group_labels`, `fill_rows`): stops at a label-only row, skips total rows
- [x] 7 column gaps scale for text < 7.5pt (`col_gap`); a label running into the next column doesn't merge them
      (`table_gaps`)
- [x] all suites pass; report.md matches; report/scanned/scorecard unchanged; spans ok; ASCII; --refresh
- [x] corpus compare (firecrawl-fixtures, 47 PDFs): 4 changed, reviewed below
- [x] GLOSSARY: dashboard page, data bar, sidebar, group label
- [x] commit + PR
- [ ] office check: pdf2md on the real file + `python pdf_facts.py FILE.pdf 1`; fi ligature

## Review
Corpus changes (old main vs new):
- real-estate-pricing, stacked_header_glyph_runs: spaces restored (`Bond Yield`, `Over Treasury`, `Apples Picked`)
- p1244-1996: two stacked "Date" header columns now split into two columns
- bits_pilani_feedback: 3-number cells split into separate columns (better); its 2-line header ("Depart-/ment")
  now separates from the table (worse, cosmetic)
- fixed on the way: 3 crashes (None cells in ruled tables), 3 tables broken by over-eager title bands,
  a wrong group label copied into another department's row
- encrypted-secret123 fails old and new (needs a password; open work #7)
Not done: sidebar shown again only if it changes is by exact text; `ﬁ` loss needs the office facts output.
