"""Layout facts of one PDF page, without its words: gaps between letters, fonts,
shapes, pictures. Safe to paste out of the office.
Usage: python pdf_facts.py FILE.pdf [PAGE]   (PAGE starts at 1, default 1)"""
import sys
from collections import Counter
import pdfplumber

path, pageno = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 1
with pdfplumber.open(path) as pdf:
    p = pdf.pages[pageno - 1]
    chars = p.chars
    print("page", pageno, "of", len(pdf.pages), "size", round(p.width), "x", round(p.height))
    print("chars", len(chars), "space chars", sum(c["text"] == " " for c in chars),
          "unmapped (cid)", sum(c["text"].startswith("(cid:") for c in chars),
          "ligatures", sum(0xFB00 <= ord(c["text"][0]) <= 0xFB06 for c in chars))
    print("fonts", Counter((c["fontname"].split("+")[-1], round(c["size"], 1)) for c in chars).most_common(8))
    # gaps between neighbouring letters on one line, in points and as a share of font size
    lines = {}
    for c in chars:
        lines.setdefault(round(c["top"]), []).append(c)
    pts, rel = Counter(), Counter()
    for ln in lines.values():
        ln.sort(key=lambda c: c["x0"])
        for a, b in zip(ln, ln[1:]):
            g = b["x0"] - a["x1"]
            if -1 < g < 15 and a["text"] != " " and b["text"] != " ":
                pts[round(g * 4) / 4] += 1
                rel[round(g / a["size"], 2)] += 1
    print("gap pt  (gap: count)", sorted(pts.items())[:40])
    print("gap/size(gap: count)", sorted(rel.items())[:40])
    print("upright", Counter(c["upright"] for c in chars))
    fills = Counter(str(r.get("non_stroking_color")) for r in p.rects if r.get("fill"))
    print("rects", len(p.rects), "filled colours", fills.most_common(8))
    big = [r for r in p.rects if r["width"] > 80 and r["height"] > 200]
    print("tall filled panels (x0,x1,top,bottom)", [(round(r["x0"]), round(r["x1"]), round(r["top"]), round(r["bottom"])) for r in big][:5])
    print("curves", len(p.curves), "lines", len(p.lines), "images", len(p.images),
          [(round(i["x0"]), round(i["top"]), round(i["width"]), round(i["height"])) for i in p.images][:10])
