"""Spreadsheet tests: workbook/CSV -> Markdown must match Firecrawl (anydoc) exactly.
Cases are ported from anydoc's own tests (MIT); workbooks are built here with invented data.
Run: python tests/test_sheet.py            (exit code 0 = all passed)"""
import io
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import pdf2md  # noqa: E402
from pdf2md import NumberFormat, SheetError, render_serial  # noqa: E402
from pdf2md import convert_sheet as convert  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def eq(name, got, want):
    check(name, got == want, f"\n  got:  {got!r}\n  want: {want!r}")


SML = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
WS_REL = R + "/worksheet"
STYLES_REL = R + "/styles"
SST_REL = R + "/sharedStrings"
VML_REL = R + "/vmlDrawing"


def workbook(sheets, styles=None, shared=None, date1904=False, extra=()):
    """sheets: [(name, state, worksheet body)]. Returns xlsx bytes."""
    wb_sheets, rels = "", ""
    for i, (name, state, _) in enumerate(sheets, 1):
        st = f' state="{state}"' if state else ""
        wb_sheets += f'<sheet name="{name}" sheetId="{i}"{st} r:id="rId{i}"/>'
        rels += f'<Relationship Id="rId{i}" Type="{WS_REL}" Target="worksheets/sheet{i}.xml"/>'
    if styles is not None:
        rels += f'<Relationship Id="rId90" Type="{STYLES_REL}" Target="styles.xml"/>'
    if shared is not None:
        rels += f'<Relationship Id="rId91" Type="{SST_REL}" Target="sharedStrings.xml"/>'
    pr = '<workbookPr date1904="1"/>' if date1904 else ""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", f'<?xml version="1.0"?><workbook xmlns="{SML}" '
                   f'xmlns:r="{R}">{pr}<sheets>{wb_sheets}</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   f'<?xml version="1.0"?><Relationships xmlns="{PKG_RELS}">{rels}</Relationships>')
        for i, (_, _, body) in enumerate(sheets, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml",
                       f'<?xml version="1.0"?><worksheet xmlns="{SML}" xmlns:r="{R}">{body}</worksheet>')
        if styles is not None:
            z.writestr("xl/styles.xml", f'<?xml version="1.0"?><styleSheet xmlns="{SML}">{styles}</styleSheet>')
        if shared is not None:
            z.writestr("xl/sharedStrings.xml", f'<?xml version="1.0"?><sst xmlns="{SML}">{shared}</sst>')
        for name, body in extra:
            z.writestr(name, body)
    return buf.getvalue()


def one_sheet(body, **kw):
    return workbook([("S", "", body)], **kw)


def md(data, **kw):
    return convert(data, **kw).markdown


def inline(ref, text):
    return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'


def err_code(fn):
    try:
        fn()
    except SheetError as e:
        return e.code
    return None


# ---------------------------------------------------------------- golden: Firecrawl's sample.xlsx
# Same cells and formats as firecrawl/apps/api/samples/sample.xlsx; the expected string is the
# one asserted in firecrawl's document-converter.test.ts.
def golden():
    shared = "".join(f"<si><t>{s}</t></si>" for s in [
        "sample file", "test", "Name", "Price", "iPhone", "iPad", "Macbook",
        "other tab", "ChatGPT", "Claude", "Perplexity"])
    s = lambda ref, i: f'<c r="{ref}" t="s"><v>{i}</v></c>'  # noqa: E731
    n = lambda ref, v, st="": f'<c r="{ref}"{st}><v>{v}</v></c>'  # noqa: E731
    sheet1 = ("<sheetData>"
              f'<row r="2">{s("B2", 0)}{s("C2", 1)}</row>'
              f'<row r="4">{s("B4", 2)}{s("C4", 3)}</row>'
              f'<row r="5">{s("B5", 4)}{n("C5", 1000)}</row>'
              f'<row r="6">{s("B6", 5)}{n("C6", 800)}</row>'
              f'<row r="7">{s("B7", 6)}{n("C7", 1200)}</row></sheetData>')
    cur = ' s="1"'
    sheet2 = ("<sheetData>"
              f'<row r="2">{s("B2", 7)}</row>'
              f'<row r="4">{s("B4", 2)}{s("C4", 3)}</row>'
              f'<row r="5">{s("B5", 8)}{n("C5", 20, cur)}</row>'
              f'<row r="6">{s("B6", 9)}{n("C6", 17, cur)}</row>'
              f'<row r="7">{s("B7", 10)}{n("C7", 20, cur)}</row></sheetData>')
    styles = ('<numFmts count="1"><numFmt numFmtId="44" formatCode="_(&quot;$&quot;* #,##0.00_);'
              '_(&quot;$&quot;* \\(#,##0.00\\);_(&quot;$&quot;* &quot;-&quot;??_);_(@_)"/></numFmts>'
              '<cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="44"/></cellXfs>')
    data = workbook([("Sheet1", "", sheet1), ("Sheet2", "", sheet2)], styles=styles, shared=shared)
    want = ("## Sheet1\n\n| sample file | test |\n| --- | --- |\n|  |  |\n| Name | Price |\n"
            "| iPhone | 1000 |\n| iPad | 800 |\n| Macbook | 1200 |\n\n## Sheet2\n\n|  |  |\n"
            "| --- | --- |\n| other tab |  |\n|  |  |\n| Name | Price |\n| ChatGPT | $20.00 |\n"
            "| Claude | $17.00 |\n| Perplexity | $20.00 |\n")
    eq("golden: firecrawl sample.xlsx", md(data), want)
    r = convert(data)
    check("golden: no warning", r.warning is None, r.warning)
    eq("golden: sheet names", r.sheets, ["Sheet1", "Sheet2"])


