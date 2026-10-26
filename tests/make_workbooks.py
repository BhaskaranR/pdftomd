"""Synthetic workbooks (invented data) for comparing sheet2md with Firecrawl's converter (anydoc).
Written as raw SpreadsheetML so saved formula values, checkboxes and 1904 dates can be set
exactly. Deterministic: same bytes every run.
Run: python tests/make_workbooks.py tests/samples/workbooks"""
import os
import sys
import zipfile
from xml.sax.saxutils import escape

SML = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
BUILTIN_IDS = {"0": 1, "0.00": 2, "#,##0": 3, "#,##0.00": 4, "0%": 9, "0.00%": 10, "0.00E+00": 11,
               "# ?/?": 12, "mm-dd-yy": 14, "d-mmm-yy": 15, "h:mm": 20, "m/d/yy h:mm": 22, "[h]:mm:ss": 46, "@": 49}


def col_letters(c):
    s = ""
    c += 1
    while c:
        c, rem = divmod(c - 1, 26)
        s = chr(65 + rem) + s
    return s


def ref_rc(ref):
    i = next(k for k, ch in enumerate(ref) if ch.isdigit())
    col = 0
    for ch in ref[:i]:
        col = col * 26 + ord(ch) - 64
    return int(ref[i:]) - 1, col - 1


class Sheet:
    def __init__(self, name, cells=None, state="", hidden_rows=(), hidden_cols=(), merges=(),
                 checkboxes=()):
        self.name, self.cells, self.state = name, cells or {}, state
        self.hidden_rows, self.hidden_cols, self.merges = hidden_rows, hidden_cols, merges
        self.checkboxes = checkboxes      # [(col, row, checked or None, caption, hidden)]


def text(v):
    return escape(v, {'"': "&quot;"})


