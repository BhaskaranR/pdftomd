"""Layout tests: side-by-side tables, vector charts, and things that must NOT change.
All documents are generated here with invented data.
Run: python tests/test_layout.py            (exit code 0 = all passed)"""
import math
import os
import re
import sys
import tempfile

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from pdf2md import convert  # noqa: E402
sys.path.insert(0, HERE)
from fonts import font  # noqa: E402

W, H = letter
OUT = tempfile.mkdtemp(prefix="pdf2md_layout_")
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def run(path):
    return convert(path, images="none")



def spans_ok(r):
    return all(r.markdown[slice(*it["markdownSpan"])] == it["content"]
               for pg in r.blocks for it in pg["items"] if it["markdownSpan"])


def figure_blocks(r):
    return [it for pg in r.blocks for it in pg["items"] if it["type"] == "figure"]


def table_rows(md):
    """Return markdown tables as lists of row-cell lists."""
    tables, cur = [], []
    for line in md.splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not all(re.fullmatch(r"-{3,}", c) for c in cells):
                cur.append(cells)
        elif cur:
            tables.append(cur)
            cur = []
    if cur:
        tables.append(cur)
    return tables


def find_table(md, header):
    for t in table_rows(md):
        if t and t[0][:len(header)] == header:
            return t
    return None


# ───────────────────────── document builders ─────────────────────────

def scorecard(path, right_row=None, bars_as_paths=False):
    """Two independent column groups on one row grid + framed stacked bar chart."""
    c = canvas.Canvas(path, pagesize=letter)
    ROW, TOP = 10.5, H - 150
    RROW = right_row or ROW

    def y_l(r):
        return TOP - r * ROW

    def y_r(r):
        return TOP - r * RROW

    def t(x, y, s, size=7.5, font="Helvetica", right=False):
        c.setFont(font, size)
        (c.drawRightString if right else c.drawString)(x, y, s)

    def rule(x0, x1, y):
        c.setLineWidth(1.2)
        c.line(x0, y - 2.5, x1, y - 2.5)

    def band(x0, x1, y, label):
        c.setFillColor(colors.HexColor("#DCE6F2"))
        c.rect(x0, y - 3, x1 - x0, 12.5, stroke=0, fill=1)
        c.setFillColor(colors.black)
        t(x0 + 2, y, label, size=11)

    c.setFont("Helvetica-Bold", 15); c.drawString(40, H - 70, "Northwind Securities")
    c.setFont("Helvetica-Bold", 7.5); c.drawString(40, H - 84, "Contoso Capital 2025 H1 Broker Vote")
    L0, L1 = 40, 300

    def small(start, head, rows):
        t(L0, y_l(start), head, 7); t(L0 + 120, y_l(start), "Rank", 7)
        t(L0 + 190, y_l(start), "Tier", 7); t(L0 + 215, y_l(start), "Voter Participation", 7)
        rule(L0, L1, y_l(start))
        for i, (a, b, cc, d) in enumerate(rows, 1):
            t(L0, y_l(start + i), a); t(L0 + 120, y_l(start + i), b)
            t(L0 + 192, y_l(start + i), cc); t(L0 + 230, y_l(start + i), d)

    band(L0, L0 + 150, y_l(0), "BREAKDOWN")
    small(1, "RESOURCE PROVIDER", [("Northwind Securities", "4 out of 50", "2", "17 out of 17")])
    small(5, "CATEGORIES", [("Corporate Access", "1 out of 28", "1", "9 out of 12"),
                            ("Research", "5 out of 42", "3", "10 out of 14"),
                            ("Sales", "2 out of 40", "1", "13 out of 13")])
    small(11, "DEPARTMENTS", [("Equity", "3 out of 44", "1", "11 out of 11"),
                              ("Credit", "3 out of 17", "3", "2 out of 2"),
                              ("Trading", "3 out of 30", "2", "3 out of 3"),
                              ("Overlay", "10 out of 12", "4", "1 out of 1")])
    small(17, "REGIONS", [("North America", "4 out of 49", "1", "15 out of 17"),
                          ("Europe", "5 out of 15", "3", "5 out of 12"),
                          ("Asia Pacific", "6 out of 11", "4", "2 out of 7")])

    band(L0, L0 + 200, y_l(23), "PEER ANALYSIS BY CATEGORY")
    fx0, fy0, fx1, fy1 = L0 + 5, y_l(40), L1 - 10, y_l(24) - 4
    c.setStrokeColor(colors.HexColor("#BBBBBB")); c.setLineWidth(0.5)
    c.rect(fx0, fy0, fx1 - fx0, fy1 - fy0, stroke=1, fill=0)
    c.setStrokeColor(colors.black)
    series = [("Corporate Access", "#4472C4"), ("Research", "#ED7D31"),
              ("Sales", "#A5A5A5"), ("Trading", "#FFC000")]
    bars = [("Provider #1", [10, 80, 20, 3]), ("Provider #2", [15, 45, 25, 5]),
            ("Provider #3", [15, 45, 22, 4]), ("Northwind", [20, 22, 25, 4]),
            ("Provider #5", [10, 15, 30, 4])]
    ax = fx0 + 55
    c.setLineWidth(0.6); c.line(ax, fy0 + 8, ax, fy1 - 6)
    for i, (label, vals) in enumerate(bars):
        by = fy1 - 22 - i * 20
        c.setFont("Helvetica", 5.5); c.drawRightString(ax - 4, by + 2, label)
        x = ax
        for (name, col), v in zip(series, vals):
            c.setFillColor(colors.HexColor(col))
            if bars_as_paths:       # five-point path -> pdfplumber "curve", not "rect"
                p = c.beginPath(); p.moveTo(x, by); p.lineTo(x + v * 1.15 / 2, by)
                p.lineTo(x + v * 1.15, by); p.lineTo(x + v * 1.15, by + 9); p.lineTo(x, by + 9)
                p.close(); c.drawPath(p, stroke=0, fill=1)
            else:
                c.rect(x, by, v * 1.15, 9, stroke=0, fill=1)
            x += v * 1.15
        c.setFillColor(colors.black)
    for i, (name, col) in enumerate(series):
        ly = fy0 + 60 - i * 11
        c.setFillColor(colors.HexColor(col)); c.rect(fx1 - 75, ly, 5, 5, stroke=0, fill=1)
        c.setFillColor(colors.black); c.setFont("Helvetica", 5.5); c.drawString(fx1 - 67, ly, name)

    R0, R1 = 320, 575
    band(R0, R0 + 160, y_r(0), "RESEARCH MENTIONS")
    for x, hd in ((R0, "NAME"), (R0 + 150, "VOTES"), (R0 + 185, "ANALYST"), (R0 + 225, "FEEDBACK")):
        t(x, y_r(1), hd, 7)
    rule(R0, R1, y_r(1))
    names = ["TOTAL", "Avery Stone", "Blake Morgan", "Casey Rivera", "Devon Patel", "Emery Chen",
             "Finley Brooks", "Harper Quinn", "Jordan Ellis", "Kai Navarro", "Logan Price"]
    votes = ["82", "8.8", "6.3", "5.6", "4.9", "4.3", "4.1", "3.8", "3.1", "3.1", "3.0"]
    for i, (nm, v) in enumerate(zip(names, votes), 2):
        t(R0, y_r(i), nm, font="Helvetica-Oblique" if nm == "TOTAL" else "Helvetica")
        t(R0 + 172, y_r(i), v, right=True)
    band(R0, R0 + 160, y_r(16), "SALES MENTIONS")
    for x, hd in ((R0, "NAME"), (R0 + 150, "VOTES"), (R0 + 185, "ANALYST"), (R0 + 225, "FEEDBACK")):
        t(x, y_r(17), hd, 7)
    rule(R0, R1, y_r(17))
    for i, (nm, v) in enumerate([("TOTAL", "122"), ("Jamie Ortiz", "56.6"),
                                 ("Riley Brennan", "33.3"), ("Skyler Moss", "21.3")], 18):
        t(R0, y_r(i), nm, font="Helvetica-Oblique" if nm == "TOTAL" else "Helvetica")
        t(R0 + 172, y_r(i), v, right=True)

    c.setFont("Helvetica", 9); c.drawString(40, 40, "Confidential (sample)")
    c.drawCentredString(W / 2, 48, "Northwind Securities")
    c.save()
    return path