# ---------------------------------------------------------------- number formats (numfmt.rs)
def fmt(code, v):
    f = NumberFormat.parse(code)
    if f is None:
        return f"<no parse {code!r}>"
    out = f.format_number(v)
    return out[1] if out[0] == "text" else f"<{out}>"


def numfmt():
    cases = [
        ("0.0%", 0.075, "7.5%"), ("0%", 0.155, "16%"), ("0.00%", -0.5, "-50.00%"),
        ("#,##0", 9876543.0, "9,876,543"), ("#,##0.00", 1234.5, "1,234.50"),
        ("#,##0", 0.0, "0"), ("#,##0", 999.0, "999"),
        ('"$"#,##0.00', 1234.5, "$1,234.50"), ("[$$-409]#,##0.00", 1234.5, "$1,234.50"),
        ('#,##0.00\\ "kr"', 1234.5, "1,234.50 kr"),
        ("00000", 42.0, "00042"), ("#", 0.0, ""), ("0.##", 5.0, "5"), ("0.??", 5.0, "5.  "),
        ("0.0#", 5.25, "5.25"), ("0.00", 5.255, "5.26"), ("???", 42.0, " 42"),
        ("0.", 5.0, "5."), ('0."kg"', 5.0, "5.kg"),
        ("0.00;(0.00)", -3.5, "(3.50)"), ("0.00;(0.00)", 3.5, "3.50"),
        ('0;-0;"zero"', 0.0, "zero"), ("0", -3.0, "-3"),
        ("#,##0;[Red](#,##0)", -1234.0, "(1,234)"),
        ("[>=100]0.0;0.00", 250.0, "250.0"), ("[>=100]0.0;0.00", 3.0, "3.00"),
        ("0.0,,", 12_345_678.0, "12.3"), ("#,##0,", 12_345_678.0, "12,346"),
        ("0.00E+00", 12345.0, "1.23E+04"), ("0.00E+00", 0.0001234, "1.23E-04"),
        ("##0.0E+0", 0.0000123, "12.3E-6"), ("0.00E+00", 0.0, "0.00E+00"),
        ("# ?/?", 5.25, "5 1/4"), ("# ??/??", 2.675, "2 27/40"), ("# ?/?", 5.0, "5"),
        ("?/?", 0.5, "1/2"), ("# ?/8", 5.25, "5 2/8"),
        ("?/" + "?" * 20, 0.5, "1/" + " " * 19 + "2"),
        ("0.00_);(0.00)", 3.5, "3.50 "), ("$* 0.00", 3.5, "$3.50"),
        ('0.0\\ "m/s"', 3.51, "3.5 m/s"), ('0"d"', 3.0, "3d"),
        ('"yes";"yes";"no"', 1.0, "yes"), ('"yes";"yes";"no"', 0.0, "no"),
        ("0", 2.5, "3"),
        ("?/8", 0.0625, "1/8"),  # 0.5 eighths rounds half away from zero, not to even
    ]
    for code, v, want in cases:
        eq(f"numfmt {code!r} {v}", fmt(code, v), want)

    def parts(code):
        out = NumberFormat.parse(code).format_number(45000.5)
        return out[1] if out[0] == "datetime" else out
    for code, want in [("yyyy-mm-dd", (True, False, False)), ("d mmm yyyy", (True, False, False)),
                       ("h:mm", (False, True, False)), ("yyyy-mm-dd hh:mm:ss", (True, True, False)),
                       ("[h]:mm:ss", (False, True, True)), ("[m]", (False, True, True)),
                       ("mm:ss", (False, True, False)), ("mm/dd/yyyy", (True, False, False)),
                       ("yyyy\\-mm\\-dd", (True, False, False)), ("h:mm AM/PM", (False, True, False))]:
        eq(f"date parts {code!r}", parts(code), want)

    eq("general keeps literals", NumberFormat.parse('"~"General" kg"').format_number(1234.5),
       ("general", 1234.5, "~", " kg"))
    eq("general;general negative", NumberFormat.parse("General;General").format_number(-3.5),
       ("general", 3.5, "", ""))
    for code in ["[DBNum1]0", "0.0.0", "abc0", "0;0;0;0;0", "€0.00", ""]:
        check(f"refuses {code!r}", NumberFormat.parse(code) is None)
    f = NumberFormat.parse('0.00;(0.00);"-";"* "@" *"')
    eq("text section", f.format_text("hi"), "* hi *")
    eq("@ text", NumberFormat.parse("@").format_text("hi"), "hi")
    eq("@ number", NumberFormat.parse("@").format_number(3.5), ("general", 3.5, "", ""))
    eq("no text section", NumberFormat.parse("0.00").format_text("hi"), None)