def build(path, sheets, date1904=False):
    shared, shared_ix = [], {}
    fmts, xfs = {}, [0]                   # format code -> numFmtId; cellXfs numFmtIds

    def sst(s):
        if s not in shared_ix:
            shared_ix[s] = len(shared)
            shared.append(f'<si><t xml:space="preserve">{text(s)}</t></si>')
        return shared_ix[s]

    def style(code):
        if code is None:
            return ""
        fid = BUILTIN_IDS.get(code) if isinstance(code, str) else code
        if fid is None:
            fid = fmts.setdefault(code, 164 + len(fmts))
        if fid not in xfs:
            xfs.append(fid)
        return f' s="{xfs.index(fid)}"'

    def cell(ref, v):
        fmt = None
        if isinstance(v, tuple) and v[0] == "fmt":
            _, v, fmt = v
        s = style(fmt)
        if isinstance(v, bool):
            return f'<c r="{ref}" t="b"{s}><v>{int(v)}</v></c>'
        if isinstance(v, (int, float)):
            return f'<c r="{ref}"{s}><v>{v!r}</v></c>'
        if isinstance(v, str):
            return f'<c r="{ref}" t="s"{s}><v>{sst(v)}</v></c>'
        kind = v[0]
        if kind == "f":                   # ("f", formula, saved value or None)
            _, f, saved = v
            if saved is None:
                return f'<c r="{ref}"{s}><f>{text(f)}</f></c>'
            if isinstance(saved, str):
                return f'<c r="{ref}" t="str"{s}><f>{text(f)}</f><v>{text(saved)}</v></c>'
            if isinstance(saved, bool):
                return f'<c r="{ref}" t="b"{s}><f>{text(f)}</f><v>{int(saved)}</v></c>'
            return f'<c r="{ref}"{s}><f>{text(f)}</f><v>{saved!r}</v></c>'
        if kind == "e":
            return f'<c r="{ref}" t="e"{s}><v>{text(v[1])}</v></c>'
        if kind == "is":
            return f'<c r="{ref}" t="inlineStr"{s}><is><t xml:space="preserve">{text(v[1])}</t></is></c>'
        if kind == "rich":
            runs = "".join(f'<r><t xml:space="preserve">{text(t)}</t></r>' for t in v[1])
            shared.append(f"<si>{runs}</si>")
            return f'<c r="{ref}" t="s"{s}><v>{len(shared) - 1}</v></c>'
        raise ValueError(v)

    files = {}
    for i, sh in enumerate(sheets, 1):
        rows = {}
        for ref, v in sh.cells.items():
            r, c = ref_rc(ref)
            rows.setdefault(r, []).append((c, ref, v))
        for r in sh.hidden_rows:
            rows.setdefault(r - 1, [])
        body = ""
        if sh.hidden_cols:
            body += "<cols>" + "".join(f'<col min="{c}" max="{c}" width="9" hidden="1"/>'
                                       for c in sh.hidden_cols) + "</cols>"
        body += "<sheetData>"
        for r in sorted(rows):
            hid = ' hidden="1"' if r + 1 in sh.hidden_rows else ""
            body += f'<row r="{r + 1}"{hid}>' + "".join(cell(ref, v) for _, ref, v in sorted(rows[r])) + "</row>"
        body += "</sheetData>"
        if sh.merges:
            body += f'<mergeCells count="{len(sh.merges)}">' + "".join(
                f'<mergeCell ref="{m}"/>' for m in sh.merges) + "</mergeCells>"
        if sh.checkboxes:
            body += '<legacyDrawing r:id="rId1"/>'
            shapes = ""
            for k, (c, r, checked, caption, hidden) in enumerate(sh.checkboxes):
                vis = ";visibility:hidden" if hidden else ""
                chk = "" if checked is None else f"<x:Checked>{int(checked)}</x:Checked>"
                shapes += (f'<v:shape id="_x0000_s{1025 + k}" type="#_x0000_t201" style="position:absolute{vis}">'
                           f'<v:textbox><div><font>{text(caption)}</font></div></v:textbox>'
                           f'<x:ClientData ObjectType="Checkbox"><x:Anchor>{c}, 5, {r}, 2, {c + 1}, 10, '
                           f'{r + 1}, 1</x:Anchor>{chk}</x:ClientData></v:shape>')
            files[f"xl/drawings/vmlDrawing{i}.vml"] = (
                '<xml xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office" '
                f'xmlns:x="urn:schemas-microsoft-com:office:excel">{shapes}</xml>')
            files[f"xl/worksheets/_rels/sheet{i}.xml.rels"] = (
                f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{PKG}"><Relationship Id="rId1" '
                f'Type="{R}/vmlDrawing" Target="../drawings/vmlDrawing{i}.vml"/></Relationships>')
        files[f"xl/worksheets/sheet{i}.xml"] = (
            f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{SML}" xmlns:r="{R}">{body}</worksheet>')

    wb_sheets = "".join(f'<sheet name="{text(sh.name)}" sheetId="{i}"'
                        + (f' state="{sh.state}"' if sh.state else "") + f' r:id="rId{i}"/>'
                        for i, sh in enumerate(sheets, 1))
    pr = '<workbookPr date1904="1"/>' if date1904 else "<workbookPr/>"
    files["xl/workbook.xml"] = (f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{SML}" xmlns:r="{R}">'
                                f'{pr}<sheets>{wb_sheets}</sheets></workbook>')
    n = len(sheets)
    files["xl/_rels/workbook.xml.rels"] = (
        f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{PKG}">'
        + "".join(f'<Relationship Id="rId{i}" Type="{R}/worksheet" Target="worksheets/sheet{i}.xml"/>'
                  for i in range(1, n + 1))
        + f'<Relationship Id="rId{n + 1}" Type="{R}/styles" Target="styles.xml"/>'
        + f'<Relationship Id="rId{n + 2}" Type="{R}/sharedStrings" Target="sharedStrings.xml"/></Relationships>')
    numfmts = "".join(f'<numFmt numFmtId="{fid}" formatCode="{text(code)}"/>' for code, fid in fmts.items())
    files["xl/styles.xml"] = (
        f'<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="{SML}">'
        + (f'<numFmts count="{len(fmts)}">{numfmts}</numFmts>' if fmts else "")
        + '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        + '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
        + '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        + '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        + f'<cellXfs count="{len(xfs)}">'
        + "".join(f'<xf numFmtId="{fid}" fontId="0" fillId="0" borderId="0" xfId="0"'
                  + (' applyNumberFormat="1"' if fid else "") + "/>" for fid in xfs)
        + "</cellXfs></styleSheet>")
    files["xl/sharedStrings.xml"] = (f'<?xml version="1.0" encoding="UTF-8"?><sst xmlns="{SML}" '
                                     f'count="{len(shared)}" uniqueCount="{len(shared)}">{"".join(shared)}</sst>')
    files["_rels/.rels"] = (f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{PKG}"><Relationship '
                            f'Id="rId1" Type="{R}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    sheet_ct = "application/vnd.openxmlformats-officedocument.spreadsheetml"
    files["[Content_Types].xml"] = (
        f'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="{CT}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="vml" ContentType="application/vnd.openxmlformats-officedocument.vmlDrawing"/>'
        f'<Override PartName="/xl/workbook.xml" ContentType="{sheet_ct}.sheet.main+xml"/>'
        + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="{sheet_ct}.worksheet+xml"/>'
                  for i in range(1, n + 1))
        + f'<Override PartName="/xl/styles.xml" ContentType="{sheet_ct}.styles+xml"/>'
        f'<Override PartName="/xl/sharedStrings.xml" ContentType="{sheet_ct}.sharedStrings+xml"/></Types>')

    order = ["[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels"]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name in order + sorted(k for k in files if k not in order):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, files[name])