def column_chart(path, data_labels=False):
    """Vertical clustered bars drawn as paths, numeric value axis, legend below."""
    c = canvas.Canvas(path, pagesize=letter)
    c.setFont("Helvetica-Bold", 14); c.drawString(72, H - 72, "Quarterly Volume")
    c.setFont("Helvetica", 10)
    c.drawString(72, H - 95, "The chart below shows volume by quarter for two desks.")
    x0, y0, scale = 120, 400, 2.0          # value 1 = 2pt
    c.setLineWidth(0.5)
    c.line(x0, y0, x0, y0 + 170); c.line(x0, y0, x0 + 300, y0)
    for v in (0, 20, 40, 60, 80):
        c.setFont("Helvetica", 7); c.drawRightString(x0 - 4, y0 + v * scale - 2, str(v))
    quarters = ["Q1", "Q2", "Q3", "Q4"]
    desk_a = [30, 45, 60, 52]
    desk_b = [25, 35, 40, 70]
    for i, q in enumerate(quarters):
        bx = x0 + 20 + i * 70
        for j, (vals, col) in enumerate(((desk_a, "#2F5597"), (desk_b, "#C55A11"))):
            hgt = vals[i] * scale
            c.setFillColor(colors.HexColor(col))
            p = c.beginPath(); p.moveTo(bx + j * 22, y0); p.lineTo(bx + j * 22 + 10, y0)
            p.lineTo(bx + j * 22 + 20, y0); p.lineTo(bx + j * 22 + 20, y0 + hgt)
            p.lineTo(bx + j * 22, y0 + hgt); p.close(); c.drawPath(p, stroke=0, fill=1)
            if data_labels:
                c.setFillColor(colors.black); c.setFont("Helvetica", 6)
                c.drawCentredString(bx + j * 22 + 10, y0 + hgt + 3, str(vals[i]))
        c.setFillColor(colors.black); c.setFont("Helvetica", 7)
        c.drawCentredString(bx + 21, y0 - 10, q)
    for j, (nm, col) in enumerate((("Desk A", "#2F5597"), ("Desk B", "#C55A11"))):
        c.setFillColor(colors.HexColor(col)); c.rect(170 + j * 80, y0 - 32, 6, 6, stroke=0, fill=1)
        c.setFillColor(colors.black); c.setFont("Helvetica", 7); c.drawString(179 + j * 80, y0 - 32, nm)
    c.setFont("Helvetica", 10)
    c.drawString(72, 300, "Volumes rose in every quarter except the third for Desk A.")
    c.save()
    return path


def pie_chart(path):
    c = canvas.Canvas(path, pagesize=letter)
    c.setFont("Helvetica-Bold", 14); c.drawString(72, H - 72, "Mix")
    start = 0
    for (nm, share, col), k in zip((("Equity", 50, "#4472C4"), ("Credit", 30, "#ED7D31"),
                                    ("Other", 20, "#A5A5A5")), range(3)):
        c.setFillColor(colors.HexColor(col))
        c.wedge(200, 400, 360, 560, start, share * 3.6, stroke=0, fill=1)
        start += share * 3.6
        c.rect(400, 520 - k * 14, 6, 6, stroke=0, fill=1)
        c.setFillColor(colors.black); c.setFont("Helvetica", 8); c.drawString(410, 520 - k * 14, nm)
    c.save()
    return path


def wide_table(path):
    """ONE table: full-width header rule, wide gap between two column groups."""
    c = canvas.Canvas(path, pagesize=letter)
    c.setFont("Helvetica-Bold", 12); c.drawString(72, H - 72, "Regional summary")
    cols = [72, 170, 230, 380, 440]
    heads = ["Region", "Q1", "Q2", "Q3", "Q4"]
    y = H - 110
    c.setFont("Helvetica-Bold", 8)
    for x, hd in zip(cols, heads):
        c.drawString(x, y, hd)
    c.setLineWidth(1); c.line(72, y - 3, 520, y - 3)
    rows = [("North", "12", "14", "15", "18"), ("South", "9", "11", "13", "12"),
            ("East", "7", "8", "10", "11"), ("West", "15", "16", "19", "21")]
    c.setFont("Helvetica", 8)
    for i, r in enumerate(rows, 1):
        for x, v in zip(cols, r):
            c.drawString(x, y - i * 12, v)
    c.save()
    return path


def cell_border_table(path):
    """ONE table drawn with per-cell border segments that abut each other."""
    c = canvas.Canvas(path, pagesize=letter)
    xs = [72, 200, 300, 400]
    y = H - 100
    data = [("Item", "Units", "Price", "Total"), ("Widget", "4", "2.50", "10.00"),
            ("Gadget", "2", "7.25", "14.50"), ("Gizmo", "1", "9.99", "9.99")]
    c.setLineWidth(0.8)
    for i, row in enumerate(data):
        yy = y - i * 14
        c.setFont("Helvetica-Bold" if i == 0 else "Helvetica", 8)
        for k, v in enumerate(row):
            c.drawString(xs[k] + 3, yy, v)
            right = xs[k + 1] if k + 1 < len(xs) else 500
            c.line(xs[k], yy - 4, right, yy - 4)       # cell bottom border segment
    c.save()
    return path