# ---------------------------------------------------------------- floats, serials
def values():
    for v, want in [(0.0000004, "0.0000004"), (12.0, "12"), (1.5, "1.5"),
                    (3554.7000000000003, "3554.7"), (5649.5599999999995, "5649.56"),
                    (346_289_529.491_800_1, "346289529.4918"), (1.0, "1"), (1e20, "100000000000000000000")]:
        eq(f"format_float {v}", pdf2md.format_float(v), want)
    eq("time of day", pdf2md.format_time_of_day(32_694.184 / 86_400.0), "09:04:54")
    eq("duration", pdf2md.format_duration_days((26 * 3600 + 30 * 60 + 15) / 86_400.0), "26:30:15")
    eq("negative duration", pdf2md.format_duration_days(-0.5), "-12:00:00")
    d = (True, False, False)
    eq("serial 59", render_serial(59.0, d, False), "1900-02-28")
    eq("serial 60 (fake leap day)", render_serial(60.0, d, False), "60")
    eq("serial 61", render_serial(61.0, d, False), "1900-03-01")
    eq("late serial 59", render_serial(59.9999999, d, False), "1900-02-28")
    eq("1904 serial 60", render_serial(60.0, d, True), "1904-03-01")
    eq("sub-day combined", render_serial(0.5, (True, True, False), False), "12:00:00")
    eq("sub-day date-only", render_serial(0.5, d, False), "0.5")


# ---------------------------------------------------------------- header guess (header.rs)
def header():
    def detect(rows):
        return pdf2md.header_rows([[[("text", t)] for t in r] for r in rows])
    eq("labels over numbers", detect([["name", "qty"], ["a", "1"], ["b", "2"]]), 1)
    eq("labels over date/bool", detect([["when", "ok"], ["2026-03-15", "TRUE"], ["2026-03-16", "FALSE"]]), 1)
    eq("numeric first row", detect([["36", "12", "aka"], ["173", "57", "aka"], ["306", "220", "aka"]]), 0)
    eq("label repeated below", detect([["x", "aka"], ["y", "aka"], ["z", "aka"]]), 0)
    # anydoc 0.2.4 (what Firecrawl runs): a repeated label is a data vote, cancelling the number column.
    eq("repeat vs typed column (0.2.4)", detect([["region", "qty"], ["region", "1"], ["north", "2"]]), 0)
    eq("all text short", detect([["col1", "col2"], ["naïve", "café"], ["Αθήνα", "数据"]]), 1)
    eq("all text long label", detect([["l" * 65, "col2"], ["a", "b"], ["c", "d"]]), 0)
    eq("title line", detect([["Q1 report"], ["a", "1"], ["b", "2"]]), 0)
    eq("unlabelled column", detect([["name", ""], ["a", "1"], ["b", "2"]]), 0)
    eq("duplicate labels", detect([["name", "name"], ["a", "1"], ["b", "2"]]), 0)
    eq("single row", detect([["name", "qty"]]), 0)
    eq("index column", detect([["", "qty"], ["a", "1"], ["b", "2"]]), 1)
    eq("mixed column abstains", detect([["kind", "value"], ["percent", "15.5%"], ["date", "2026-03-15"]]), 1)
    for v, want in [("1,234.56", "number"), ("-1.5e3", "number"), ("15.5%", "number"), ("-0-", "text"),
                    ("NaN", "text"), ("2026-03-15", "date"), ("15/03/2026 09:04", "date"),
                    ("2026-03-15T09:04:54.123Z", "date"), ("26:30:15", "date"), ("yes", "bool"),
                    ("  ", None)]:
        eq(f"classify {v!r}", pdf2md.classify_value(v), want)


