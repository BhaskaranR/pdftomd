"""chunk(): markdown -> pieces for embeddings. Invented text and the synthetic samples only.
Run: python tests/test_chunk.py            (exit code 0 = all passed)"""
import contextlib
import io
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import chunk2md  # noqa: E402
import pdf2md  # noqa: E402
from chunk2md import chunk  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def eq(name, got, want):
    check(name, got == want, f"\n  got:  {got!r}\n  want: {want!r}")


def sentences(n, seed="Loans"):
    return " ".join(f"{seed} grew in region {i} because branches opened earlier than planned." for i in range(n))


def basics():
    md = ("# Report\n\n## Loans\n\nIntro para.\n\n| Branch | Amount |\n| --- | --- |\n| North | 10 |\n\n"
          "## Deposits\n\nText.\n")
    cs = chunk(md)
    eq("two sections -> two chunks", len(cs), 2)
    eq("ids", [c["id"] for c in cs], ["c1", "c2"])
    eq("paths", [c["headingPath"] for c in cs], [["Report", "Loans"], ["Report", "Deposits"]])
    eq("content keeps its own heading", cs[0]["content"],
       "## Loans\n\nIntro para.\n\n| Branch | Amount |\n| --- | --- |\n| North | 10 |")
    eq("text = ancestors + content", cs[1]["text"], "Report\n\n## Deposits\n\nText.")
    for c in cs:
        eq(f"span slices exactly ({c['id']})", md[c["markdownSpan"][0]:c["markdownSpan"][1]], c["content"])
    eq("types", cs[0]["types"], ["section_header", "text", "table"])
    eq("no pages for plain markdown", (cs[0]["pages"], cs[0]["bboxes"]), ([], []))
    eq("empty input", chunk(""), [])
    eq("no headings", [c["headingPath"] for c in chunk("Just text.\n\nMore text.")], [[]])
    eq("no headings: text = content", chunk("Just text.")[0]["text"], "Just text.")


def packing():
    paras = [sentences(12, f"P{i}") for i in range(3)]          # ~900 chars each
    md = "# Report\n\n## Loans\n\n" + "\n\n".join(paras) + "\n"
    cs = chunk(md, max_chars=2000)
    check("packed into several chunks", len(cs) >= 2, len(cs))
    check("every chunk within the limit", all(len(c["text"]) <= 2000 for c in cs), [len(c["text"]) for c in cs])
    whole = all(any(p in c["content"] for c in cs) for p in paras)
    check("no paragraph split when each fits", whole)
    eq("later chunks carry the full path", cs[-1]["text"].split("\n\n")[0], "Report > Loans")
    eq("later chunk has no heading line", cs[-1]["content"].startswith("##"), False)
    for c in cs:
        eq(f"packed span exact ({c['id']})", md[slice(*c["markdownSpan"])], c["content"])

    long = sentences(80)                                          # ~6,000 chars, one paragraph
    cs = chunk("## Notes\n\n" + long, max_chars=1000)
    check("long paragraph split", len(cs) >= 6, len(cs))
    check("split pieces within limit", all(len(c["text"]) <= 1000 for c in cs), [len(c["text"]) for c in cs])
    check("split at sentence ends", all(c["content"].rstrip().endswith(".") for c in cs))
    eq("nothing lost", re.sub(r"\s+", " ", " ".join(c["content"].replace("## Notes", "") for c in cs)).strip(), long)


def tables_lists_code():
    rows = "\n".join(f"| R{i} | {i * 7} | note {i} |" for i in range(100))
    md = "## Big\n\n| Name | Value | Note |\n| --- | --- | --- |\n" + rows + "\n"
    cs = chunk(md, max_chars=1000)
    check("big table split", len(cs) >= 3, len(cs))
    check("each piece starts with the header", all(
        "| Name | Value | Note |\n| --- | --- | --- |\n" in c["content"] for c in cs))
    got_rows = [ln for c in cs for ln in c["content"].splitlines() if re.match(r"\| R\d+ ", ln)]
    eq("every row once, in order", got_rows, rows.splitlines())
    check("table pieces within limit", all(len(c["text"]) <= 1000 for c in cs), [len(c["text"]) for c in cs])
    check("table piece span covers its rows", all(
        md[slice(*c["markdownSpan"])].splitlines()[-1] == c["content"].splitlines()[-1] for c in cs))
    eq("table pieces typed", {t for c in cs for t in c["types"]} - {"section_header"}, {"table"})

    items = "\n".join(f"- item {i}: " + sentences(4, f"I{i}") for i in range(5))   # ~300 chars each
    cs = chunk("## List\n\n" + items, max_chars=1000)
    lines = [ln for c in cs for ln in c["content"].splitlines() if ln.startswith("- ")]
    eq("list items whole", lines, items.splitlines())

    code = "```\n" + "\n".join(f"x{i} = {i}" for i in range(200)) + "\n```"
    cs = chunk("## Code\n\n" + code, max_chars=500)
    eq("code block kept whole", [c["content"] for c in cs if "```" in c["content"]], ["## Code\n\n" + code])
    check("oversize flagged", any(c.get("oversize") for c in cs))
    check("oversize only when needed", not any(c.get("oversize") for c in chunk("## A\n\nshort", max_chars=500)))

    md = "## A\n\nfirst page text\n\n---\n\n<!-- page 2 -->\n\nsecond page text\n"
    cs = chunk(md)
    check("page markers dropped", not any("<!-- page" in c["content"] or "\n---" in c["content"] for c in cs),
          [c["content"] for c in cs])
    check("text across a page marker stays in one section", any(
        "first page text" in c["content"] and c["headingPath"] == ["A"] for c in cs))
    check("bad max_chars", _raises(lambda: chunk("x", max_chars=10)))