def financial(path):
    """Labels + two numeric columns, underline segments only under the numbers."""
    c = canvas.Canvas(path, pagesize=letter)
    c.setFont("Helvetica-Bold", 11); c.drawString(72, H - 72, "Statement of income")
    c.setFont("Helvetica-Bold", 8)
    c.drawRightString(400, H - 100, "2025"); c.drawRightString(480, H - 100, "2024")
    rows = [("Revenue", "1,240", "1,105"), ("Cost of sales", "(610)", "(560)"),
            ("Gross profit", "630", "545"), ("Operating expenses", "(300)", "(281)"),
            ("Operating income", "330", "264")]
    c.setFont("Helvetica", 8)
    for i, (lab, a, b) in enumerate(rows, 1):
        yy = H - 100 - i * 13
        c.drawString(72, yy, lab); c.drawRightString(400, yy, a); c.drawRightString(480, yy, b)
        if lab in ("Cost of sales", "Operating expenses"):
            c.setLineWidth(0.6)
            c.line(360, yy - 3, 400, yy - 3); c.line(440, yy - 3, 480, yy - 3)
    c.save()
    return path


def zebra_and_heatmap(path):
    c = canvas.Canvas(path, pagesize=letter)
    c.setFont("Helvetica-Bold", 11); c.drawString(72, H - 72, "Scores")
    shades = ["#F2F2F2", "#FFFFFF"]
    heat = ["#F8CBAD", "#C6EFCE", "#FFEB9C"]
    rows = [("Alpha", "71", "64", "80"), ("Beta", "55", "62", "48"), ("Gamma", "90", "85", "77"),
            ("Delta", "60", "66", "70")]
    c.setFont("Helvetica-Bold", 8)
    for x, hd in zip((72, 200, 260, 320), ("Name", "Q1", "Q2", "Q3")):
        c.drawString(x, H - 100, hd)
    for i, r in enumerate(rows, 1):
        yy = H - 100 - i * 14
        c.setFillColor(colors.HexColor(shades[i % 2])); c.rect(70, yy - 4, 300, 13, stroke=0, fill=1)
        for k, (x, v) in enumerate(zip((72, 200, 260, 320), r)):
            if k:
                c.setFillColor(colors.HexColor(heat[(i + k) % 3]))
                c.rect(x - 2, yy - 3, 40, 11, stroke=0, fill=1)
            c.setFillColor(colors.black); c.setFont("Helvetica", 8); c.drawString(x, yy, v)
    c.setFillColor(colors.HexColor("#1F4E79")); c.rect(72, H - 300, 450, 6, stroke=0, fill=1)
    c.setFillColor(colors.black); c.setFont("Helvetica", 10)
    c.drawString(72, H - 320, "A decorative bar sits above this paragraph.")
    c.save()
    return path


def status_matrix(path):
    """Grid of coloured EMPTY cells with row/column labels: a table, not a chart."""
    c = canvas.Canvas(path, pagesize=letter)
    c.setFont("Helvetica-Bold", 11); c.drawString(72, H - 72, "Project status")
    cols = ["Jan", "Feb", "Mar", "Apr"]
    rows = ["Design", "Build", "Test"]
    pal = ["#70AD47", "#FFC000", "#C00000"]
    c.setFont("Helvetica", 8)
    for k, m in enumerate(cols):
        c.drawString(160 + k * 40, H - 100, m)
    for i, rname in enumerate(rows):
        yy = H - 118 - i * 18
        c.setFillColor(colors.black); c.drawString(72, yy + 3, rname)
        for k in range(len(cols)):
            c.setFillColor(colors.HexColor(pal[(i + k) % 3]))
            c.rect(158 + k * 40, yy, 30, 12, stroke=0, fill=1)
    c.setFillColor(colors.black)
    c.save()
    return path


def form(path):
    c = canvas.Canvas(path, pagesize=letter)
    c.setFont("Helvetica-Bold", 12); c.drawString(72, H - 72, "Application form")
    c.setFont("Helvetica", 9)
    fields = [("Name:", "Date:"), ("Address:", "Phone:"), ("City:", "Postcode:")]
    for i, (a, b) in enumerate(fields):
        yy = H - 110 - i * 24
        c.drawString(72, yy, a); c.line(120, yy - 2, 290, yy - 2)
        c.drawString(320, yy, b); c.line(370, yy - 2, 540, yy - 2)
    c.drawString(72, H - 200, "Please complete every field in block capitals and sign below.")
    c.save()
    return path


# ───────────────────────── tests ─────────────────────────

def test_scorecard(variant, **kw):
    p = scorecard(os.path.join(OUT, f"scorecard_{variant}.pdf"), **kw)
    r = run(p)
    md = r.markdown
    pre = f"scorecard[{variant}]"
    check(f"{pre}: resource-provider table",
          find_table(md, ["RESOURCE PROVIDER", "Rank", "Tier", "Voter Participation"]) and
          ["Northwind Securities", "4 out of 50", "2", "17 out of 17"] in
          find_table(md, ["RESOURCE PROVIDER", "Rank", "Tier", "Voter Participation"]))
    cat = find_table(md, ["CATEGORIES", "Rank", "Tier", "Voter Participation"])
    check(f"{pre}: categories table", cat and ["Research", "5 out of 42", "3", "10 out of 14"] in cat
          and len(cat) == 4, cat)
    dep = find_table(md, ["DEPARTMENTS", "Rank", "Tier", "Voter Participation"])
    check(f"{pre}: departments table", dep and len(dep) == 5, dep)
    reg = find_table(md, ["REGIONS", "Rank", "Tier", "Voter Participation"])
    check(f"{pre}: regions table", reg and len(reg) == 4, reg)
    tables = table_rows(md)
    research = [t for t in tables if t[0][:2] == ["NAME", "VOTES"] and any(r_[0] == "Avery Stone" for r_ in t)]
    check(f"{pre}: research table separate", research and ["Avery Stone", "8.8", "", ""] in research[0]
          and len(research[0]) == 12, research)
    sales = [t for t in tables if t[0][:2] == ["NAME", "VOTES"] and any(r_[0] == "Jamie Ortiz" for r_ in t)]
    check(f"{pre}: sales table separate", sales and ["Jamie Ortiz", "56.6", "", ""] in sales[0], sales)
    mixed = [ln for ln in md.splitlines() if "out of" in ln and re.search(r"Avery|Blake|Casey|Jamie|NAME", ln)]
    check(f"{pre}: no row mixes left and right", not mixed, mixed[:3])
    order = [md.find(s) for s in ("BREAKDOWN", "RESEARCH MENTIONS", "SALES MENTIONS")]
    check(f"{pre}: reading order: left tables, then right tables", -1 not in order and order == sorted(order), order)
    check(f"{pre}: chart section kept after the left tables", md.find("PEER ANALYSIS") > md.find("REGIONS"))
    check(f"{pre}: section labels on their own line",
          all(re.search(rf"^(#+ )?{h}$", md, re.M) for h in ("BREAKDOWN", "RESEARCH MENTIONS", "SALES MENTIONS")))
    check(f"{pre}: no figure when images are off", not figure_blocks(r))
    check(f"{pre}: chart labels stay in the text", all(x in md for x in ("Provider #1", "Provider #5", "Corporate Access")))
    check(f"{pre}: no chart numbers invented", "Chart data" not in md and "100.0" not in md)
    ri = convert(p, images="embed")
    figs = figure_blocks(ri)
    check(f"{pre}: chart saved as one image when images are on",
          len(figs) == 1 and figs[0]["content"].startswith("![Chart"), len(figs))
    check(f"{pre}: image mode keeps all text", ri.markdown.count("Provider #") == md.count("Provider #"))
    check(f"{pre}: markdownSpan exact", spans_ok(r))


