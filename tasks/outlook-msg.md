# Outlook .msg (+ .eml) → Markdown, attachments included

One email in → one markdown doc: `# Subject`, From/To/Cc/Bcc/Date (UTC)/Attachments lines, message text,
`## Attachment: <name>` per attachment (content headings pushed down). Invites add Start/End/Location/Attendees.
Full decisions: grilling session 2026-10-26 (plan file squishy-roaming-cookie.md).

## Tasks
- [x] 0. Sync with main (fast-forward to 408e3fa; again to 675efcc: LICENSE + chunk2md merged in, emails chunk too). One file per format confirmed → `msg2md.py`.
- [x] 1. `tests/make_mail.py`: compound-file writer + synthetic .msg/.eml set. Cross-check the writer with the
      real `cfb` crate (cached locally, `cargo --offline`).
- [x] 2. `msg2md.py` part 1: container reader (cfb port), properties, recipients, header lines, invites,
      plain text, attachment list, attached emails (≤3 deep). `tests/test_mail.py`.
- [x] 3. `msg2md.py` part 2: HTML → markdown (firecrawl/html-to-markdown rules, golden pairs from its testdata),
      compressed RTF (MS-OXRTFCP vector) + RTF reader (anydoc port, checked with anyref), `.eml`.
- [x] 4. `pdf2md.py`: scrape() email branch (before sheets — .msg is OLE like .xls), attachment reading
      (PDF/image/workbook), inline pictures, CLI; SKILL.md "Install" lists msg2md.py.
- [x] 5. README, skill/SKILL.md, CLAUDE.md, NOTICE, GLOSSARY.md; fingerprints; goldens `tests/out/mail/`.
- [x] 6. All checks (test_mail, test_sheet, test_layout, test_contract, report.pdf diff).

## Review
- Tests: test_mail 147/147, test_chunk 70/70, test_sheet 210/210, test_layout 107/107, test_contract, test_vision all
  passed; report.pdf output identical; 45 real PDFs (firecrawl-fixtures) old vs new: identical markdown/blocks/metadata.
- Compound-file reader agrees with the real `cfb` crate (strict open) on every sample: paths, sizes, byte checksums.
- HTML converter: 25/25 of html-to-markdown's own expected outputs (`tests/html2md/`); compressed RTF = spec example.
- Changed vs plan: message text prefers HTML > RTF > plain (user chose "keep structure"); native RTF gives words,
  paragraphs and table rows only (user chose this over a 3–4k-line anydoc port).
- Not covered: real Outlook files (synthetic only, by decision); Outlook's quirkier HTML (Word-generated, misnested
  tags) goes through a simplified HTML5 tree builder; named-string properties; `.msg` with Unicode-less ANSI recipients.

## Review fixes (2026-10-26, test first: tests/test_mail.py test_review_findings)
- [x] Deep HTML: tree walks are loops (no RecursionError); leading-space trim visits only indented lines
      (same output on 20,000 random strings; 2,000-deep 1.0 s -> 0.12 s).
- [x] Picture pointed at by cid: is inline even without the hidden flag (no double OCR / section).
- [x] .eml unknown charset: UTF-8, then cp1252 fallback.  - [x] Subject with line breaks: one title line.
- [x] Picture named '..': safe file name.  - [x] Hidden non-picture attachments: listed and read.
- [x] Plain and RTF text: headings not pushed down (kept as written).

## Real newsletter (2026-10-26)
- [x] A real marketing .eml came out as one broken table (nested layout tables). Emails now unwrap layout tables
      (role=presentation / 1 column / tables, headings, lists, quotes in cells) and drop tracking pixels; data tables
      stay; web-page goldens untouched. Test: test_layout_tables + tests/out/mail/newsletter.eml.md.

## Unresolved
- none (follow-ups: image step for attached PDFs; old .xls; Word attachments)