# ---------------------------------------------------------------- workbook reading (xlsx.rs)
def xlsx():
    styles = ('<numFmts><numFmt numFmtId="164" formatCode="0.0%"/><numFmt numFmtId="165" '
              'formatCode="&quot;$&quot;#,##0.00"/><numFmt numFmtId="166" formatCode="mm/dd/yyyy"/>'
              '</numFmts><cellXfs><xf numFmtId="0"/><xf numFmtId="164"/><xf numFmtId="165"/>'
              '<xf numFmtId="166"/></cellXfs>')
    data = one_sheet('<sheetData><row r="1"><c r="A1" s="1"><v>0.075</v></c><c r="B1" s="2"><v>1234.5</v>'
                     '</c><c r="C1" s="3"><v>46096</v></c></row></sheetData>', styles=styles)
    eq("formats apply", md(data), "|  |  |  |\n| --- | --- | --- |\n| 7.5% | $1,234.50 | 2026-03-15 |\n")

    data = one_sheet('<sheetData><row r="1"><c r="A1" s="0"><v>1234.5</v></c><c r="B1" s="1"><v>1234.5</v>'
                     '</c></row></sheetData>', styles='<cellXfs><xf numFmtId="5"/><xf numFmtId="30"/></cellXfs>')
    eq("unresolvable ids -> General", md(data), "|  |  |\n| --- | --- |\n| 1234.5 | 1234.5 |\n")

    data = one_sheet('<sheetData><row r="1"><c r="A1" t="b"><v>1</v></c><c r="B1" t="e"><v>#DIV/0!</v></c>'
                     '<c r="C1" t="str"><v>=sum</v></c><c r="D1" t="d"><v>2026-03-15</v></c><c r="E1" '
                     't="inlineStr"><is><r><t>in</t></r><r><t>line</t></r></is></c></row></sheetData>')
    eq("value types", md(data),
       "|  |  |  |  |  |\n| --- | --- | --- | --- | --- |\n| TRUE | #DIV/0! | =sum | 2026-03-15 | inline |\n")

    data = one_sheet('<sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                     '</sheetData>', shared='<si><t>plain</t></si><si><r><t>ri</t></r><r><t>ch</t></r>'
                     '<rPh sb="0" eb="1"><t>ignored</t></rPh></si>')
    eq("shared + rich strings", md(data), "|  |  |\n| --- | --- |\n| plain | rich |\n")

    data = one_sheet('<sheetData><row r="1"><c r="A1" s="0"><v>100</v></c></row></sheetData>',
                     styles='<numFmts><numFmt numFmtId="164" formatCode="yyyy-mm-dd"/></numFmts>'
                     '<cellXfs><xf numFmtId="164"/></cellXfs>', date1904=True)
    eq("date1904", md(data), "|  |\n| --- |\n| 1904-04-10 |\n")

    data = one_sheet('<sheetData><row r="1">' + inline("F1", "wide") + '</row></sheetData>'
                     '<mergeCells count="1"><mergeCell ref="F1:O3"/></mergeCells>')
    blank = "|" + "  |" * 9
    eq("merge widens past populated area", md(data),
       "|" + "  |" * 10 + "\n|" + " --- |" * 10 + "\n| wide " + blank + "\n|" + "  |" * 10 + "\n|" + "  |" * 10 + "\n")

    def merged(ref):
        return one_sheet('<sheetData><row r="11">' + inline("D11", "x") + inline("E11", "y") + '</row>'
                         '<row r="12">' + inline("D12", "z") + inline("E12", "w") + '</row></sheetData>'
                         f'<mergeCells count="1"><mergeCell ref="{ref}"/></mergeCells>')
    eq("merge inside used range", md(merged("D11:E11")), "|  |  |\n| --- | --- |\n| x |  |\n| z | w |\n")
    eq("merge outside used columns ignored", md(merged("A1:B12")), "| x | y |\n| --- | --- |\n| z | w |\n")

    visible = ('<cols><col min="2" max="2" hidden="1"/></cols><sheetData><row r="1">' + inline("A1", "a")
               + inline("B1", "hidden col") + inline("C1", "c") + '</row><row r="2" hidden="1">'
               + inline("A2", "hidden row") + '</row><row r="3">' + inline("A3", "d") + '</row></sheetData>')
    secret = '<sheetData><row r="1">' + inline("A1", "secret") + '</row></sheetData>'
    data = workbook([("Shown", "", visible), ("Secret", "hidden", secret)])
    eq("hidden sheet/row/col omitted", md(data), "| a | c |\n| --- | --- |\n| d |  |\n")
    eq("includeHidden brings them back", md(data, include_hidden=True),
       "## Shown\n\n| a | hidden col | c |\n| --- | --- | --- |\n| hidden row |  |  |\n| d |  |  |\n\n"
       "## Secret\n\n|  |\n| --- |\n| secret |\n")

    data = one_sheet('<cols><col min="2" max="2" hidden="1"/></cols><sheetData><row r="1">' + inline("A1", "m")
                     + inline("D1", "x") + '</row></sheetData><mergeCells count="1"><mergeCell ref="A1:C1"/>'
                     '</mergeCells>')
    eq("merge remaps across hidden column", md(data), "|  |  |  |\n| --- | --- | --- |\n| m |  | x |\n")

    data = one_sheet('<sheetData><row r="1" hidden="1">' + inline("A1", "kept") + '</row><row r="2"/>'
                     '<row r="3">' + inline("B3", "x") + '</row></sheetData><mergeCells count="1">'
                     '<mergeCell ref="A1:A3"/></mergeCells>')
    eq("merge origin in hidden row keeps value", md(data), "|  |  |\n| --- | --- |\n| kept |  |\n|  | x |\n")

    data = one_sheet('<sheetData><row r="1">' + inline("A1", "a") + '</row><row r="1048576">'
                     + inline("XFD1048576", "b") + '</row></sheetData>')
    eq("grid budget: one sheet", err_code(lambda: convert(data)), "SPREADSHEET_TOO_LARGE")
    half = ('<sheetData><row r="1">' + inline("A1", "a") + '</row><row r="150000">' + inline("P150000", "b")
            + '</row></sheetData>')
    data = workbook([("A", "", half), ("B", "", half)])
    eq("grid budget: across sheets", err_code(lambda: convert(data)), "SPREADSHEET_TOO_LARGE")

    vml = """<xml xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:x="urn:schemas-microsoft-com:office:excel">
        <v:shape id="s1" type="#t201" style="position:absolute;margin-left:1pt">
          <v:textbox><div style="text-align:left"><font face="Tahoma">Roof</font></div></v:textbox>
          <x:ClientData ObjectType="Checkbox"><x:Anchor>1, 5, 0, 2, 2, 10, 1, 1</x:Anchor><x:Checked>1</x:Checked></x:ClientData>
        </v:shape>
        <v:shape id="s2" type="#t201" style="position:absolute">
          <v:textbox><div><font></font></div></v:textbox>
          <x:ClientData ObjectType="Checkbox"><x:Anchor>2, 5, 0, 2, 3, 10, 1, 1</x:Anchor></x:ClientData>
        </v:shape>
        <v:shape id="s3" type="#t201" style="position:absolute;visibility:hidden">
          <x:ClientData ObjectType="Checkbox"><x:Anchor>3, 5, 0, 2, 4, 10, 1, 1</x:Anchor><x:Checked>1</x:Checked></x:ClientData>
        </v:shape>
        <v:shape id="s4" type="#t202" style="position:absolute">
          <v:textbox><div>a note</div></v:textbox>
          <x:ClientData ObjectType="Note"><x:Anchor>4, 5, 0, 2, 5, 10, 1, 1</x:Anchor></x:ClientData>
        </v:shape></xml>"""
    rels = (f'<?xml version="1.0"?><Relationships xmlns="{PKG_RELS}"><Relationship Id="rId1" '
            f'Type="{VML_REL}" Target="../drawings/vmlDrawing1.vml"/></Relationships>')
    data = workbook([("S", "", '<sheetData><row r="1"><c r="A1" t="str"><v>14</v></c><c r="B1" t="str">'
                                '<v>L/R</v></c></row></sheetData><legacyDrawing r:id="rId1"/>')],
                    extra=[("xl/worksheets/_rels/sheet1.xml.rels", rels), ("xl/drawings/vmlDrawing1.vml", vml)])
    eq("checkboxes land in anchor cell", md(data), "|  |  |  |\n| --- | --- | --- |\n| 14 | L/R [x] Roof | [ ] |\n")