def test_column_chart(labels):
    p = column_chart(os.path.join(OUT, f"column_{labels}.pdf"), data_labels=labels)
    r = run(p)
    md = r.markdown
    pre = f"column chart[{'labels' if labels else 'axis'}]"
    check(f"{pre}: no data table invented", not find_table(md, ["Category", "Desk A", "Desk B"]))
    check(f"{pre}: labels kept", all(x in md for x in ("Desk A", "Desk B", "Q1", "Q4")))
    check(f"{pre}: chart image when images are on", len(figure_blocks(convert(p, images="embed"))) == 1)
    check(f"{pre}: body text kept", "Volumes rose in every quarter" in md and
          "The chart below shows volume" in md)
    check(f"{pre}: markdownSpan exact", spans_ok(r))
    ri = convert(p, images="embed")
    labels_line = next((l for l in ri.markdown.splitlines() if l.startswith("*Chart labels:")), "")
    check(f"{pre}, images on: ticks and categories gathered in one line under the chart",
          all(x in labels_line for x in ("Q1", "Q4", "0")), labels_line or ri.markdown[:400])
    check(f"{pre}, images on: legend outside the chart box still in the text",
          all(x in ri.markdown for x in ("Desk A", "Desk B")), ri.markdown[:400])
    check(f"{pre}, images on: body text outside the chart kept",
          "Volumes rose in every quarter" in ri.markdown and "The chart below shows volume" in ri.markdown)
    check(f"{pre}, images on: markdownSpan exact", spans_ok(ri))


def test_pie():
    r = run(pie_chart(os.path.join(OUT, "pie.pdf")))
    md = r.markdown
    check("pie: figure block when images are on",
          len(figure_blocks(convert(os.path.join(OUT, "pie.pdf"), images="embed"))) == 1)
    check("pie: labels kept", all(s in md for s in ("Equity", "Credit", "Other")))
    check("pie: no invented numbers", not re.search(r"\b(50|30|20)(\.0)?\b", md), md)


def test_negative(name, builder, must_have, table_header=None, table_len=None):
    r = run(builder(os.path.join(OUT, f"{name}.pdf")))
    md = r.markdown
    check(f"{name}: no figure invented", not figure_blocks(r), figure_blocks(r))
    for s in must_have:
        check(f"{name}: keeps '{s}'", s in md, md)
    if table_header:
        t = find_table(md, table_header)
        check(f"{name}: stays ONE table {table_header}", t and (table_len is None or len(t) == table_len)
              and len(t[0]) == len(table_header), table_rows(md))
    check(f"{name}: markdownSpan exact", spans_ok(r))


def rotated_scan(path, angle):
    """A scanned page lying sideways / upside down (text is pixels only)."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (1700, 2200), "white")
    d = ImageDraw.Draw(im)
    big, f = font(60, bold=True), font(34, serif=True)
    d.text((150, 150), "Annual Overview", font=big, fill="black")
    y = 300
    for ln in ["The firm manages assets for institutional clients across",
               "public and private markets, with offices in many regions.",
               "Client service and research quality improved this year."]:
        d.text((150, y), ln, font=f, fill="black")
        y += 55
    im.rotate(angle, expand=True).save(path, resolution=200)
    return path


def mixed_page(path):
    """Paragraphs + two charts side by side + two tables side by side + paragraph."""
    c = canvas.Canvas(path, pagesize=letter)
    c.setFont("Helvetica-Bold", 16); c.drawString(50, H - 60, "Quarterly Review")
    c.setFont("Helvetica", 10)
    c.drawString(50, H - 82, "This review covers trading volumes and client rankings for the quarter.")
    c.drawString(50, H - 96, "Both desks grew, and client feedback improved across all regions.")

    def chart(x0, top, title, labels, vals):
        c.setStrokeColor(colors.HexColor("#BBBBBB")); c.setLineWidth(0.5)
        c.rect(x0, top - 150, 240, 140, stroke=1, fill=0); c.setStrokeColor(colors.black)
        c.setFont("Helvetica-Bold", 9); c.drawString(x0 + 8, top - 24, title)
        ax = x0 + 70
        c.line(ax, top - 140, ax, top - 32)
        for i, (lab, (a, b)) in enumerate(zip(labels, vals)):
            by = top - 50 - i * 24
            c.setFont("Helvetica", 7); c.drawRightString(ax - 4, by + 2, lab)
            c.setFillColor(colors.HexColor("#2F5597")); c.rect(ax, by, a * 1.5, 9, stroke=0, fill=1)
            c.setFillColor(colors.HexColor("#ED7D31")); c.rect(ax + a * 1.5, by, b * 1.5, 9, stroke=0, fill=1)
            c.setFillColor(colors.black)
    chart(50, H - 110, "Volume by desk", ["Equities", "Credit", "Rates", "FX"], [(40, 30), (25, 20), (35, 10), (20, 25)])
    chart(320, H - 110, "Volume by region", ["Americas", "Europe", "Asia"], [(50, 20), (30, 30), (20, 15)])

    def table(x0, x1, top, head, rows):
        c.setFont("Helvetica-Bold", 8)
        for x, hd in zip((x0, x0 + 110, x0 + 170), head):
            c.drawString(x, top, hd)
        c.setLineWidth(1); c.line(x0, top - 3, x1, top - 3)
        c.setFont("Helvetica", 8)
        for i, r in enumerate(rows, 1):
            for x, v in zip((x0, x0 + 110, x0 + 170), r):
                c.drawString(x, top - i * 12, v)
    table(50, 290, H - 300, ("Client", "Rank", "Votes"),
          [("Harbor Fund", "2", "41"), ("Pine Capital", "5", "28"), ("Lake Partners", "1", "55"), ("Ridge Asset", "7", "12")])
    table(320, 560, H - 300, ("Analyst", "Sector", "Score"),
          [("Morgan Lee", "Energy", "8.1"), ("Riley Park", "Banks", "7.4"), ("Jordan Fox", "Tech", "9.0"),
           ("Casey Wren", "Retail", "6.8"), ("Taylor Moss", "Autos", "7.7"), ("Drew Hale", "Media", "6.2")])
    c.setFont("Helvetica", 10)
    c.drawString(50, H - 420, "Overall, rankings improved in three of four regions this quarter.")
    c.drawString(50, H - 434, "The next review is scheduled for the end of the following quarter.")
    c.save()
    return path


def test_mixed_page():
    p = mixed_page(os.path.join(OUT, "mixed.pdf"))
    r = run(p)
    md = r.markdown
    pre = "mixed page"
    check(f"{pre}: intro paragraph intact", "This review covers trading volumes and client rankings for the quarter. "
          "Both desks grew, and client feedback improved across all regions." in md, md[:400])
    check(f"{pre}: closing paragraph intact", "Overall, rankings improved in three of four regions this quarter. "
          "The next review is scheduled for the end of the following quarter." in md)
    left = find_table(md, ["Client", "Rank", "Votes"])
    right = find_table(md, ["Analyst", "Sector", "Score"])
    check(f"{pre}: left table separate", left and ["Lake Partners", "1", "55"] in left and len(left) == 5, left)
    check(f"{pre}: right table separate", right and ["Jordan Fox", "Tech", "9.0"] in right and len(right) == 7, right)
    mixed = [ln for ln in md.splitlines() if re.search(r"Harbor|Pine|Lake|Ridge", ln) and re.search(r"Morgan|Riley|Jordan|Casey|Analyst", ln)]
    check(f"{pre}: no row mixes the two tables", not mixed, mixed)
    check(f"{pre}: chart labels kept", all(x in md for x in ("Volume by desk", "Volume by region", "Equities", "Americas", "Asia")))
    check(f"{pre}: intro first, closing last",
          md.find("This review covers") < md.find("Client") and md.find("Overall, rankings") > md.find("Drew Hale"))
    ri = convert(p, images="embed")
    check(f"{pre}: both side-by-side charts saved as images", len(figure_blocks(ri)) == 2, len(figure_blocks(ri)))
    check(f"{pre}: markdownSpan exact", spans_ok(r) and spans_ok(ri))


def demo_pages():
    """tests/make_demo.py's 4-page PDF, converted once: per-page markdown (images saved)."""
    if not hasattr(demo_pages, "cache"):
        from make_demo import build
        pdf = build(os.path.join(OUT, "layout_demo.pdf"), os.path.join(OUT, "demo_tmp"))
        r = convert(pdf, images="save", image_dir=os.path.join(OUT, "demo_images"))
        demo_pages.cache = ({p["pageNumber"]: p["markdown"] for p in r.pages}, r)
    return demo_pages.cache