def fmt(v, code):
    return ("fmt", v, code)


WORKBOOKS = {
    # Number formats: built-in and custom, sections, conditions, scaling, fractions, text.
    "formats": [Sheet("Formats", {
        "A1": "Format", "B1": "Value",
        "A2": "currency", "B2": fmt(1234.5, '"$"#,##0.00'),
        "A3": "accounting", "B3": fmt(-20, '_("$"* #,##0.00_);_("$"* \\(#,##0.00\\);_("$"* "-"??_);_(@_)'),
        "A4": "accounting zero", "B4": fmt(0, '_("$"* #,##0.00_);_("$"* \\(#,##0.00\\);_("$"* "-"??_);_(@_)'),
        "A5": "percent", "B5": fmt(0.075, "0.0%"),
        "A6": "thousands", "B6": fmt(9876543.21, "#,##0"),
        "A7": "negative parens", "B7": fmt(-1234.5, "#,##0.00;(#,##0.00)"),
        "A8": "red negative", "B8": fmt(-42, "#,##0;[Red](#,##0)"),
        "A9": "scientific", "B9": fmt(12345.678, "0.00E+00"),
        "A10": "engineering", "B10": fmt(0.0000123, "##0.0E+0"),
        "A11": "fraction", "B11": fmt(5.25, "# ?/?"),
        "A12": "eighths", "B12": fmt(2.375, "# ?/8"),
        "A13": "millions", "B13": fmt(12345678, '0.0,," M"'),
        "A14": "condition", "B14": fmt(250, "[>=100]0.0;0.00"),
        "A15": "euro locale", "B15": fmt(99.5, "[$€-407]#,##0.00"),
        "A16": "text section", "B16": fmt("abc", '0;-0;0;"<"@">"'),
        "A17": "zero padded", "B17": fmt(42, "00000"),
        "A18": "general", "B18": 3554.7000000000003,
        "A19": "tiny", "B19": 0.0000004,
        "A20": "big", "B20": 123456789012345678,
        "A21": "unit", "B21": fmt(12.5, '0.0" kg"'),
        "A22": "hidden value", "B22": fmt(7, ";;;"),
        "A23": "general kg", "B23": fmt(3.5, 'General" kg"'),
        "A24": "negative percent", "B24": fmt(-0.5, "0.00%"),
    })],
    # Dates, times and durations in the 1900 system, including the fake leap day.
    "dates": [Sheet("Dates", {
        "A1": "Kind", "B1": "Value",
        "A2": "iso", "B2": fmt(46096, "yyyy-mm-dd"),
        "A3": "us", "B3": fmt(46096, "mm/dd/yyyy"),
        "A4": "builtin 14", "B4": fmt(46096, "mm-dd-yy"),
        "A5": "with time", "B5": fmt(46096.375, "m/d/yy h:mm"),
        "A6": "midnight", "B6": fmt(46096.0, "m/d/yy h:mm"),
        "A7": "time only", "B7": fmt(0.6041666666666666, "h:mm"),
        "A8": "am/pm", "B8": fmt(0.75, "h:mm AM/PM"),
        "A9": "duration", "B9": fmt(1.1043402777777778, "[h]:mm:ss"),
        "A10": "month name", "B10": fmt(46096, "d mmm yyyy"),
        "A11": "serial 60", "B11": fmt(60, "yyyy-mm-dd"),
        "A12": "serial 59", "B12": fmt(59, "yyyy-mm-dd"),
        "A13": "typed date", "B13": ("d" if False else ("is", "2026-03-15")),
    })],
    "dates_1904": [Sheet("Dates1904", {"A1": "When", "B1": "n", "A2": fmt(100, "yyyy-mm-dd"), "B2": 1,
                                       "A3": fmt(45000, "yyyy-mm-dd"), "B3": 2})],
    # Several sheets: visible, hidden, very hidden, empty, awkward names.
    "multi_sheet": [
        Sheet("Q1 | Sales", {"A1": "Region", "B1": "Units", "A2": "North", "B2": 120, "A3": "South", "B3": 80}),
        Sheet("Hidden", {"A1": "secret"}, state="hidden"),
        Sheet("VeryHidden", {"A1": "very secret"}, state="veryHidden"),
        Sheet("Empty"),
        Sheet("R&D *notes*", {"B3": "only cell"}),
        Sheet("Totals", {"A1": "Total", "B1": ("f", "SUM('Q1 | Sales'!B2:B3)", 200)}),
    ],
    # Hidden rows and columns, merges across them, title merged across the header.
    "merges_hidden": [Sheet("Layout", {
        "A1": "Branch report", "A2": "Branch", "B2": "Internal", "C2": "Loans", "D2": "Deposits",
        "A3": "North", "B3": "x", "C3": 10, "D3": 20,
        "A4": "Old branch", "B4": "x", "C4": 1, "D4": 2,
        "A5": "South", "B5": "y", "C5": 30, "D5": 40,
        "A6": "Group A", "C6": 5, "D6": 6, "C7": 7, "D7": 8,
    }, hidden_rows=(4,), hidden_cols=(2,), merges=("A1:D1", "A6:A7"))],
    # Header guess: labels over numbers, all text, numeric first row, index column.
    "headers": [
        Sheet("Labels", {"A1": "name", "B1": "qty", "A2": "pen", "B2": 3, "A3": "ink", "B3": 5}),
        Sheet("AllText", {"A1": "code", "B1": "label", "A2": "a1", "B2": "alpha", "A3": "b2", "B3": "beta"}),
        Sheet("Numeric", {"A1": 36, "B1": 12, "A2": 173, "B2": 57, "A3": 306, "B3": 220}),
        Sheet("Index", {"B1": "qty", "A2": "x", "B2": 1, "A3": "y", "B3": 2}),
        Sheet("Repeat", {"A1": "x", "B1": "aka", "A2": "y", "B2": "aka", "A3": "z", "B3": "aka"}),
        # A text column repeating its label next to a number column: anydoc 0.2.4 reads no header
        # (the repeat outvotes); unreleased anydoc reads one. Firecrawl runs 0.2.4.
        Sheet("RepeatTyped", {"A1": "region", "B1": "qty", "A2": "region", "B2": 1, "A3": "north", "B3": 2}),
    ],
    # Markdown-sensitive and unusual text.
    "text": [Sheet("Text", {
        "A1": "Case", "B1": "Text",
        "A2": "pipe", "B2": "a | b",
        "A3": "tag", "B3": "<b>bold</b>",
        "A4": "entity", "B4": "&amp; and &#169;",
        "A5": "stars", "B5": "*emphasis* and **strong**",
        "A6": "underscores", "B6": "snake_case and _x_",
        "A7": "backslash", "B7": "C:\\path\\file",
        "A8": "ticks", "B8": "use `code` here",
        "A9": "brackets", "B9": "[link](http://x.y)",
        "A10": "dollars", "B10": "$5 and $x$",
        "A11": "newline", "B11": "line one\nline two",
        "A12": "padding", "B12": "   padded   ",
        "A13": "nbsp", "B13": "no\u00a0break",
        "A14": "emoji", "B14": "ok \U0001F44D",
        "A15": "rtl", "B15": "مرحبا",
        "A16": "hash", "B16": "# not a heading",
        "A17": "tilde", "B17": "~strike~",
        "A18": "rich", "B18": ("rich", ["bo", "ld"]),
        "A19": "inline", "B19": ("is", "inline string"),
        "A20": "leading dash", "B20": "- item",
    })],
    # Formulas with and without saved values, plus value types.
    "formulas": [Sheet("Calc", {
        "A1": "Item", "B1": "Amount",
        "A2": "a", "B2": 10, "A3": "b", "B3": 20,
        "A4": "sum", "B4": ("f", "SUM(B2:B3)", 30),
        "A5": "text", "B5": ("f", 'CONCAT("x","y")', "xy"),
        "A6": "bool", "B6": ("f", "B2>B3", False),
        "A7": "error", "B7": ("e", "#DIV/0!"),
        "A8": "unsaved", "B8": ("f", "B2*2", None),
        "A9": "true", "B9": True,
        "A10": "n/a", "B10": ("e", "#N/A"),
    })],
    # Data far from A1, trailing blank rows, 200 rows.
    "sparse_long": [Sheet("Data", {
        "D10": "Id", "E10": "Score",
        **{f"D{r}": r - 10 for r in range(11, 211)},
        **{f"E{r}": fmt((r * 37 % 100) / 10, "0.0") for r in range(11, 211)},
        "D215": ("is", " "),
    })],
    "one_cell": [Sheet("Only", {"C3": "lonely"})],
    # Form-control checkboxes: checked with caption, unchecked bare, hidden, mixed state.
    "checkboxes": [Sheet("Checks", {"A1": "Task", "B1": "Done", "A2": "audit", "A3": "review"},
                         checkboxes=[(1, 1, True, "Yes", False), (1, 2, None, "", False),
                                     (2, 1, True, "ghost", True), (2, 2, False, "No", False)])],
}


