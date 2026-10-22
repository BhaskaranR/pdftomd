"""Synthetic 'scorecard' PDF: side-by-side tables on a shared row grid plus a
vector stacked-bar chart. All names and numbers are invented."""
import sys
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.pdfgen import canvas

out = sys.argv[1] if len(sys.argv) > 1 else "scorecard.pdf"
W, H = letter
c = canvas.Canvas(out, pagesize=letter)
ROW = 10.5                         # Excel-like row grid shared by both sides
TOP = H - 150


def y_of(row):
    return TOP - row * ROW


def text(x, row, s, size=7.5, font="Helvetica", color=colors.black, right=False):
    c.setFont(font, size)
    c.setFillColor(color)
    (c.drawRightString if right else c.drawString)(x, y_of(row), s)
    c.setFillColor(colors.black)


def rule(x0, x1, row, width=1.2):
    c.setLineWidth(width)
    c.line(x0, y_of(row) - 2.5, x1, y_of(row) - 2.5)


def band(x0, x1, row, label):
    c.setFillColor(colors.HexColor("#DCE6F2"))
    c.rect(x0, y_of(row) - 3, x1 - x0, ROW + 2, stroke=0, fill=1)
    text(x0 + 2, row, label, size=11)


# title block
c.setFont("Helvetica-Bold", 15); c.drawString(40, H - 70, "Northwind Securities")
c.setFont("Helvetica-Bold", 7.5); c.drawString(40, H - 84, "Contoso Capital 2025 H1 Broker Vote")
c.setFont("Helvetica", 14); c.drawString(470, H - 76, "CONTOSO")

# ---------------- left column: four small tables ----------------
L0, L1 = 40, 300


def small_table(start, head, rows):
    text(L0, start, head[0], size=7)
    text(L0 + 120, start, "Rank", size=7)
    text(L0 + 190, start, "Tier", size=7)
    text(L0 + 215, start, "Voter Participation", size=7)
    rule(L0, L1, start)
    for i, (name, rank, tier, part) in enumerate(rows, 1):
        text(L0, start + i, name)
        text(L0 + 120, start + i, rank)
        text(L0 + 192, start + i, tier)
        text(L0 + 230, start + i, part)


band(L0, L0 + 150, 0, "BREAKDOWN")
small_table(1, ["RESOURCE PROVIDER"], [("Northwind Securities", "4 out of 50", "2", "17 out of 17")])
small_table(5, ["CATEGORIES"], [("Corporate Access", "1 out of 28", "1", "9 out of 12"),
                                ("Research", "5 out of 42", "3", "10 out of 14"),
                                ("Sales", "2 out of 40", "1", "13 out of 13")])
small_table(11, ["DEPARTMENTS"], [("Equity", "3 out of 44", "1", "11 out of 11"),
                                  ("Credit", "3 out of 17", "3", "2 out of 2"),
                                  ("Trading", "3 out of 30", "2", "3 out of 3"),
                                  ("Overlay", "10 out of 12", "4", "1 out of 1")])
small_table(17, ["REGIONS"], [("North America", "4 out of 49", "1", "15 out of 17"),
                              ("Europe", "5 out of 15", "3", "5 out of 12"),
                              ("Asia Pacific", "6 out of 11", "4", "2 out of 7")])

# vector chart: stacked horizontal bars + legend, inside a frame
band(L0, L0 + 200, 23, "PEER ANALYSIS BY CATEGORY")
fx0, fy0, fx1, fy1 = L0 + 5, y_of(40), L1 - 10, y_of(24) - 4
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
        c.rect(x, by, v * 1.15, 9, stroke=0, fill=1)
        x += v * 1.15
    c.setFillColor(colors.black)
for i, (name, col) in enumerate(series):
    ly = fy0 + 60 - i * 11
    c.setFillColor(colors.HexColor(col)); c.rect(fx1 - 75, ly, 5, 5, stroke=0, fill=1)
    c.setFillColor(colors.black); c.setFont("Helvetica", 5.5); c.drawString(fx1 - 67, ly, name)

# ---------------- right column: two name/vote tables, same row grid ----------------
R0, R1 = 320, 575
band(R0, R0 + 160, 0, "RESEARCH MENTIONS")
for x, h in ((R0, "NAME"), (R0 + 150, "VOTES"), (R0 + 185, "ANALYST"), (R0 + 225, "FEEDBACK")):
    text(x, 1, h, size=7)
rule(R0, R1, 1)
names = ["TOTAL", "Avery Stone", "Blake Morgan", "Casey Rivera", "Devon Patel", "Emery Chen",
         "Finley Brooks", "Harper Quinn", "Jordan Ellis", "Kai Navarro", "Logan Price",
         "Morgan Hale", "Parker Lane", "Quinn Foster", "Reese Walker", "Rowan Hayes",
         "Sage Kimura", "Taylor Reid", "Blair Novak", "Drew Carter", "Elliot Shaw"]
votes = [82, 8.8, 6.3, 5.6, 4.9, 4.3, 4.1, 3.8, 3.1, 3.1, 3.0, 3.0, 2.8, 2.7, 2.6, 2.5,
         2.4, 2.4, 2.3, 2.2, 2.0]
for i, (n, v) in enumerate(zip(names, votes), 2):
    text(R0, i, n, font="Helvetica-Oblique" if n == "TOTAL" else "Helvetica")
    text(R0 + 172, i, f"{v:g}" if n == "TOTAL" else f"{v:.1f}", right=True)

band(R0, R0 + 160, 25, "SALES MENTIONS")
for x, h in ((R0, "NAME"), (R0 + 150, "VOTES"), (R0 + 185, "ANALYST"), (R0 + 225, "FEEDBACK")):
    text(x, 26, h, size=7)
rule(R0, R1, 26)
for i, (n, v) in enumerate([("TOTAL", "122"), ("Jamie Ortiz", "56.6"), ("Riley Brennan", "33.3"),
                            ("Skyler Moss", "21.3"), ("Alex Romero", "5.4"), ("Cameron Dale", "5.0")], 27):
    text(R0, i, n, font="Helvetica-Oblique" if n == "TOTAL" else "Helvetica")
    text(R0 + 172, i, v, right=True)

c.setFont("Helvetica", 9); c.drawString(40, 40, "Confidential (sample)")
c.drawCentredString(W / 2, 48, "Northwind Securities")
c.save()
print("wrote", out)
