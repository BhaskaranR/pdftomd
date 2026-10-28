"""Email tests: Outlook .msg / .eml -> Markdown (msg2md, and through pdf2md.scrape).
All emails are built by tests/make_mail.py with invented data.
Run: python tests/test_mail.py            (exit code 0 = all passed)
     python tests/test_mail.py --save     (rewrite tests/out/mail/ after a reviewed change)"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import make_mail  # noqa: E402
import msg2md  # noqa: E402
from msg2md import MailError, convert  # noqa: E402

SAMPLES = os.path.join(HERE, "samples", "mail")
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def eq(name, got, want):
    check(name, got == want, f"\n  got:  {got!r}\n  want: {want!r}")


def raises(fn, *args, **kw):
    try:
        fn(*args, **kw)
    except Exception as e:  # noqa: BLE001
        return e
    return None


def path(name):
    return os.path.join(SAMPLES, name)


def md(name, **kw):
    return convert(path(name), **kw).markdown


def test_compound_file():
    cf = msg2md.Compound(open(path("plain.msg"), "rb").read())
    root = cf.children(0)
    check("cfb: root lists the subject stream", "__SUBSTG1.0_0037001F" in root)
    check("cfb: names compare case-insensitively", "__substg1.0_0037001f".upper() in root)
    eq("cfb: small stream from the mini stream",
       cf.stream(root["__SUBSTG1.0_0037001F"]).decode("utf-16-le"), "Q3 sales report\0")
    att = cf.children(root["__ATTACH_VERSION1.0_#00000001"])
    png = cf.stream(att["__SUBSTG1.0_37010102"])
    check("cfb: big stream (>= 4096 bytes) from regular sectors",
          png == open(os.path.join(HERE, "samples", "invoice.png"), "rb").read())
    e = raises(msg2md.Compound, b"not a compound file at all" * 30)
    check("cfb: wrong signature -> EMAIL_UNREADABLE", isinstance(e, MailError) and e.code == "EMAIL_UNREADABLE")
    data = bytearray(open(path("plain.msg"), "rb").read())
    struct.pack_into("<H", data, 28, 0x1234)
    e = raises(msg2md.Compound, bytes(data))
    check("cfb: bad byte order mark -> EMAIL_UNREADABLE", isinstance(e, MailError) and "byte order" in str(e))
    data = bytearray(open(path("plain.msg"), "rb").read())
    struct.pack_into("<I", data, 48, 0xFFFFFFF0)                # first directory sector out of range
    e = raises(msg2md.Compound, bytes(data))
    check("cfb: directory chain out of range -> EMAIL_UNREADABLE", isinstance(e, MailError))
    e = raises(convert, path("not_email.doc"))
    check("an old Word file is not an email -> EMAIL_UNSUPPORTED",
          isinstance(e, MailError) and e.code == "EMAIL_UNSUPPORTED")


def test_header():
    m = md("plain.msg")
    lines = m.split("\n")
    eq("subject is the one top-level title", lines[0], "# Q3 sales report")
    check("From: name <address>", "**From:** Alice Moreno <alice.moreno@example.com>  " in lines)
    check("To lists every recipient in order",
          "**To:** Bram Visser <bram.visser@example.org>, Chen Wei <chen.wei@example.org>  " in lines)
    check("Cc line", "**Cc:** Dana Okafor <dana.okafor@example.net>  " in lines)
    check("Bcc line when the file has one", "**Bcc:** Eli Navarro <eli.navarro@example.net>  " in lines)
    check("Date in UTC, labelled", "**Date:** 2026-10-26 14:03 UTC  " in lines)
    check("Attachments: paperclip files in email order",
          "**Attachments:** report.pdf, invoice.png, budget.xlsx, scores.csv, old.xls, notes.txt, "
          "locked.pdf, broken.pdf" in lines)
    eq("no title other than the subject at level 1", [ln for ln in lines if ln.startswith("# ")],
       ["# Q3 sales report"])
    check("quoted reply history kept", "From: Bram Visser\nSent: Friday\nSubject: figures?" in m)
    check("plain text: CRLF -> LF", "\r" not in m)

    r = convert(path("plain.msg"))
    eq("metadata: title", r.metadata["title"], "Q3 sales report")
    eq("metadata: date ISO UTC", r.metadata["date"], "2026-10-26T14:03:27Z")
    eq("metadata: to", r.metadata["to"], ["Bram Visser <bram.visser@example.org>",
                                          "Chen Wei <chen.wei@example.org>"])
    eq("metadata: content type", r.metadata["contentType"], "application/vnd.ms-outlook")
    eq("metadata: attachment names", [a["name"] for a in r.metadata["attachments"]],
       ["report.pdf", "invoice.png", "budget.xlsx", "scores.csv", "old.xls", "notes.txt", "locked.pdf",
        "broken.pdf"])
    check("without a reader nothing is read", not any(a["read"] for a in r.metadata["attachments"]))
    check("without a reader: no attachment sections", "## Attachment:" not in r.markdown)


def test_people_and_text():
    mail = msg2md.read_msg(open(path("html.msg"), "rb").read())
    eq("Exchange internal address -> name only", mail.sender, ("Ops Team", None))
    eq("no date stored -> none", mail.sent, None)
    eq("unicode subject", mail.subject, "Résumé — 季度报告")
    eq("inline pictures are not paperclip files", [a.name for a in mail.attachments if not a.inline],
       ["summary.pdf"])
    m = md("ansi.msg")
    check("8-bit strings decoded with the email's code page",
          m.startswith("# Café menu\n") and "René Dupont <rene@example.fr>" in m and "Crème brûlée: 6 €" in m)
    eq("an email with nothing in it", md("empty.msg"), "# (no subject)\n")
    eq("markdown characters in names are escaped", msg2md.person_md(("A*B [x]", "a@b.c")),
       "A\\*B \\[x\\] <a@b.c>")


def test_invite():
    m = md("invite.msg")
    for line in ("**Attendees:** Bram Visser <bram.visser@example.org>  ",
                 "**Optional:** Dana Okafor <dana.okafor@example.net>  ",
                 "**Start:** 2026-11-03 09:30 UTC  ", "**End:** 2026-11-03 10:30 UTC  ",
                 "**Location:** Room 4B  "):
        check(f"invite: {line.split(':**')[0][2:]} line", line in m.split("\n"), m)
    check("invite: no To/Cc lines", "**To:**" not in m and "**Cc:**" not in m)
    r = convert(path("invite.msg"))
    eq("invite metadata", (r.metadata["start"], r.metadata["end"], r.metadata["location"]),
       ("2026-11-03T09:30:00Z", "2026-11-03T10:30:00Z", "Room 4B"))


def test_attached_emails():
    r = convert(path("nested.msg"))
    m = r.markdown
    for level, hashes in ((1, "###"), (2, "#####"), (3, "######")):
        check(f"attached email level {level} read, subject pushed down", f"\n{hashes} Level {level}\n" in m)
        check(f"attached email level {level}: its text", f"text at level {level}" in m)
    check("level 4 not read", "deepest" not in m and "_Could not be read: attached emails are read 3 levels "
                                                     "deep._" in m)
    check("level 4: warning", "could not be read: attached emails are read 3 levels deep" in (r.warning or ""))
    check("headings never deeper than ######", "#######" not in m)
    m = md("nested.eml")
    check(".eml: attached email read as an email", "## Attachment: Q3 sales report.eml\n\n### Q3 sales report" in m)
    check(".eml: the attached email's files stay with it",
          m.count("**Attachments:**") == 2 and "**Attachments:** Q3 sales report.eml" in m)


def test_eml():
    m = md("plain.eml")
    check(".eml: same header as .msg", m.startswith("# Q3 sales report\n\n**From:** Alice Moreno "
                                                   "<alice.moreno@example.com>  \n"))
    check(".eml: Date converted to UTC from +05:30", "**Date:** 2026-10-26 14:03 UTC" in m)
    check(".eml: attachments listed", "**Attachments:** report.pdf, scores.csv" in m)
    eq(".eml: content type", convert(path("plain.eml")).metadata["contentType"], "message/rfc822")


def test_detection():
    for name, want in (("plain.msg", ".msg"), ("plain.eml", ".eml"), ("html.eml", ".eml"),
                       ("not_email.doc", None)):
        data = open(path(name), "rb").read()
        eq(f"found from the bytes: {name}", msg2md.email_ext("", None, data), want)
    eq("found from the file name", msg2md.email_ext("x/Mail.MSG", None, b""), ".msg")
    eq("found from a URL path", msg2md.email_ext("https://h/mail.eml?dl=1", None, b""), ".eml")
    eq("found from the content type", msg2md.email_ext("", "message/rfc822; charset=utf-8", b""), ".eml")
    eq("CSV text is not an email", msg2md.email_ext("", None, b"name,score\nAda,91\n"), None)
    eq("one header-like line is not an email", msg2md.email_ext("", None, b"Note: hello\n\nbody\n"), None)


def test_rtf_compression():
    # MS-OXRTFCP section 4.1: the spec's own example
    vec = bytes.fromhex("2d0000002b0000004c5a4675f1c5c7a703000a00726370673132354232"
                        "0af32068656c0900206277 05b06c647d0a800fa0".replace(" ", ""))
    eq("compressed RTF: spec example", msg2md.decompress_rtf(vec),
       b"{\\rtf1\\ansi\\ansicpg1252\\pard hello world}\r\n")
    eq("compressed RTF: test writer CRC = spec CRC", make_mail.crc32_rtf(vec[16:]), 0xA7C7C5F1)
    raw = make_mail.RTF_NATIVE.encode("latin-1")
    eq("compressed RTF: round trip with references", msg2md.decompress_rtf(make_mail.compress_rtf(raw)), raw)
    eq("uncompressed RTF (MELA)", msg2md.decompress_rtf(struct.pack("<IIII", 15, 3, 0x414C454D, 0) + b"abc"),
       b"abc")


def test_html_matches_firecrawl():
    root = os.path.join(HERE, "html2md")
    for case in sorted(os.listdir(root)):
        folder = os.path.join(root, case)
        if not os.path.isdir(folder):
            continue
        html = open(os.path.join(folder, "input.html"), encoding="utf-8").read()
        want = open(os.path.join(folder, "expected.md"), encoding="utf-8").read()
        eq(f"html-to-markdown: {case}", msg2md.html_to_markdown(html, gfm=case.startswith("gfm_"),
                                                                domain="example.com"), want)


def test_html_message_text():
    m = md("html.msg")
    check("HTML: heading pushed under the subject", "\n### Quarterly update\n" in m)
    check("HTML: bold and link", "The numbers are **up** this quarter. See the "
                                 "[full report](https://example.com/q3)." in m)
    check("HTML: list", "- North: 1,410\n- South: 1,020" in m)
    check("HTML: table", "| Region | Q4 |\n| --- | --- |\n| North | 1,410 |\n| South | 1,020 |" in m)
    check("HTML: <br> keeps the line break", "Regards,\n\nAlice" in m)
    check("HTML: <style> and <head> dropped", "margin" not in m)
    check("HTML: no reader -> inline pictures dropped, no cid: links left", "cid:" not in m)
    m = md("html.eml")
    check(".eml: HTML version preferred over plain", "Plain fallback" not in m and "| Region | Q4 |" in m)


def test_rtf_message_text():
    m = md("rtf_html.msg")
    check("RTF-wrapped HTML: unwrapped and converted as HTML", "Meeting moved to **Thursday**." in m)
    check("RTF-wrapped HTML: code page escapes decoded", "Café at 10:00" in m)
    check("RTF-wrapped HTML: \\htmlrtf parts left out", "\\par" not in m and "htmlrtf" not in m)
    m = md("rtf_text.msg")
    check("RTF plain text: lines kept", "Plain note one.\nSecond line with a tab\there." in m)
    m = md("rtf_native.msg")
    # anydoc (Firecrawl) on the same RTF gives the same words, table and paragraphs, plus bold/italic
    check("native RTF: words and paragraphs", "Status for October: on track.\n\nBudget table:" in m)
    check("native RTF: table rows", "| Item | Amount |\n| --- | --- |\n| Rent | 1,200 |\n\nThanks." in m)
    eq("RTF tokens: \\binN payload taken whole", list(msg2md.rtf_tokens(b"\\bin3 {}\\x")),
       [("bin", b"{}\\"), ("byte", ord("x"))])
    eq("RTF: \\uN with fallback skipped", msg2md.rtf_body(b"{\\rtf1\\uc1 caf\\u233?s}")[1], "cafés")


def test_scrape_attachments():
    import pdf2md
    r = pdf2md.scrape(path("plain.msg"), images="none")
    d, m = r["data"], r["data"]["markdown"]
    report = make_mail.pdf_bytes("Sales report", make_mail.ROWS)
    want_pdf = msg2md.shift_headings(pdf2md.convert(report, images="none", name="report.pdf").markdown.strip("\n"), 2)
    check("scrape: attached PDF = the PDF converted on its own, headings pushed down",
          f"## Attachment: report.pdf\n\n{want_pdf}\n\n## Attachment:" in m, m)
    check("scrape: workbook attachment as a table", "## Attachment: budget.xlsx\n\n| Item | Amount |" in m)
    check("scrape: CSV attachment as a table", "## Attachment: scores.csv\n\n| name | score |" in m)
    check("scrape: old .xls and .txt listed only (no section)",
          "## Attachment: old.xls" not in m and "## Attachment: notes.txt" not in m and "old.xls, notes.txt" in m)
    check("scrape: password-protected PDF -> note, rest goes on",
          "## Attachment: locked.pdf\n\n_Could not be read: password-protected._" in m)
    check("scrape: damaged PDF -> note", "## Attachment: broken.pdf\n\n_Could not be read: damaged or not a "
                                         "readable PDF._" in m)
    check("scrape: notes are warnings too", "attachment locked.pdf: could not be read: password-protected"
          in d.get("warning", "") and "attachment broken.pdf" in d.get("warning", ""))
    if pdf2md.ocr_available():
        check("scrape: picture attachment OCR'd", "## Attachment: invoice.png\n\nScanned Invoice" in m, m)
    else:
        print("SKIP scrape: picture attachment OCR'd  (needs Tesseract)")
    read = {a["name"]: a["read"] for a in d["metadata"]["attachments"]}
    eq("scrape: metadata says which were read", [n for n, v in read.items() if v],
       ["report.pdf", "invoice.png", "budget.xlsx", "scores.csv"])
    eq("scrape: metadata keeps the source file", d["metadata"]["sourceFile"], "plain.msg")

    m = pdf2md.scrape(path("plain.msg"), images="none", password="pw123")["data"]["markdown"]
    check("scrape: --password opens attached PDFs", "## Attachment: locked.pdf\n\n### Secret" in m)
    m = pdf2md.scrape(path("plain.msg"), images="none", parsers=["image"])["data"]["markdown"]
    check("scrape: pdf parser off -> PDFs listed only", "## Attachment: report.pdf" not in m and
          "## Attachment: budget.xlsx" in m)
    m = pdf2md.scrape(path("plain.msg"), images="none", parsers=["pdf"])["data"]["markdown"]
    check("scrape: image parser off -> pictures listed only", "## Attachment: invoice.png" not in m and
          "## Attachment: report.pdf" in m)
    r = pdf2md.scrape(path("plain.msg"), images="none", parsers=[{"type": "pdf", "pages": True}])
    check("scrape: pages/blocks asked for -> warning, not an error",
          "pages and blocks are not returned for emails" in r["data"]["warning"] and "pages" not in r["data"])
    r = pdf2md.scrape(path("plain.msg"), parsers=[])
    check("scrape: parsers [] -> the file base64'd",
          r["data"]["markdown"] == __import__("base64").b64encode(open(path("plain.msg"), "rb").read()).decode())
    e = raises(pdf2md.scrape, path("not_email.doc"))
    check("scrape: an old Word file -> SCRAPE_UNSUPPORTED_FILE_ERROR",
          getattr(e, "code", None) == "SCRAPE_UNSUPPORTED_FILE_ERROR")
    data = open(path("plain.msg"), "rb").read()
    eq("scrape: bytes found to be an email without a name",
       pdf2md.scrape(data)["data"]["metadata"]["title"], "Q3 sales report")


def test_scrape_chunks():
    import pdf2md
    d = pdf2md.scrape(path("plain.msg"), images="none", parsers=[{"type": "pdf"}], chunk={"maxChars": 600})["data"]
    paths = [c["headingPath"] for c in d["chunks"]]
    check("chunks: an email chunks under subject > attachment > heading",
          ["Q3 sales report", "Attachment: report.pdf", "Sales report"] in paths, paths)
    check("chunks: every chunk's span slices the markdown",
          all(d["markdown"][c["markdownSpan"][0]:c["markdownSpan"][1]] == c["content"]
              for c in d["chunks"] if not c.get("split")))


def test_scrape_inline_pictures():
    import pdf2md
    m = pdf2md.scrape(path("html.msg"))["data"]["markdown"]
    pos_pic, pos_regards = m.find("![image001.png](data:image/png;base64,"), m.find("Regards,")
    check("inline picture kept where it sits in the text", 0 < pos_pic < pos_regards, m[:600])
    check("logo-sized inline picture dropped", "image002" not in m)
    check("inline pictures are not in the Attachments line", "**Attachments:** summary.pdf\n" in m)
    m = pdf2md.scrape(path("html.eml"), images="none")["data"]["markdown"]
    check(".eml: HTML email converts", m.startswith("# Résumé — 季度报告\n"))
    m = pdf2md.scrape(path("plain.eml"), images="none")["data"]["markdown"]
    check(".eml: PDF attachment read", "## Attachment: report.pdf\n\n### Sales report" in m)
    m = pdf2md.scrape(path("nested.msg"), images="none")["data"]["markdown"]
    check("attachments of an attached email are read too", "#### Attachment: deep.csv\n\n| name | score |" in m)


GOLDEN = os.path.join(HERE, "out", "mail")
GOLDEN_FILES = ("plain.msg", "html.msg", "rtf_html.msg", "rtf_text.msg", "rtf_native.msg", "invite.msg", "nested.msg",
                "ansi.msg", "empty.msg", "plain.eml", "html.eml", "nested.eml", "newsletter.eml")


def golden_md(name):
    """The checked-in expected output: pictures off (no OCR), so it does not depend on Tesseract."""
    import pdf2md
    return pdf2md.scrape(path(name), images="none", parsers=[{"type": "pdf"}])["data"]["markdown"]


def test_expected_outputs():
    for name in GOLDEN_FILES:
        want_path = os.path.join(GOLDEN, name + ".md")
        if "--save" in sys.argv:
            os.makedirs(GOLDEN, exist_ok=True)
            with open(want_path, "w", encoding="utf-8") as f:
                f.write(golden_md(name))
        want = open(want_path, encoding="utf-8").read() if os.path.exists(want_path) else None
        eq(f"expected output: {name}", golden_md(name), want)


def test_review_findings():
    """Each case reproduced a bug found in review (2026-10-26)."""
    import pdf2md
    scan = open(os.path.join(HERE, "samples", "invoice.png"), "rb").read()

    # 1. deeply nested HTML (long forwarded chains) must not hit Python's recursion limit
    e = raises(msg2md.html_to_markdown, "<div>" * 1500 + "deep" + "</div>" * 1500)
    check("review 1: 1500-deep HTML converts", e is None, repr(e))
    if e is None:
        eq("review 1: 1500-deep HTML keeps the text",
           msg2md.html_to_markdown("<div>" * 1500 + "deep" + "</div>" * 1500), "deep")
    e = raises(msg2md.html_to_markdown, "<blockquote>" * 1200 + "<b>q</b>" + "</blockquote>" * 1200)
    check("review 1: 1200-deep quotes with inline markup convert", e is None, repr(e))

    # 2. a picture the HTML points at is inline even without Outlook's hidden flag
    m = make_mail.message("cid", make_mail.ALICE, make_mail.SENT, html='<p>see <img src="cid:pic1"></p>',
                          attachments=[make_mail.attachment("image001.png", scan, "image/png", cid="pic1")])
    md2 = pdf2md.scrape(make_mail.msg_bytes(m), images="embed")["data"]["markdown"]
    check("review 2: picture referenced by cid shown once, in the text",
          md2.count("![image001.png](") == 1 and "## Attachment: image001.png" not in md2, md2[:400])
    check("review 2: not in the Attachments line", "**Attachments:**" not in md2)

    # 3. an .eml part with an unknown charset still converts
    raw = (b"From: a@example.com\r\nTo: d@example.com\r\nSubject: odd\r\n"
           b"Content-Type: text/plain; charset=x-bogus\r\n\r\nhello caf\xe9\r\n")
    e = raises(convert, raw)
    check("review 3: unknown charset does not crash", e is None, repr(e))
    if e is None:
        check("review 3: text decoded (cp1252 fallback)", "hello café" in convert(raw).markdown)

    # 4. a subject with line breaks stays one title line
    m = make_mail.message("line one\r\nline two", make_mail.ALICE, make_mail.SENT, body="b")
    eq("review 4: subject on one line", convert(make_mail.msg_bytes(m)).markdown.split("\n")[0],
       "# line one line two")

    # 5. a picture named '..' is saved under a safe name
    import tempfile
    tmp = tempfile.mkdtemp(prefix="mail_dotdot_")
    m = make_mail.message("dd", make_mail.ALICE, make_mail.SENT, body="b",
                          attachments=[make_mail.attachment("..", scan, "image/png")])
    e = raises(pdf2md.scrape, make_mail.msg_bytes(m), images="save", image_dir=os.path.join(tmp, "imgs"))
    check("review 5: picture named '..' does not crash", e is None, repr(e))
    check("review 5: saved inside the image folder", os.path.isdir(os.path.join(tmp, "imgs")) and
          all(os.path.isfile(os.path.join(tmp, "imgs", f)) for f in os.listdir(os.path.join(tmp, "imgs"))))

    # 6. a hidden attachment that is not a picture is still an attachment
    m = make_mail.message("h", make_mail.ALICE, make_mail.SENT, body="b",
                          attachments=[make_mail.attachment("x.pdf", make_mail.pdf_bytes("Hidden", make_mail.ROWS,
                                                                                          pages=1), hidden=True)])
    md6 = pdf2md.scrape(make_mail.msg_bytes(m), images="none")["data"]["markdown"]
    check("review 6: hidden PDF listed and read", "**Attachments:** x.pdf" in md6 and
          "## Attachment: x.pdf\n\n### Hidden" in md6, md6)

    # 7. plain text is kept as written: no heading push-down
    m = make_mail.message("p", make_mail.ALICE, make_mail.SENT, body="# not a heading\r\n- item\r\n")
    check("review 7: plain '# ' line untouched", "\n\n# not a heading\n- item\n" in
          convert(make_mail.msg_bytes(m)).markdown)


def test_layout_tables():
    """Marketing emails lay pages out with tables; those are unwrapped, data tables stay
    (a real newsletter came out as one broken table, 2026-10-26)."""
    m = md("newsletter.eml")
    body = m.split("**Date:**", 1)[1]
    check("layout: headings come through as headings", "\n## Weekly digest\n" in body and
          "\n### Trend one\n" in body, body)
    check("layout: text is plain paragraphs, not table rows",
          "\n\nWelcome to issue #1, a roundup of three invented trends.\n\n" in body, body)
    check("layout: list kept", "- Hook: short\n- Editing: simple" in body)
    check("layout: data table kept (cells holding <p>, as Outlook writes them)",
          "| Metric | Value |\n| --- | --- |\n| Views | 1,200 |" in body, body)
    check("layout: two-column role=presentation table unwrapped", "Left column" in body and
          "| Left column" not in body)
    check("layout: no other table rows", [ln for ln in body.split("\n") if ln.startswith("|")] ==
          ["| Metric | Value |", "| --- | --- |", "| Views | 1,200 |"], body)
    check("layout: banner image kept", "![banner](https://img.example.com/banner.png)" in body)
    check("tracking pixels dropped (1x1, 0x0, display:none)", "track.example.com" not in body, body)
    eq("web pages keep Firecrawl's table handling",
       msg2md.html_to_markdown('<table><tr><td>a</td></tr></table>'), "|     |\n| --- |\n| a |")


def test_shift_headings():
    eq("headings pushed down, capped at 6", msg2md.shift_headings("# a\n## b\n##### c\n#no\ntext", 2),
       "### a\n#### b\n###### c\n#no\ntext")
    eq("code blocks untouched", msg2md.shift_headings("```\n# not a heading\n```\n# h", 1),
       "```\n# not a heading\n```\n## h")


def main():
    make_mail.build_all(SAMPLES)
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                import traceback
                check(f"{name} raised", False, traceback.format_exc() + repr(e))
    fails = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else detail))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} passed" + ("" if fails else " — all passed"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