def in_order(md, needles):
    pos = [md.find(n) for n in needles]
    return all(p >= 0 for p in pos) and pos == sorted(pos), dict(zip(needles, pos))


def test_demo_two_columns():
    pages, r = demo_pages()
    md = pages[2]
    ok, where = in_order(md, [f"Column paragraph {i}." for i in range(1, 13)])
    check("demo p2: left column read before right column", ok, where)
    ok, where = in_order(md, ["Column paragraph 5.", "| North | 214 | 12 |", "Column paragraph 6."])
    check("demo p2: table in the left column between its paragraphs", ok, where)
    ok, where = in_order(md, ["Column paragraph 8.", "](", "Figure 2: The new regional office", "Column paragraph 9."])
    check("demo p2: photo and caption in the right column between its paragraphs", ok, where)
    check("demo p2: no paragraph broken across columns",
          all(md.count(f"Column paragraph {i}.") == 1 for i in range(1, 13)) and "the Column paragraph" not in md, md[:300])
    check("demo p2: marked as a column page", 2 in r.pages_with_columns, r.pages_with_columns)


def test_demo_three_columns():
    pages, r = demo_pages()
    md = pages[4]
    check("demo p4: prose columns are not a table", "| Rates | Credit | Equities |" not in md
          and "| moved with the market" not in md, md[:400])
    notes = [f"{t} note {k}." for t in ("Rates", "Credit", "Equities") for k in range(1, 5)]
    ok, where = in_order(md, notes)
    check("demo p4: three columns read in order", ok, where)
    ok, where = in_order(md, ["Credit note 4.", "| VaR 99% | 3.2m |", "| Stress | (18.5m) |", "Equities note 1."])
    check("demo p4: risk table kept, inside the middle column", ok, where)
    check("demo p4: heading before the columns", in_order(md, ["Outlook and Risks", "Rates note 1."])[0])
    check("demo p4: full-width sentence above the columns kept whole",
          "A three-column page: read each column top to bottom before the next." in md, md[:300])
    check("demo p4: marked as a column page", 4 in r.pages_with_columns, r.pages_with_columns)


def test_demo_single_colour_chart():
    pages, r = demo_pages()
    md = pages[3]
    figs = [it for pg in r.blocks if pg["pageNumber"] == 3 for it in pg["items"] if it["type"] == "figure"]
    check("demo p3: one-colour vector bar chart saved as a figure", len(figs) == 1, figs)
    ok, where = in_order(md, ["Figure 3: Fund assets", "](", "Returns are net of fees"])
    check("demo p3: chart image after its title, before the footnote", ok, where)
    check("demo p3: chart labels in one line under the image",
          "*Chart labels: 14, 12, 10, 8, 6, 4, 2, 0, Alpha, Beta, Gamma, Delta*" in md, md[-500:])
    check("demo p3: no loose axis-number lines", not re.search(r"^(14|12|10|8|6|4|2)$", md, re.M), md[-500:])
    check("demo p3: footnote after the chart kept", in_order(md, ["*Chart labels:", "Returns are net of fees"])[0])
    plain = run(os.path.join(OUT, "layout_demo.pdf")).markdown
    check("demo p3, images off: chart text stays as text (no picture to replace it)",
          "Alpha" in plain and "*Chart labels:" not in plain)
    check("demo p3: both side-by-side tables still tables",
          "| Alpha Growth | 12,400 | 8.2% |" in md and "| AAA | 14 | 11.2% |" in md, md[:600])


def test_chart_text_fold_only_labels():
    """Only chart-like text (short labels, numeric ticks) is folded into the labels line; text of a
    shaded table mistaken for a chart stays as it is."""
    from pdf2md import Word, chart_labels

    def words(rows):
        out = []
        for k, cells in enumerate(rows):
            for x, t in cells:
                for j, tok in enumerate(t.split()):
                    out.append(Word(tok, x + j * 40, x + j * 40 + 36, 100 + 16 * k, 109 + 16 * k, 9.0,
                                    "Helvetica", False, False, False))
        return out
    table = words([[(80, "Area"), (260, "Competence")],
                   [(80, "1. Embodying sustainability values"), (260, "1.1 Valuing sustainability")],
                   [(260, "1.2 Supporting fairness")], [(260, "1.3 Promoting nature")]])
    check("chart text fold: table text mistaken for a chart is not folded", chart_labels(table) == "",
          chart_labels(table))
    axis = words([[(40, "14")], [(40, "12")], [(40, "10")], [(40, "0"), (90, "Alpha"), (200, "Beta"), (310, "Gamma")]])
    check("chart text fold: ticks and short names are folded", chart_labels(axis) == "14, 12, 10, 0, Alpha, Beta, Gamma",
          chart_labels(axis))


def rating_squares(path):
    """Same-colour rating squares in a row of text (5-star style): marks, not a chart."""
    c = canvas.Canvas(path, pagesize=(W, H))
    c.setFont("Helvetica", 10)
    y = H - 100
    for k, (name, n) in enumerate((("Service", 4), ("Value", 3), ("Speed", 5), ("Support", 2))):
        c.drawString(72, y - 24 * k, name)
        c.setFillColor(colors.HexColor("#2F5597"))
        for j in range(n):
            c.rect(160 + j * 16, y - 24 * k - 2, 11, 11, fill=1, stroke=0)
        c.setFillColor(colors.black)
    c.drawString(72, y - 120, "Ratings from the client survey, one square per point.")
    c.save()
    return path


