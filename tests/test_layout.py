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
    test_mixed_page()
    test_rotated_scans()
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
