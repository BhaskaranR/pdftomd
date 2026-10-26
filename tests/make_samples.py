"""Generate sample PDFs that exercise headings, paragraphs, lists, tables
(ruled + borderless), images, code, links, two-column layout, running
headers/footers, and a scanned page."""
import os
import sys

from PIL import Image, ImageDraw
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (BaseDocTemplate, Frame, Image as RLImage, NextPageTemplate,
                                PageBreak, PageTemplate, Paragraph, Preformatted, Spacer,
                                Table, TableStyle, ListFlowable, ListItem)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fonts import font  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "samples"
os.makedirs(OUT, exist_ok=True)

# a chart-like PNG
img_path = os.path.join(OUT, "chart.png")
im = Image.new("RGB", (600, 300), "white")
d = ImageDraw.Draw(im)
for i, h in enumerate([120, 200, 90, 250, 170]):
    d.rectangle([60 + i * 100, 280 - h, 120 + i * 100, 280], fill=(40, 90, 200))
im.save(img_path)

ss = getSampleStyleSheet()
title = ParagraphStyle("t", parent=ss["Title"], fontName="Helvetica-Bold", fontSize=24, leading=28)
h2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=16, leading=20, spaceBefore=12, spaceAfter=6)
h3 = ParagraphStyle("h3", fontName="Helvetica-Bold", fontSize=13, leading=16, spaceBefore=10, spaceAfter=4)
body = ParagraphStyle("b", fontName="Times-Roman", fontSize=10.5, leading=13.5, spaceAfter=8)
code = ParagraphStyle("c", fontName="Courier", fontSize=9, leading=11)

LOREM = ("Loyalty programs reward repeat customers with points that can be redeemed "
         "for discounts, free items, or exclusive perks. Merchants use them to raise "
         "visit frequency and average order value, while customers enjoy tangible value "
         "for purchases they would have made anyway. Well-designed programs are simple "
         "to understand and quick to pay out.")


def header_footer(c, doc):
    c.saveState()
    c.setFont("Helvetica", 8)
    c.drawString(inch, letter[1] - 0.5 * inch, "Acme Corp — Annual Report 2026")
    c.drawRightString(letter[0] - inch, 0.5 * inch, f"Page {doc.page}")
    c.restoreState()


doc = BaseDocTemplate(os.path.join(OUT, "report.pdf"), pagesize=letter,
                      title="Acme Annual Report")
fw, fh = letter[0] - 2 * inch, letter[1] - 2 * inch
one = Frame(inch, inch, fw, fh, id="one")
gap = 0.3 * inch
cw = (fw - gap) / 2
left = Frame(inch, inch, cw, fh, id="l")
right = Frame(inch + cw + gap, inch, cw, fh, id="r")
doc.addPageTemplates([PageTemplate("one", [one], onPage=header_footer),
                      PageTemplate("two", [left, right], onPage=header_footer)])

story = [
    Paragraph("Acme Annual Report", title),
    Paragraph("Executive Summary", h2),
    Paragraph("This year Acme grew <b>revenue by 42%</b> and launched three new products. "
              "Details are in the <i>financial overview</i> below. Visit "
              '<link href="https://example.com/report" color="blue">our website</link> for more. '
              + LOREM, body),
    Paragraph(LOREM, body),
    Paragraph("Key Highlights", h3),
    ListFlowable([ListItem(Paragraph(t, body)) for t in [
        "Opened 12 new regional offices.",
        "Customer retention improved to 91% across all segments, the highest level in the company's history.",
        "Launched the mobile rewards app."]], bulletType="bullet", start="•"),
    Paragraph("Priorities for next year", h3),
    ListFlowable([ListItem(Paragraph(t, body)) for t in [
        "Expand into two new markets.", "Reduce churn below 5%.", "Ship the partner API."]],
        bulletType="1"),
    Paragraph("Financial Overview", h2),
    Paragraph("Quarterly results are summarised in the table below.", body),
]
t = Table([["Quarter", "Revenue ($M)", "Growth", "Margin"],
           ["Q1", "12.4", "8%", "21%"], ["Q2", "14.1", "14%", "23%"],
           ["Q3", "15.9", "13%", "24%"], ["Q4", "18.2", "15%", "26%"]])
t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                       ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                       ("FONTSIZE", (0, 0), (-1, -1), 9)]))
story += [t, Spacer(1, 12),
          Paragraph("Revenue by region (chart):", body),
          RLImage(img_path, width=4 * inch, height=2 * inch),
          Paragraph("Figure 1. Revenue by region.", body),
          PageBreak(),
          Paragraph("Regional Breakdown", h2),
          Paragraph("The borderless table below lists regional performance.", body)]
bt = Table([["Region", "Stores", "Revenue", "Change"],
            ["North", "42", "$18.1M", "+12%"], ["South", "35", "$14.7M", "+9%"],
            ["East", "28", "$11.2M", "-3%"], ["West", "51", "$22.9M", "+18%"]],
           colWidths=[1.5 * inch, 1.1 * inch, 1.3 * inch, 1.1 * inch])
bt.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), "Helvetica"), ("FONTSIZE", (0, 0), (-1, -1), 9.5)]))
story += [bt, Spacer(1, 10),
          Paragraph("Integration Example", h2),
          Paragraph("Merchants can award points with a single API call:", body),
          Preformatted('import requests\n\ndef award(customer_id, points):\n'
                       '    return requests.post("/v1/points",\n'
                       '        json={"customer": customer_id, "points": points})', code),
          Spacer(1, 10)] + [Paragraph(LOREM, body) for _ in range(3)]
story += [NextPageTemplate("two"), PageBreak(), Paragraph("Market Analysis", h2)]
story += [Paragraph(f"Column paragraph {i + 1}. " + LOREM, body) for i in range(10)]
doc.build(story)

# scanned page: text rendered into an image, wrapped in a PDF
scan = Image.new("RGB", (1700, 2200), "white")
d = ImageDraw.Draw(scan)
f_big, f = font(60, bold=True), font(34, serif=True)
d.text((150, 150), "Scanned Invoice", font=f_big, fill="black")
y = 300
for ln in ["Invoice number: 10442", "Date: 14 October 2026",
           "Bill to: Loop Coffee House, 22 Main Street",
           "", "Thank you for your business. Payment is due",
           "within thirty days of the invoice date."]:
    d.text((150, y), ln, font=f, fill="black")
    y += 55
scan.save(os.path.join(OUT, "scanned.pdf"), resolution=200)
print("samples written to", OUT)