def test_rating_squares_not_chart():
    r = run(rating_squares(os.path.join(OUT, "rating_squares.pdf")))
    check("rating squares: no figure invented", not figure_blocks(r), figure_blocks(r))


def text_table(path):
    """A borderless table of short capitalised text cells: a table, not prose."""
    c = canvas.Canvas(path, pagesize=(W, H))
    c.setFont("Helvetica-Bold", 10)
    y = H - 90
    for x, h in zip((72, 230, 390), ("Office", "Contact", "Role")):
        c.drawString(x, y, h)
    c.setFont("Helvetica", 10)
    for row in (("North Office", "Jane Smith", "Branch Manager"), ("South Office", "Ade Bello", "Credit Lead"),
                ("West Office", "Mia Chen", "Operations"), ("East Office", "Tom Ruiz", "Compliance"),
                ("Head Office", "Lea Park", "Finance Director")):
        y -= 18
        for x, v in zip((72, 230, 390), row):
            c.drawString(x, y, v)
    c.save()
    return path


def marker_list(path):
    """A list with markers in their own column and wrapped item text (like a scanned form's
    lettered list): its markers must not be split from their first lines."""
    c = canvas.Canvas(path, pagesize=(W, H))
    c.setFont("Helvetica", 10)
    y = H - 90
    items = [("A.", ["Manufacturing plants established by reputed companies in the region,"]),
             ("B.", ["Service organizations such as banks, insurers and telecom operators,"]),
             ("C.", ["Government agencies, including departments that run public registries,"]),
             ("D.", ["Reputed non-profit groups in any related sector, or those serving",
                     "special needs such as education and health care,"]),
             ("E.", ["Any proposal from a faculty member to spend time in research labs."])]
    for mark, lines_ in items:
        c.drawString(72, y, mark)
        for k, t in enumerate(lines_):
            c.drawString(126, y - 14 * k, t)
        y -= 14 * len(lines_) + 4
    c.save()
    return path


def test_marker_list_kept():
    md = run(marker_list(os.path.join(OUT, "marker_list.pdf"))).markdown
    check("marker list: every marker stays with its first line",
          all(any(m in l and first in l for l in md.splitlines())
              for m, first in (("A.", "Manufacturing plants"), ("B.", "Service organizations"),
                               ("C.", "Government agencies"), ("D.", "Reputed non-profit"),
                               ("E.", "Any proposal"))), md)
    in_table = sum(1 for m in ("A.", "B.", "C.", "D.", "E.") if f"| {m} |" in md)
    check("marker list: all items read the same way (all list items, or one table)",
          in_table in (0, 5) and not re.search(r"^#+ A\.", md, re.M), md[:400])


def label_columns_table(path):
    """A borderless table like a product sheet: two short label columns, then wrapped prose."""
    c = canvas.Canvas(path, pagesize=(792, 612))
    c.setFont("Helvetica-Bold", 10)
    for x, h in ((40, "Service Stage"), (190, "Function Name"), (360, "Explanation")):
        c.drawString(x, 560, h)
    c.setFont("Helvetica", 9)
    rows = [("1. Project creation", ["Project creation and", "management"],
             ["Select document type to automatically run project creation, with", "recommended model sets and endpoint deployment for each project"]),
            ("2. Data labeling and", ["Data storage management"],
             ["Provides convenient functions for uploading raw data, a viewer, and", "data management with search, sorting and filtering on image data"]),
            ("3. Pipeline setup", ["Pipeline and endpoint", "creation"],
             ["Choose a detector, recognizer or parser to create a pipeline, then", "connect pipelines to endpoints and manage deployment recovery"]),
            ("4. Monitoring", ["Project monitoring"],
             ["Monitoring of deployed pipelines and endpoints, notifying the customer", "of important issues such as degraded model performance over time"])]
    y = 530
    for stage, fn, ex in rows:
        c.drawString(40, y, stage)
        for k, t in enumerate(fn):
            c.drawString(190, y - 12 * k, t)
        for k, t in enumerate(ex):
            c.drawString(360, y - 12 * k, t)
        y -= 62
    c.save()
    return path


def test_label_columns_not_split():
    md = run(label_columns_table(os.path.join(OUT, "label_columns.pdf"))).markdown
    near = lambda a, b: any(a in l and b in l for l in md.splitlines())
    check("label columns page: a row's stage and function stay together",
          near("1. Project creation", "Project creation and"), md[:500])


def test_text_table_stays():
    md = run(text_table(os.path.join(OUT, "text_table.pdf"))).markdown
    check("borderless text table (short capitalised cells) stays a table",
          "| North Office | Jane Smith | Branch Manager |" in md, md)


def single_column_short_lines(path):
    """One column: a paragraph, a short bulleted list and a key/value list (short lines on the left)."""
    c = canvas.Canvas(path, pagesize=(W, H))
    c.setFont("Helvetica", 10)
    y = H - 72
    c.drawString(72, y, "Account summary for the period, prepared for the committee meeting in October 2026.")
    for item in ["- Cash balances", "- Open orders", "- Pending transfers", "- Fees charged", "- Notes"]:
        y -= 16
        c.drawString(90, y, item)
    for k, v in [("Prepared by", "J. Okafor"), ("Reviewed by", "M. Lind"), ("Period", "Q3 2026"),
                 ("Status", "Final"), ("Pages", "4"), ("Copies", "2"), ("Owner", "Finance")]:
        y -= 16
        c.drawString(72, y, k)
        c.drawString(320, y, v)
    y -= 24
    c.drawString(72, y, "The figures above were reconciled against the ledger before the meeting started.")
    c.save()
    return path


def test_single_column_stays():
    md = run(single_column_short_lines(os.path.join(OUT, "single_short.pdf"))).markdown
    ok, where = in_order(md, ["Account summary", "Cash balances", "Notes", "Prepared by", "J. Okafor", "Owner",
                              "Finance", "The figures above"])
    check("single column with short lines: read top to bottom", ok, where)