# ---------------------------------------------------------------- sheet rules + our additions
def sheets():
    a = '<sheetData><row r="1">' + inline("A1", "only") + '</row></sheetData>'
    eq("single sheet: no heading", md(workbook([("Solo", "", a)])), "|  |\n| --- |\n| only |\n")
    eq("empty sheet skipped, others keep heading", md(workbook([("Empty", "", "<sheetData/>"), ("Full", "", a)])),
       "## Full\n\n|  |\n| --- |\n| only |\n")
    eq("all empty -> empty markdown", md(workbook([("E", "", "<sheetData/>")])), "")

    data = one_sheet('<sheetData><row r="1">' + inline("A1", "item") + inline("B1", "total") + '</row>'
                     '<row r="2">' + inline("A2", "x") + '<c r="B2"><f>SUM(1,2)</f></c></row>'
                     '<row r="3">' + inline("A3", "y") + '<c r="B3"><f>SUM(2,2)</f><v>4</v></c></row></sheetData>')
    r = convert(data)
    eq("formula w/o saved value is blank", r.markdown, "| item | total |\n| --- | --- |\n| x |  |\n| y | 4 |\n")
    check("formula w/o saved value warns", r.warning and r.warning.startswith("1 formula cell"), r.warning)

    data = one_sheet('<sheetData><row r="1">' + inline("A1", "a|b") + inline("B1", "&lt;b&gt;")
                     + inline("C1", "&amp;amp;") + inline("D1", "  padded  ") + inline("E1", "1 &amp; 2")
                     + '</row></sheetData>')
    eq("cell escaping + trim", md(data),
       "|  |  |  |  |  |\n| --- | --- | --- | --- | --- |\n| a\\|b | \\<b> | &amp;amp; | padded | 1 & 2 |\n")

    data = one_sheet('<sheetData><row r="1">' + inline("A1", "two&#10;lines") + inline("B1", "no&#160;break")
                     + inline("C1", "soft&#173;hyphen") + '</row></sheetData>')
    eq("cell text cleaned", md(data), "|  |  |  |\n| --- | --- | --- |\n| two lines | no break | softhyphen |\n")

    data = one_sheet('<sheetData><row r="1">' + inline("A1", "h") + '</row><row r="2">' + inline("A2", " ")
                     + '</row></sheetData>')
    eq("trailing blank rows dropped", md(data), "|  |\n| --- |\n| h |\n")

    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64 + "EncryptionInfo".encode("utf-16-le") + b"\0" * 64
    eq("encrypted workbook", err_code(lambda: convert(ole, ext=".xlsx")), "SPREADSHEET_ENCRYPTED")
    eq("not a spreadsheet", err_code(lambda: convert(b"hello", ext=".xlsx")), "SPREADSHEET_UNREADABLE")


