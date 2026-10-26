"""A 4-page demo PDF with invented data: a ruled table with bracketed negatives and a chart picture (p1),
two columns with a table and a photo inside them (p2), two tables side by side and a one-colour vector
bar chart (p3), a heading over three columns with a small table (p4). Running header and page numbers.
Run: python tests/make_demo.py tests/samples/layout_demo.pdf   (tests/test_layout.py builds it too)"""
import os
import sys
import tempfile

from PIL import Image, ImageDraw
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (BaseDocTemplate, Frame, FrameBreak, Image as RLImage, NextPageTemplate,
                                PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fonts import font as _font  # noqa: E402

W, H = letter
M = 0.75 * inch
ss = getSampleStyleSheet()
body = ParagraphStyle("body", parent=ss["BodyText"], fontSize=9.5, leading=12.5)
small = ParagraphStyle("small", parent=body, fontSize=8, leading=10, textColor=colors.HexColor("#444444"))
cap = ParagraphStyle("cap", parent=small, fontName="Helvetica-Oblique")
h1, h2, h3 = ss["Heading1"], ss["Heading2"], ss["Heading3"]
gap = 0.3 * inch
cw2 = (W - 2 * M - gap) / 2          # two-column width
cw3 = (W - 2 * M - 2 * gap) / 3      # three-column width


def font(size, bold=False):
    return _font(size, bold=bold)


def chart_png(path):
    """A bar chart as a picture (pixels), with printed values."""
    im = Image.new("RGB", (1200, 700), "white")
    d = ImageDraw.Draw(im)
    d.text((40, 20), "Net revenue by quarter (USD m)", font=font(36, True), fill="black")
    vals = [("Q1", 41.2), ("Q2", 47.8), ("Q3", 39.5), ("Q4", 52.6)]
    d.line((100, 620, 1150, 620), fill="black", width=3)
    for i, (q, v) in enumerate(vals):
        x = 160 + i * 250
        top = 620 - v * 9
        d.rectangle((x, top, x + 140, 620), fill=["#2F5597", "#4472C4", "#8FAADC", "#1F3864"][i])
        d.text((x + 35, top - 48), f"{v}", font=font(32), fill="black")
        d.text((x + 50, 635), q, font=font(32), fill="black")
    im.save(path)
    return path


def photo_png(path):
    """A 'photo': a gradient sky, hills and a building outline (no text)."""
    im = Image.new("RGB", (900, 600))
    d = ImageDraw.Draw(im)
    for y in range(600):
        d.line((0, y, 900, y), fill=(120 + y // 8, 170 + y // 12, 230 - y // 6))
    d.polygon([(0, 600), (0, 420), (250, 330), (520, 430), (900, 300), (900, 600)], fill=(70, 120, 70))
    d.rectangle((560, 250, 700, 470), fill=(200, 200, 205), outline=(60, 60, 60), width=4)
    for r in range(4):
        for c in range(3):
            d.rectangle((575 + c * 42, 270 + r * 48, 600 + c * 42, 300 + r * 48), fill=(90, 140, 200))
    im.save(path)
    return path


def ruled(rows, widths=None, header_bg="#D9E2F3", align_right_from=1):
    t = Table(rows, colWidths=widths, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#808080")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_bg)),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ALIGN", (align_right_from, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


def furniture(c, doc):
    c.saveState()
    c.setFont("Helvetica", 7.5)
    c.setFillColor(colors.HexColor("#666666"))
    c.drawString(M, H - 0.5 * inch, "Northwind Holdings — Quarterly Review (demo, invented data)")
    c.drawRightString(W - M, 0.5 * inch, f"Page {doc.page}")
    c.restoreState()


def build(out, tmp=None):
    tmp = tmp or tempfile.mkdtemp(prefix="pdf2md_demo_")
    os.makedirs(tmp, exist_ok=True)
    doc = _doc(out)
    doc.build(_story(tmp))
    return out


def _doc(out):
    one = Frame(M, M, W - 2 * M, H - 2 * M, id="one")
    two = [Frame(M, M, cw2, H - 2 * M, id="l"), Frame(M + cw2 + gap, M, cw2, H - 2 * M, id="r")]
    three = [Frame(M + i * (cw3 + gap), M, cw3, H - 2 * M - 0.9 * inch, id=f"c{i}") for i in range(3)]
    top3 = Frame(M, H - M - 0.9 * inch, W - 2 * M, 0.9 * inch, id="top")
    doc = BaseDocTemplate(out, pagesize=letter, title="Northwind Quarterly Review (demo)")
    doc.addPageTemplates([PageTemplate("one", [one], onPage=furniture),
                          PageTemplate("two", two, onPage=furniture),
                          PageTemplate("one_b", [one], onPage=furniture),
                          PageTemplate("three", [top3] + three, onPage=furniture)])
    return doc


def _story(tmp):
    text = ("Desk volumes moved with the market over the quarter while operating costs held close to the plan "
            "approved by the committee in the spring review. ")
    s = []

    # ---- page 1: title, ruled table with negatives, chart picture ----
    s += [Paragraph("Northwind Holdings Quarterly Review", h1),
          Paragraph("This demo document is built from invented figures to exercise layout handling: a ruled "
                    "table with negative values in brackets, a chart stored as a picture, and running headers "
                    "and footers.", body), Spacer(1, 8),
          Paragraph("Table 1: Segment results (USD thousands)", h3),
          ruled([["Segment", "Q1 2026", "Q2 2026", "Change", "Margin"],
                 ["Rates", "1,250", "1,410", "160", "12.4%"],
                 ["Credit", "980", "(215)", "(1,195)", "-3.1%"],
                 ["Equities", "2,045", "1,990", "(55)", "18.0%"],
                 ["Commodities", "430", "512", "82", "7.6%"],
                 ["Total", "4,705", "3,697", "(1,008)", "9.8%"]], widths=[1.6 * inch] + [1.05 * inch] * 4),
          Spacer(1, 10),
          RLImage(chart_png(os.path.join(tmp, "chart.png")), width=5.2 * inch, height=3.03 * inch),
          Paragraph("Figure 1: Net revenue by quarter. The chart is a picture; its numbers are only in the pixels.", cap),
          Spacer(1, 8),
          Paragraph("Revenue recovered in the fourth quarter after a weak third quarter, led by rates and equities. "
                    "Credit posted a loss in the second quarter that reversed most of the first-quarter gain.", body),
          NextPageTemplate("two"), PageBreak()]

    # ---- page 2: two columns, a small table and a picture inside a column ----
    s += [Paragraph("Market Commentary", h2)]
    s += [Paragraph(f"Column paragraph {i + 1}. " + text * 2, body) for i in range(5)]
    s += [Spacer(1, 4), Paragraph("Table 2: Headcount by region", h3),
          ruled([["Region", "Staff", "Open roles"], ["North", "214", "12"], ["South", "187", "9"],
                 ["West", "96", "4"]], widths=[1.2 * inch, 0.9 * inch, 0.9 * inch]), Spacer(1, 6)]
    s += [Paragraph(f"Column paragraph {i + 6}. " + text * 2, body) for i in range(3)]
    s += [RLImage(photo_png(os.path.join(tmp, "photo.png")), width=cw2, height=cw2 * 2 / 3),
          Paragraph("Figure 2: The new regional office (illustration).", cap)]
    s += [Paragraph(f"Column paragraph {i + 9}. " + text * 2, body) for i in range(4)]
    s += [NextPageTemplate("one_b"), PageBreak()]

    # ---- page 3: two tables side by side, a vector bar chart, a footnote ----
    left = ruled([["Fund", "Assets", "Return"], ["Alpha Growth", "12,400", "8.2%"], ["Beta Income", "8,150", "4.9%"],
                  ["Gamma Value", "5,720", "-1.3%"], ["Delta Global", "9,980", "6.6%"]],
                 widths=[1.3 * inch, 0.9 * inch, 0.8 * inch])
    right = ruled([["Rating", "Count", "Share"], ["AAA", "14", "11.2%"], ["AA", "38", "30.4%"], ["A", "51", "40.8%"],
                   ["BBB", "22", "17.6%"]], widths=[0.9 * inch, 0.8 * inch, 0.8 * inch], header_bg="#E2EFDA")
    pair = Table([[left, right]], colWidths=[3.3 * inch, 2.7 * inch], hAlign="LEFT")
    pair.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    dr = Drawing(6.5 * inch, 2.6 * inch)
    bc = VerticalBarChart()
    bc.x, bc.y, bc.width, bc.height = 40, 30, 5.6 * inch, 1.9 * inch
    bc.data = [[12.4, 8.15, 5.72, 9.98]]
    bc.categoryAxis.categoryNames = ["Alpha", "Beta", "Gamma", "Delta"]
    bc.valueAxis.valueMin, bc.valueAxis.valueMax, bc.valueAxis.valueStep = 0, 14, 2
    bc.bars[0].fillColor = colors.HexColor("#4472C4")
    dr.add(bc)
    s += [Paragraph("Funds and Ratings", h2),
          Paragraph("Table 3 (left) lists fund assets in USD thousands; Table 4 (right) shows the rating mix of the "
                    "bond book. The two tables sit side by side, as exported from one spreadsheet.", body), Spacer(1, 8),
          pair, Spacer(1, 14), Paragraph("Figure 3: Fund assets (USD m), drawn as vector shapes", h3), dr,
          Paragraph("* Returns are net of fees for the twelve months to 30 September 2026. Gamma Value returned "
                    "-1.3% after a write-down of (420) thousand.", small),
          NextPageTemplate("three"), PageBreak()]

    # ---- page 4: heading across the top, three columns below ----
    s += [Paragraph("Outlook and Risks", h2),
          Paragraph("A three-column page: read each column top to bottom before the next.", body), FrameBreak()]
    for i, topic in enumerate(["Rates", "Credit", "Equities"]):
        s += [Paragraph(topic, h3)]
        s += [Paragraph(f"{topic} note {k + 1}. " + text, body) for k in range(4)]
        if i == 1:
            s += [ruled([["Risk", "Limit"], ["VaR 99%", "3.2m"], ["Stress", "(18.5m)"]],
                        widths=[0.95 * inch, 0.8 * inch])]
        s += [FrameBreak()]
    return s


if __name__ == "__main__":
    print("written", build(sys.argv[1]))
