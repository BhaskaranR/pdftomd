Input/expected pairs copied from firecrawl/html-to-markdown's testdata (MIT; see NOTICE), the converter
Firecrawl runs on web pages. `tests/test_mail.py` checks msg2md's port against them, with the domain
"example.com" as in their tests. `gfm_*` cases run with the GitHub-flavored plugins (as Firecrawl does);
the others are CommonMark-only, as in their tests. Folder = their test name; expected.md = the variation
matching Firecrawl's options (atx headings, `-` bullets, `_` em, `**` strong, inlined links).