# ---------------------------------------------------------------- CSV (csv.rs)
def csv_tests():
    eq("sniff ;", pdf2md.sniff_delimiter('a;b;c\n"1,5";"2,5";x\n"3,0";y;z\n'), ";")
    eq("sniff ,", pdf2md.sniff_delimiter("a,b,c\n1,2,3\n"), ",")
    eq("sniff tab", pdf2md.sniff_delimiter("a\tb\n1\t2\n"), "\t")
    long_field = '"' + "\n".join(["line"] * 30) + '"'
    eq("sniff multiline field", pdf2md.sniff_delimiter(f"a;b;c\n{long_field};2;3\nx;y;z\n"), ";")
    eq("csv basic", md(b"Name,Price\niPhone,1000\niPad,800\n", ext=".csv"),
       "| Name | Price |\n| --- | --- |\n| iPhone | 1000 |\n| iPad | 800 |\n")
    eq("csv utf-16", md(b"\xff\xfe" + "x,y\ncafé,90\n".encode("utf-16-le"), ext=".csv"),
       "| x | y |\n| --- | --- |\n| café | 90 |\n")
    eq("csv cp1252", md(b"x,y\ncaf\xe9,90\n", ext=".csv"), "| x | y |\n| --- | --- |\n| café | 90 |\n")
    eq("csv ragged + blank lines", md(b"a,b\n\n1\n2,3,4\n", ext=".csv"),
       "|  |  |  |\n| --- | --- | --- |\n| a | b |  |\n| 1 |  |  |\n| 2 | 3 | 4 |\n")