CSVS = {
    "comma": b"Name,Price\niPhone,1000\niPad,800\n",
    "semicolon": b'a;b;c\n"1,5";"2,5";x\n"3,0";y;z\n',
    "tab": b"code\tqty\nA1\t3\nB2\t4\n",
    "pipe": b"x|y\n1|2\n3|4\n",
    "quoted_multiline": b'id,note\n1,"two\nlines"\n2,"say ""hi"""\n',
    "ragged_blank_lines": b"a,b\n\n1\n2,3,4\n",
    "padding": b'k,v\n"  padded  ",x\n',
    "utf16": b"\xff\xfe" + "x,y\ncaf\u00e9,90\n".encode("utf-16-le"),
    "cp1252": b"x,y\ncaf\xe9,90\n",
    "utf8_bom": b"\xef\xbb\xbfname,qty\npen,3\n",
    "markdown_chars": b"col,text\n1,a | b\n2,<i>x</i>\n3,*s*\n",
    "crlf": b"a,b\r\n1,2\r\n3,4\r\n",
    "single_column": b"only\none\ntwo\n",
    "trailing_empty": b"a,b\n1,2\n,\n , \n",
}


def main(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for name, sheets in WORKBOOKS.items():
        build(os.path.join(out_dir, name + ".xlsx"), sheets, date1904=name.endswith("1904"))
    for name, data in CSVS.items():
        with open(os.path.join(out_dir, name + ".csv"), "wb") as f:
            f.write(data)
    print(f"{len(WORKBOOKS)} workbooks and {len(CSVS)} CSV files in {out_dir}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "samples", "workbooks"))