def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


def pdf():
    r = pdf2md.convert(os.path.join(HERE, "samples", "report.pdf"), images="none")
    cs = chunk(r)
    check("pdf: chunks made", len(cs) >= 4, len(cs))
    for c in cs:
        if not c.get("split"):
            eq(f"pdf: span exact ({c['id']})", r.markdown[slice(*c["markdownSpan"])], c["content"])
    check("pdf: every chunk has pages", all(c["pages"] for c in cs), [c["pages"] for c in cs])
    check("pdf: bboxes on known pages", all(b["page"] in c["pages"] and len(b["bbox"]) == 4
                                           for c in cs for b in c["bboxes"]))
    fin = next((c for c in cs if "Quarterly results" in c["content"]), None)
    eq("pdf: section path", fin and fin["headingPath"], ["Acme Annual Report", "Financial Overview"])
    check("pdf: table typed", fin and "table" in fin["types"], fin and fin["types"])
    furniture = {it["content"] for p in r.blocks for it in p["items"]
                 if it["type"] in ("page_header", "page_footer", "page_number")}
    check("pdf: no headers/footers/page numbers",
          not any(f and f in c["content"].splitlines() for f in furniture for c in cs), furniture)
    check("pdf: within limit unless oversize", all(len(c["text"]) <= 2000 or c.get("oversize") for c in cs))
    for size in (300, 1000, 2000):                 # nothing lost: every non-space character lands in a chunk
        covered = set()
        for c in chunk(r, size):
            covered.update(range(*c["markdownSpan"]))
        lost = [k for u in chunk2md._md_units(r.markdown) if u["kind"] not in ("break", "heading")
                for k in range(u["start"], u["end"]) if not r.markdown[k].isspace() and k not in covered]
        eq(f"pdf: nothing lost at {size}", lost, [])


def spreadsheet():
    import make_workbooks as mw
    path = os.path.join(tempfile.mkdtemp(prefix="chunk_"), "charts.xlsx")
    mw.build(path, mw.WORKBOOKS["charts"])
    import sheet2md
    cs = chunk(sheet2md.convert(path, charts=True))
    paths = [c["headingPath"] for c in cs]
    check("sheet: sheet table under the sheet name", ["Loans"] in paths, paths)
    check("sheet: chart under sheet + chart title", ["Loans", "Chart: Growth rate"] in paths, paths)
    check("sheet: untitled chart", ["Mix", "Chart 2"] in paths, paths)
    check("sheet: chart sheet", ["Dashboard", "Chart: Totals"] in paths, paths)
    check("sheet: no pages", all(c["pages"] == [] for c in cs))
    growth = next(c for c in cs if c["headingPath"] == ["Loans", "Chart: Growth rate"])
    check("sheet: chart chunk holds its table", "| North | 10.5% |" in growth["content"], growth["content"])


def api():
    r = pdf2md.scrape(os.path.join(HERE, "samples", "report.pdf"), chunk={"maxChars": 1000})
    cs = r["data"].get("chunks")
    check("scrape: data.chunks", isinstance(cs, list) and cs, r["data"].keys())
    check("scrape: maxChars honoured", all(len(c["text"]) <= 1000 or c.get("oversize") for c in cs or []))
    check("scrape: pages from blocks even without blocks option", all(c["pages"] for c in cs or []))
    check("scrape: no chunks unless asked", "chunks" not in pdf2md.scrape(
        os.path.join(HERE, "samples", "report.pdf"))["data"])
    check("scrape: chunk=True uses default", len(pdf2md.scrape(
        os.path.join(HERE, "samples", "report.pdf"), chunk=True)["data"]["chunks"]) <= len(cs))
    check("scrape: bad option", _raises(lambda: pdf2md.scrape(
        os.path.join(HERE, "samples", "report.pdf"), chunk={"maxChars": "big"})))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        pdf2md.main([os.path.join(HERE, "samples", "report.pdf"), "--images", "none", "--chunks",
                     "--chunk-size", "1500"])
    data = json.loads(out.getvalue())["data"]
    check("cli: --chunks prints JSON with chunks", data.get("chunks"), list(data))
    check("cli: --chunk-size", all(len(c["text"]) <= 1500 or c.get("oversize") for c in data["chunks"]))
    md_path = os.path.join(tempfile.mkdtemp(prefix="chunkcli_"), "doc.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Report\n\n## Loans\n\nIntro para.\n")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        chunk2md.main([md_path])
    eq("chunk2md cli on a markdown file", [c["headingPath"] for c in json.loads(out.getvalue())],
       [["Report", "Loans"]])


def standalone():
    """chunk2md needs no PDF libraries: a search pipeline can import it alone."""
    import subprocess
    code = ("import sys; [sys.modules.__setitem__(m, None) for m in ('pdfplumber', 'pypdfium2', 'PIL')]; "
            "import chunk2md; print(len(chunk2md.chunk('# A\\n\\ntext')))")
    r = subprocess.run([sys.executable, "-c", code], cwd=os.path.dirname(HERE), capture_output=True, text=True)
    eq("chunk2md imports without PDF libraries", (r.returncode, r.stdout.strip()), (0, "1"))


if __name__ == "__main__":
    for t in (basics, packing, tables_lists_code, pdf, spreadsheet, api, standalone):
        try:
            t()
        except Exception:
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