# ---------------------------------------------------------------- scrape() / CLI routing
def routing():
    import base64
    import contextlib
    import tempfile
    import pdf2md

    ext = pdf2md.spreadsheet_ext
    eq("detect: url extension", ext("https://x.com/files/q3.XLSX?dl=1"), ".xlsx")
    eq("detect: extension mid-path", ext("https://x.com/q3.csv/download"), ".csv")
    eq("detect: content-type", ext("https://x.com/export", "text/csv; charset=utf-8"), ".csv")
    eq("detect: xls content-type", ext("https://x.com/export", "application/vnd.ms-excel"), ".xls")
    a = '<sheetData><row r="1">' + inline("A1", "item") + inline("B1", "qty") + '</row><row r="2">' \
        + inline("A2", "pen") + '<c r="B2"><v>3</v></c></row></sheetData>'
    xl = workbook([("Stock", "", a)])
    eq("detect: workbook bytes", ext("download", None, xl), ".xlsx")
    eq("detect: pdf is not a sheet", ext("download", "application/pdf", b"%PDF-1.7"), None)

    table = "| item | qty |\n| --- | --- |\n| pen | 3 |\n"
    r = pdf2md.scrape(xl)
    eq("scrape: xlsx bytes -> markdown", r["data"].get("markdown"), table)
    m = r["data"]["metadata"]
    eq("scrape: metadata", (m.get("contentType"), m.get("numSheets"), m.get("sheetNames")),
       (pdf2md.CONTENT_TYPE_OF[".xlsx"], 1, ["Stock"]))
    check("scrape: no pdf fields", "pdfType" not in m, m)
    raw = pdf2md.scrape(xl, parsers=[])
    eq("scrape: parsers [] -> base64", base64.b64decode(raw["data"].get("base64", "")), xl)
    eq("scrape: pdf-only parsers still convert", pdf2md.scrape(
        xl, parsers=[{"type": "pdf", "mode": "fast", "maxPages": 1, "pages": True, "blocks": True}])
       ["data"].get("markdown"), table)
    hidden = workbook([("Stock", "", a), ("Old", "hidden", a)])
    eq("scrape: hidden sheet dropped", pdf2md.scrape(hidden)["data"]["markdown"], table)
    check("scrape: include_hidden", pdf2md.scrape(hidden, include_hidden=True)["data"]["markdown"]
          .count("## ") == 2)
    unsaved = one_sheet('<sheetData><row r="1">' + inline("A1", "t") + '<c r="B1"><f>1+1</f></c>'
                        '</row></sheetData>')
    check("scrape: formula warning in data.warning",
          "no saved value" in (pdf2md.scrape(unsaved)["data"].get("warning") or ""))
    eq("scrape: encrypted -> SheetError", err_code(lambda: pdf2md.scrape(
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + "EncryptionInfo".encode("utf-16-le"),
        filename="locked.xlsx")), "SPREADSHEET_ENCRYPTED")

    tmp = tempfile.mkdtemp(prefix="sheets_")
    csv_path = os.path.join(tmp, "stock.csv")
    with open(csv_path, "wb") as f:
        f.write(b"item,qty\npen,3\n")
    eq("scrape: csv path", pdf2md.scrape(csv_path)["data"]["markdown"], table)
    xl_path = os.path.join(tmp, "stock.xlsx")
    with open(xl_path, "wb") as f:
        f.write(xl)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        pdf2md.main([xl_path])
    eq("cli: pdf2md.py file.xlsx", out.getvalue(), table)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        pdf2md.main([os.path.join(tmp, "stock.xlsx"), "--include-hidden", "--json"])
    check("cli: --json response", '"numSheets": 1' in out.getvalue(), out.getvalue()[:200])