BP_HEAD = ["Rank", "Rank..", "%To..", "$ YTD", "$ Prev YTD", "$Δ POP", "B/D %Δ POP", "WM %Δ POP"]
# (labels as printed, rank, change, share, ytd, prev ytd, B/D %, WM %)
BP_PAGES = [
    [("Overall Trading Commissions", "", 2, [
        (["Overall Commissions", ""], 5, -1, 100, 152918094, 136877273, 11, 22),
        (["Equity", "Subtotal"], 5, -1, 19, 31159326, 25989698, 20, 36),
        (["", "High Touch"], 5, -1, 15, 23931992, 18323498, 31, 40),
        (["", "Low Touch"], 5, -2, 4, 7227334, 7666200, -6, 24),
        (["Fixed Income", "Subtotal"], 5, 0, 81, 121768768, 110887575, 9, 18),
        (["", "Cash"], 4, -1, 63, 92128437, 83656870, 9, 16),
        (["", "Derivatives"], 6, -2, 18, 29640331, 27230705, 9, 26)]),
     ("Equity Regions", "(Excludes supranational and multi-country)", 1, [
         (["Asia ex Japan"], 2, 0, 12, 3892637, 3074707, 27, 26),
         (["Canada"], 11, -6, 0, 102261, 252902, -60, 17),
         (["EMEA"], 4, -1, 27, 8364453, 6305697, 33, 53),
         (["Japan"], 6, -2, 4, 1250278, 1433449, -13, 39),
         (["United States"], 5, -1, 53, 16402928, 13923751, 18, 6)]),
     ("Equity Derivatives", "(Futures aggregated via executing B/D)", 1, [
         (["Total"], 5, 0, 100, 4989930, 3238731, 54, 79),
         (["Options"], 5, 0, 98, 4908639, 3222006, 52, 64),
         (["Swaps"], 5, 2, 2, 81291, 11840, 87, 80)])],
    [("Fixed Income Sectors", "", 3, [
        (["Investment Grade", "Subtotal", ""], 4, -1, 24, 31781814, 31329706, 1, 6),
        (["", "Cash", "New Issue"], 1, 1, 58, 19127501, 20298311, -6, 0),
        (["", "", "Secondary"], 5, -1, 43, 11033887, 10167886, 9, 22),
        (["", "Deriv", ""], 4, 3, 1, 509643, 155433, 28, -47),
        (["High Yield (ex BL)", "Subtotal", ""], 4, -1, 9, 11230921, 12457646, -10, 26),
        (["", "Cash", "New Issue"], 3, 0, 20, 6530342, 9524806, -31, 15),
        (["", "", "Secondary"], 4, -1, 13, 3430249, 2260293, 52, 46),
        (["Municipals", "Subtotal", ""], 2, 0, 1, 1507158, 1397330, 8, -6),
        (["", "Tax Exempt", ""], 1, 1, 1, 679198, 616801, 10, -5)])],
    [("", "", 3, [   # the table carries on: the page starts under a group printed on the page before
        (["", "Taxable", ""], 2, 1, 1, 827960, 780529, 6, -8),
        (["Structured Finance", "Subtotal", ""], 2, 0, 6, 8309372, 6203253, 34, 26),
        (["", "Cash", "New Issue"], 4, 0, 9, 2788777, 1712329, 63, 81),
        (["", "", "Secondary"], 1, 1, 21, 5520595, 4490924, 23, 3)])],
]


def bp_cells(row):
    """A row's printed cells after the labels."""
    _, rank, chg, share, ytd, prev, bd, wm = row
    d = ytd - prev
    return [str(rank), "%+d" % chg, "%d%%" % share, "{:,}".format(ytd), "{:,}".format(prev),
            ("+" if d >= 0 else "") + "{:,}".format(d), "%+d%%" % bd, "%+d%%" % wm]


def bp_expected():
    """Tables as they should read: group labels filled in (a lower label only under its own upper label),
    carried over a page break."""
    out, last = [], []
    for page in BP_PAGES:
        for title, note, nlab, rows in page:
            if not title:
                last = last[:nlab]
            else:
                last = [""] * nlab
            table = [[""] * nlab + BP_HEAD]
            for row in rows:
                labels, filled, fresh = row[0], [], False
                for k, lab in enumerate(labels):
                    fresh = fresh or bool(lab)
                    filled.append(lab if lab or fresh else last[k])
                last = filled
                table.append(filled + bp_cells(row))
            out.append((title, note, table))
    return out


def broker_profile(path):
    """Dashboard-export broker profile (invented values): every word drawn on its own with no space characters
    (the gap is one space, ~0.28 em, at 4.5-6pt), a navy sidebar repeated on every page, stacked tables under
    grey title bands, up to three levels of group labels, and two bar columns whose % sits under each bar."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    PW, PH = 700, 792
    c = canvas.Canvas(path, pagesize=(PW, PH))
    navy, band = colors.HexColor("#1F3A5F"), colors.HexColor("#E6E9EE")
    green, red = colors.HexColor("#3CB043"), colors.HexColor("#E0403A")

    def words(x, y, s, size=5.5, fnt="Helvetica", right=False, color=colors.black):
        ws, gap = s.split(), stringWidth(" ", fnt, size)
        if right:
            x -= sum(stringWidth(w, fnt, size) for w in ws) + gap * (len(ws) - 1)
        c.setFillColor(color)
        for w in ws:
            c.setFont(fnt, size)
            c.drawString(x, y, w)
            x += stringWidth(w, fnt, size) + gap
        c.setFillColor(colors.black)

    def sidebar():
        c.setFillColor(navy)
        c.rect(25, 20, 105, PH - 120, stroke=0, fill=1)
        white = colors.white
        y = PH - 130
        for s, f, z in [("Global", "Helvetica-Bold", 11), ("Broker Profile", "Helvetica-Bold", 11),
                        ("December 31, 2025", "Helvetica", 7), ("Northwind Securities", "Helvetica", 7)]:
            words(35, y, s, z, f, color=white)
            y -= 14
        y -= 12
        for yr in ("'25", "'24"):
            words(30, y, yr, 4.5, color=white)
            for k, m in enumerate("JFMAMJJASOND"):
                c.setFillColor(colors.HexColor("#F2A65A"))
                c.rect(40 + k * 7.5, y - 1.5, 7, 6.5, stroke=0, fill=1)
                words(41.5 + k * 7.5, y, m, 4.5)
            y -= 8
        y -= 14
        words(32, y, "Filters:", 7, "Helvetica-Bold", color=white)
        y -= 10
        words(32, y, "Northwind Securities Ltd", 5, color=white)
        y -= 20
        words(32, y, "Definitions:", 7, "Helvetica-Bold", color=white)
        y -= 12
        words(32, y, "Period Over Period (POP)", 6, "Helvetica-Bold", color=white)
        y -= 8
        for ln in ["Compares the current year,", "through the date listed, to the", "equivalent period from the",
                   "previous year. This is based", "on actual values not", "annualized estimates."]:
            words(34, y, ln, 4.8, color=white)
            y -= 6
        y -= 6
        for title, items in [("Low Touch", ["Algorithm (CA)", "VWAP (VW)", "Portfolio Optimization (PO)"]),
                             ("High Touch", ["Natural (CN)", "Working (CW)", "Block Risk Trade (CT)"])]:
            words(32, y, title, 6, "Helvetica-Bold", color=white)
            y -= 7
            for it in items:
                words(34, y, "• " + it, 4.8, color=white)
                y -= 6
            y -= 6

    X0 = 140
    cols = [292, 312, 332, 375, 420, 467]           # right edges of the six number columns
    # label columns; on the 2-level table a long top label runs past the start of the next column
    labx = {1: [X0 + 2], 2: [X0 + 2, X0 + 50], 3: [X0 + 2, X0 + 60, X0 + 105]}
    axes = [525, 620]                               # zero line of the two bar columns
    rowh = 22
    for page in BP_PAGES:
        sidebar()
        y = PH - 105
        for title, note, nlab, rows in page:
            if title:
                c.setFillColor(band)
                c.rect(X0, y - 4, PW - X0 - 20, 11, stroke=0, fill=1)
                words(X0 + 2, y, title, 6, "Helvetica-Bold")
                if note:
                    words(X0 + 90, y, note, 4.5, "Helvetica-Oblique", color=colors.grey)
            hy = y - 12
            for name, x in zip(BP_HEAD[:6], cols):
                words(x, hy, name, 5, "Helvetica-Bold", right=True)
            words(axes[0] - 15, hy, BP_HEAD[6], 5, "Helvetica-Bold")
            words(axes[1] - 15, hy, BP_HEAD[7], 5, "Helvetica-Bold")
            y = hy - 14
            for row in rows:
                for x, lab in zip(labx[nlab], row[0]):
                    if lab:
                        words(x, y, lab, 5.5, "Helvetica-Bold" if lab == "Subtotal" else "Helvetica")
                cells = bp_cells(row)
                for x, v in zip(cols, cells[:6]):
                    words(x, y, v, 5.5, right=True)
                for axis, v, pct in zip(axes, cells[6:], row[6:]):
                    w = max(min(pct * 0.3, 35), -35)
                    c.setFillColor(green if pct >= 0 else red)
                    c.rect(axis, y - 9, w if w else 0.8, rowh - 2, stroke=0, fill=1)
                    if pct >= 0:
                        words(axis + w + 1, y - 13, v, 4.5)
                    else:
                        words(axis + w - 1, y - 13, v, 4.5, right=True)
                y -= rowh
            y -= 10
        c.showPage()
    c.save()
    return path


def bp_norm(s):
    return s.replace("∆", "Δ")             # the Delta glyph's name maps to either code point


def test_broker_profile():
    r = convert(broker_profile(os.path.join(OUT, "broker_profile.pdf")), images="embed")
    md = bp_norm(r.markdown)
    for words_ in ["Compares the current year,", "Northwind Securities Ltd", "Block Risk Trade (CT)",
                   "Portfolio Optimization (PO)"]:
        check(f"broker profile: words keep their spaces ({words_})", words_ in md, md[:400])
    check("broker profile: data bars are not charts", "Chart" not in md and not figure_blocks(r),
          re.findall(r".*Chart.*", md)[:3])
    tables = [[[bp_norm(c) for c in row] for row in t] for t in table_rows(md)]
    cells = {c for t in tables for row in t for c in row}
    check("broker profile: sidebar is not part of a table",
          not any(w in c for c in cells for w in ("Definitions", "Filters", "Algorithm", "Compares")),
          [c for c in cells if "Definitions" in c or "Algorithm" in c][:3])
    check("broker profile: sidebar shown once, before the tables",
          md.count("Definitions:") == 1 and md.find("Definitions:") < md.find("|"), md.count("Definitions:"))
    for title, note, table in bp_expected():
        got = next((t for t in tables if table[1] in t), None)
        name = title or "continued table"
        check(f"broker profile: {name} reads row for row", got == table,
              f"\n        want {table[:3]}\n        got  {(got or tables)[:3]}")
        if title:
            check(f"broker profile: {title} is a heading", re.search(r"(?m)^#+ " + re.escape(title) + "$", md),
                  title)
        if note:
            check(f"broker profile: note under {title}", re.search(r"(?m)^\*" + re.escape(note) + r"\*$", md),
                  note)
    check("broker profile: block spans slice to content", spans_ok(r))


def test_group_label_fill_stops():
    import pdf2md as P
    rows = [["", "A", "B"], ["CS IS", "8.33", "7.59"], ["ECO", "", ""], ["", "7.54", "8.24"], ["FIN", "", ""]]
    got = P.fill_rows(rows, 1, [""])
    check("group labels: a label on a row of its own stops the copying (values row stays unlabelled)",
          got[3][0] == "", got)
    rows = [["", "", "A"], ["Equity", "Cash", "1"], ["", "Deriv", "2"], ["", "Total", "3"]]
    got = P.fill_rows(rows, 2, ["", ""])
    check("group labels: copied down, but a total row keeps its blanks",
          got[2][0] == "Equity" and got[3][0] == "", got)


def test_rotated_scans():
    import pdf2md as P
    if not P.ocr_available():
        check("rotated scans: tesseract available (skipped)", True)
        return
    for angle in (90, 180, 270):
        r = run(rotated_scan(os.path.join(OUT, f"rot{angle}.pdf"), angle))
        md = r.markdown
        check(f"rotated scan {angle}: reads upright text",
              "Annual Overview" in md and "institutional clients" in md, md[:200])
        check(f"rotated scan {angle}: boxes inside the page",
              all(0 <= v <= 1 for pg in r.blocks for it in pg["items"] if it["bbox"] for v in it["bbox"]))


if __name__ == "__main__":
    test_demo_two_columns()
    test_single_column_stays()
    test_demo_three_columns()
    test_text_table_stays()
    test_marker_list_kept()
    test_label_columns_not_split()
    test_demo_single_colour_chart()
    test_rating_squares_not_chart()
    test_chart_text_fold_only_labels()
    test_mixed_page()
    test_rotated_scans()
    test_broker_profile()
    test_group_label_fill_stops()
    test_scorecard("aligned")
    test_scorecard("unaligned", right_row=12.0)
    test_scorecard("path-bars", bars_as_paths=True)
    test_column_chart(False)
    test_column_chart(True)
    test_pie()
    test_negative("wide_table", wide_table, ["Regional summary"], ["Region", "Q1", "Q2", "Q3", "Q4"], 5)
    test_negative("cell_borders", cell_border_table, [], ["Item", "Units", "Price", "Total"], 4)
    test_negative("financial", financial, ["Statement of income"], None)
    test_negative("zebra_heatmap", zebra_and_heatmap, ["A decorative bar"], ["Name", "Q1", "Q2", "Q3"], 5)
    test_negative("status_matrix", status_matrix, ["Design", "Build", "Test", "Jan", "Apr"])
    test_negative("form", form, ["Name:", "Date:", "Address:", "Phone:", "City:", "Postcode:",
                                 "Please complete every field"])
    # financial: the statement must not be split between the two number columns
    fr = run(os.path.join(OUT, "financial.pdf")).markdown
    fin = [t for t in table_rows(fr) if any("Revenue" in c for row in t for c in row)]
    check("financial: Revenue row keeps both years together",
          fin and any(row[:1] == ["Revenue"] and "1,240" in row and "1,105" in row for row in fin[0]), fin)

    width = max(len(n) for n, _, _ in RESULTS)
    failed = [x for x in RESULTS if not x[1]]
    for n, ok, detail in RESULTS:
        print(f"{'PASS' if ok else 'FAIL'}  {n}" + ("" if ok else f"\n        {str(detail)[:300]}"))
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    sys.exit(1 if failed else 0)