# ---------------------------------------------------------------- charts (opt-in; not in Firecrawl)
CHARTS_MD = """## Loans

|  |  |  |
| --- | --- | --- |
| Branch | 2025 | 2026 |
| North | 10 | 12 |
| South | 8 | 9 |

**Chart: Growth rate** (line)

|  | growth |
| --- | --- |
| North | 10.5% |
| South | 20.0% |

**Chart: Loans by branch** (column)

| Branch | 2025 | 2026 |
| --- | --- | --- |
| North | 10 | 12 |
| South | 8 | 9 |

## Mix

**Chart: Share** (pie)

|  | Share |
| --- | --- |
| A | 50% |
| B | 30% |
| C | 20% |

**Chart 2** (column + line)

|  | Units | Rate |
| --- | --- | --- |
| Q1 | 5 | 0.1 |
| Q2 | 7 | 0.2 |
| Q3 |  | 0.3 |

**Chart: Fit** (scatter)

| X | Y1 | Y2 |
| --- | --- | --- |
| 1 | 2 | 1 |
| 2 | 4 |  |
| 3 | 6 | 9 |

**Chart: Regions** (column)

|  | Loans |
| --- | --- |
| North / Branch A | 1 |
| North / Branch B | 2 |
| South / Branch C | 3 |

**Chart: Stale** (column): no saved values

## Dashboard

**Chart: Totals** (column)

|  | Total |
| --- | --- |
| North | 22 |
| South | 17 |
"""


def charts_tests():
    import contextlib
    import tempfile
    sys.path.insert(0, HERE)
    import make_workbooks as mw
    import pdf2md
    tmp = tempfile.mkdtemp(prefix="charts_")
    path = os.path.join(tmp, "charts.xlsx")
    mw.build(path, mw.WORKBOOKS["charts"])
    with open(os.path.join(HERE, "out", "workbooks", "charts.xlsx.md"), encoding="utf-8") as f:
        eq("charts off: same as Firecrawl", md(path), f.read())
    r = convert(path, charts=True)
    eq("charts on: tables in sheet order", r.markdown, CHARTS_MD)
    eq("charts on: no-saved-values warning", r.warning, "chart 'Stale' on sheet 'Mix' has no saved values")
    eq("scrape charts=True", pdf2md.scrape(path, charts=True)["data"]["markdown"], CHARTS_MD)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        pdf2md.main([path, "--charts"])
    eq("cli --charts", out.getvalue(), CHARTS_MD)

    chart = {"at": (0, 3), "type": "bar", "title": "Hidden chart", "series": [
        {"name": "n", "cats": ["a"], "vals": [1]}]}
    hid = os.path.join(tmp, "hidden.xlsx")
    mw.build(hid, [mw.Sheet("Shown", {"A1": "x"}), mw.Sheet("Secret", {"A1": "y"}, state="hidden", charts=[chart])])
    check("hidden sheet's chart left out", "Hidden chart" not in convert(hid, charts=True).markdown)
    check("include_hidden keeps it", "**Chart: Hidden chart** (column)" in
          convert(hid, charts=True, include_hidden=True).markdown)
    golden_like = os.path.join(tmp, "plain.xlsx")
    mw.build(golden_like, mw.WORKBOOKS["formats"])
    eq("charts=True without charts changes nothing", md(golden_like, charts=True), md(golden_like))


# ---------------------------------------------------------------- synthetic set vs Firecrawl's output
def expected_outputs():
    """tests/out/workbooks/*.md is anydoc 0.2.4's output (tests/compare_anydoc.py --save)."""
    import tempfile
    sys.path.insert(0, HERE)
    import make_workbooks
    tmp = tempfile.mkdtemp(prefix="workbooks_")
    make_workbooks.main(tmp)
    out = os.path.join(HERE, "out", "workbooks")
    names = sorted(os.listdir(tmp))
    eq("expected outputs: one per generated file", sorted(n[:-3] for n in os.listdir(out)), names)
    for name in names:
        with open(os.path.join(out, name + ".md"), encoding="utf-8") as f:
            eq(f"matches anydoc: {name}", md(os.path.join(tmp, name)), f.read())


if __name__ == "__main__":
    for t in (golden, numfmt, values, header, xlsx, sheets, csv_tests, routing, expected_outputs, charts_tests):
        try:
            t()
        except Exception as e:  # a crash fails that group, not the run
            import traceback
            RESULTS.append((f"{t.__name__} crashed", False, traceback.format_exc()))
    failed = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"FAIL {name}{detail}")
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if not failed:
        print("all passed")
    sys.exit(1 if failed else 0)
