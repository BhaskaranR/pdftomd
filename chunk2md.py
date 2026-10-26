#!/usr/bin/env python3
"""Cut converted markdown into chunks for search / RAG embeddings.

It reads the markdown's own
structure (headings, paragraphs, lists, tables, code), so every format chunks the same way;
PDF blocks add page numbers and boxes through their markdownSpan.
  - a chunk never spans two sections; whole blocks are packed up to max_chars of `text`
  - paragraphs, list items, code blocks and table rows are never cut; a paragraph longer
    than the limit is split at sentence ends, a table by rows with its header repeated
  - `text` (what gets embedded) = the heading path line + `content`
  - spreadsheet chart tables ("**Chart: X** (kind)") are sections of their own under the sheet

Works on the markdown of every converter (pdf2md.py, sheet2md.py) and on plain markdown;
standard library only, so a search pipeline can use it without the PDF libraries.

  python chunk2md.py doc.md [--chunk-size 2000]     # JSON list of chunks
  from chunk2md import chunk; chunk(result_or_markdown, max_chars=2000)
"""
import argparse
import json
import re
import sys


CHUNK_MAX_CHARS = 2000
CHUNK_MIN_CHARS = 100
_MD_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*$")
_MD_LIST = re.compile(r"^([ \t]*)(?:[-*+]|\d{1,9}[.)])[ \t]+")
_MD_CHART = re.compile(r"^\*\*(Chart(?:: .+?| \d+))\*\* \(")
_MD_PAGE = re.compile(r"^<!-- page \d+ -->$")
_MD_TABLE_RULE = re.compile(r"^\|(?:\s*:?-{3,}:?\s*\|)+\s*$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=\S)")
_UNIT_TYPE = {"heading": "section_header", "chart": "chart", "text": "text", "figure": "figure",
              "table": "table", "list": "list", "code": "code"}


def check_size(max_chars):
    """The chunk size, if it is a whole number of characters >= CHUNK_MIN_CHARS."""
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < CHUNK_MIN_CHARS:
        raise ValueError(f"maxChars must be an integer of at least {CHUNK_MIN_CHARS}")
    return max_chars


def _md_units(md):
    """Markdown -> [{kind, start, end, level?, title?}] with exact character spans, in order.
    Page markers come back as {"kind": "break"} so nothing is joined across them."""
    lines, pos = [], 0
    for ln in md.splitlines(keepends=True):
        lines.append((pos, ln.rstrip("\r\n")))
        pos += len(ln)
    units, i, n = [], 0, len(lines)

    def end_of(j):
        return lines[j][0] + len(lines[j][1])

    def starts_block(t):
        return bool(t.startswith("```") or _MD_HEADING.match(t) or t.lstrip().startswith("|"))

    while i < n:
        start, t = lines[i]
        if not t.strip():
            i += 1
            continue
        nxt = next((lines[k][1] for k in range(i + 1, n) if lines[k][1].strip()), "")
        if _MD_PAGE.match(t.strip()) or (t.strip() == "---" and _MD_PAGE.match(nxt.strip())):
            units.append({"kind": "break", "start": start, "end": end_of(i)})
            i += 1
            continue
        if t.startswith("```") or t.startswith("~~~"):
            fence, j = t[:3], i + 1
            while j < n and not lines[j][1].startswith(fence):
                j += 1
            j = min(j, n - 1)
            units.append({"kind": "code", "start": start, "end": end_of(j)})
            i = j + 1
            continue
        m = _MD_HEADING.match(t)
        if m:
            units.append({"kind": "heading", "start": start, "end": end_of(i),
                          "level": len(m.group(1)), "title": m.group(2).strip()})
            i += 1
            continue
        if t.lstrip().startswith("|"):
            j = i
            while j + 1 < n and lines[j + 1][1].lstrip().startswith("|"):
                j += 1
            units.append({"kind": "table", "start": start, "end": end_of(j)})
            i = j + 1
            continue
        m = _MD_LIST.match(t)
        if m:
            indent, j = len(m.group(1)), i
            while j + 1 < n:
                t2 = lines[j + 1][1]
                m2 = _MD_LIST.match(t2)
                if not t2.strip() or starts_block(t2) or (m2 and len(m2.group(1)) <= indent):
                    break
                j += 1
            units.append({"kind": "list", "start": start, "end": end_of(j)})
            i = j + 1
            continue
        m = _MD_CHART.match(t)
        if m:
            units.append({"kind": "chart", "start": start, "end": end_of(i), "level": 7, "title": m.group(1)})
            i += 1
            continue
        j = i
        while j + 1 < n and lines[j + 1][1].strip() and not starts_block(lines[j + 1][1]) \
                and not _MD_LIST.match(lines[j + 1][1]):
            j += 1
        units.append({"kind": "figure" if t.startswith("![") else "text", "start": start, "end": end_of(j)})
        i = j + 1
    return units


def _split_paragraph(md, u, budget):
    """A paragraph longer than the budget -> exact sub-spans cut at sentence ends (or, for a
    sentence that alone is too long, at the last space that fits)."""
    s, e = u["start"], u["end"]
    cuts = [s] + [s + m.end() for m in _SENTENCE_END.finditer(md[s:e])] + [e]
    sents = [(a, b) for a, b in zip(cuts, cuts[1:]) if md[a:b].strip()]
    pieces, cur = [], None
    for a, b in sents:
        while b - a > budget:                      # one sentence too long: cut at a space
            sp = md.rfind(" ", a, a + budget)
            if sp <= a:
                break
            if cur:
                pieces.append(cur)
                cur = None
            pieces.append((a, sp))
            a = sp + 1
        if cur and b - cur[0] <= budget:
            cur = (cur[0], b)
        else:
            if cur:
                pieces.append(cur)
            cur = (a, b)
    if cur:
        pieces.append(cur)
    return [{"kind": "text", "start": a, "end": len(md[:b].rstrip()), "exact": True} for a, b in pieces]


def _split_table(md, u, budget):
    """A table longer than the budget -> pieces of whole rows, each with the header repeated.
    The piece's span covers its rows; its content adds the header lines."""
    block = md[u["start"]:u["end"]]
    rows, pos = [], u["start"]
    for ln in block.split("\n"):
        rows.append((pos, pos + len(ln), ln))
        pos += len(ln) + 1
    head = rows[:2] if len(rows) > 1 and _MD_TABLE_RULE.match(rows[1][2].strip()) else []
    header = "\n".join(r[2] for r in head)
    body = rows[len(head):]
    pieces, cur = [], []
    for row in body:
        size = len(header) + 1 + sum(len(r[2]) + 1 for r in cur + [row])
        if cur and size > budget:
            pieces.append(cur)
            cur = []
        cur.append(row)
    if cur:
        pieces.append(cur)
    out = []
    for k, rs in enumerate(pieces):
        content = "\n".join(([header] if header else []) + [r[2] for r in rs])
        exact = not head or k == 0
        out.append({"kind": "table", "start": (u["start"] if k == 0 else rs[0][0]), "end": rs[-1][1],
                    "exact": exact, "content": content})
    return out


def _chunk_text(path, content, own_heading):
    crumbs = path[:-1] if own_heading else path
    prefix = " > ".join(crumbs)
    return f"{prefix}\n\n{content}" if prefix else content


def chunk_markdown(src, max_chars: int = CHUNK_MAX_CHARS) -> list:
    """Cut a document into chunks for embeddings. src: a convert() Result, a spreadsheet
    result, a scrape() `data` dict, or plain markdown. Returns [{id, text, content, headingPath,
    markdownSpan, pages, bboxes, types, split?, oversize?}]: `content` is the exact slice
    markdown[span] except for table pieces after the first (split: true, header repeated)."""
    check_size(max_chars)
    if isinstance(src, str):
        md, blocks = src, []
    elif isinstance(src, dict):
        md, blocks = src.get("markdown") or "", src.get("blocks") or []
    else:
        md, blocks = getattr(src, "markdown", "") or "", getattr(src, "blocks", None) or []
    items = [(it["markdownSpan"], p["pageNumber"], it.get("bbox"))
             for p in blocks for it in p.get("items", []) if it.get("markdownSpan")]

    chunks, stack = [], []
    section = {"head": None, "units": []}

    def where(s, e):
        pages, boxes = [], []
        for (a, b), page, bbox in items:
            if a < e and b > s:
                if page not in pages:
                    pages.append(page)
                if bbox:
                    boxes.append({"page": page, "bbox": bbox})
        return sorted(pages), boxes

    def emit(group, path, head):
        """group: consecutive pieces packed into one chunk."""
        own = head is not None and group[0] is head
        if len(group) == 1 and "content" in group[0]:
            content = group[0]["content"]
        elif group[-1].get("content") and group[0] is head:       # heading + first table piece
            content = md[head["start"]:head["end"]] + "\n\n" + group[-1]["content"]
        else:
            content = md[group[0]["start"]:group[-1]["end"]]
        start, end = group[0]["start"], group[-1]["end"]
        text = _chunk_text(path, content, own)
        pages, boxes = where(start, end)
        c = {"id": f"c{len(chunks) + 1}", "text": text, "content": content, "headingPath": list(path),
             "markdownSpan": [start, end], "pages": pages, "bboxes": boxes,
             "types": list(dict.fromkeys(_UNIT_TYPE[p["kind"]] for p in group))}
        if not all(p.get("exact", True) for p in group):
            c["split"] = True
        if len(text) > max_chars:
            c["oversize"] = True
        chunks.append(c)

    def close():
        head, units = section["head"], section["units"]
        path = [t for _, t in stack]
        if not units and (head is None or head["kind"] != "chart"):
            return                                   # heading with nothing under it: path only
        budget = max_chars - len(" > ".join(path)) - 2
        pieces = [head] if head is not None else []
        for u in units:
            if u["kind"] == "break":
                pieces.append(u)
            elif u["end"] - u["start"] > budget and u["kind"] == "text":
                pieces += _split_paragraph(md, u, budget)
            elif u["end"] - u["start"] > budget and u["kind"] == "table":
                pieces += _split_table(md, u, budget)
            else:
                pieces.append(u)
        group = []
        for p in pieces:
            if p["kind"] == "break":
                if group:
                    emit(group, path, head)
                group = []
                continue
            if group and (not p.get("exact", True) or not group[-1].get("exact", True)
                          or "content" in group[-1]):
                emit(group, path, head)
                group = []
            if group:
                own = group[0] is head
                trial = _chunk_text(path, md[group[0]["start"]:p["end"]], own)
                lone_head = len(group) == 1 and group[0] is head    # a heading never stands alone
                if len(trial) > max_chars and not lone_head:
                    emit(group, path, head)
                    group = []
                elif group[0] is head and len(group) == 1 and "content" in p:
                    group.append(p)                  # heading rides with the first table piece
                    emit(group, path, head)
                    group = []
                    continue
            group.append(p)
        if group:
            emit(group, path, head)

    for u in _md_units(md):
        if u["kind"] in ("heading", "chart"):
            close()
            while stack and stack[-1][0] >= u["level"]:
                stack.pop()
            stack.append((u["level"], u["title"]))
            section = {"head": u, "units": []}
        else:
            section["units"].append(u)
    close()
    return chunks


chunk = chunk_markdown


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--verify"]:
        sys.exit(verify())
    if argv[:1] == ["--refresh"]:
        sys.exit(refresh())
    ap = argparse.ArgumentParser(description="Cut a markdown file into chunks for embeddings (JSON).")
    ap.add_argument("src", help="markdown file, or - for stdin")
    ap.add_argument("--chunk-size", type=int, default=CHUNK_MAX_CHARS, metavar="N",
                    help=f"largest chunk in characters (default {CHUNK_MAX_CHARS})")
    a = ap.parse_args(argv)
    md = sys.stdin.read() if a.src == "-" else open(a.src, encoding="utf-8").read()
    try:
        out = chunk_markdown(md, a.chunk_size)
    except ValueError as e:
        sys.exit(f"error: {e}")
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")


# ---- transcription check -----------------------------------------------------------------------
# After this file is copied or retyped somewhere else, `python3 <this file> --verify` compares every
# function and constant with the fingerprints below; whitespace and comments don't count, any change
# to code, numbers or text in strings does. After a code change: `python3 <this file> --refresh`.

def _fingerprints(src):
    """['<8 hex> <name>'] for each top-level function, class and constant, in file order."""
    import ast
    import hashlib
    import io as _io
    import tokenize
    lines = src.splitlines(keepends=True)
    skip = {"COMMENT", "NL", "NEWLINE", "ENDMARKER"}
    out = []
    for node in ast.parse(src).body:
        if not isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Assign)):
            continue
        name = getattr(node, "name", None) or getattr(node.targets[0], "id", None)
        if not name or name == "FINGERPRINTS":
            continue
        seg = lines[node.lineno - 1:node.end_lineno]
        toks, depth, start = [], 0, None
        for t in tokenize.generate_tokens(_io.StringIO("".join(seg)).readline):
            kind = tokenize.tok_name[t.type]
            if kind == "FSTRING_START":          # Python 3.12+: rebuild the whole f-string
                depth += 1
                start = start or t.start
            elif kind == "FSTRING_END":
                depth -= 1
                if depth == 0:
                    (r0, c0), (r1, c1) = start, t.end
                    text = "".join(seg[r0 - 1:r1])[c0:len("".join(seg[r0 - 1:r1 - 1])) + c1]
                    toks.append("STRING:" + text)
                    start = None
            elif depth == 0 and kind not in skip:
                toks.append(kind + ":" + t.string)
        out.append(hashlib.sha256("\x00".join(toks).encode()).hexdigest()[:8] + " " + name)
    return out


def verify(path=None):
    """Compare this file's code with FINGERPRINTS; print the result, return 0 if all match."""
    import os
    path = path or __file__
    try:
        have = _fingerprints(open(path, encoding="utf-8").read())
    except SyntaxError as e:
        print(f"{os.path.basename(path)}: does not parse at line {e.lineno} (check indentation there)")
        return 1
    want = FINGERPRINTS.split()
    want = [f"{want[i]} {want[i + 1]}" for i in range(0, len(want), 2)]
    differ = [w.split()[1] for w in want if w not in have] + \
        [h.split()[1] + " (not in the list)" for h in have if h not in want]
    name = os.path.basename(path)
    if differ:
        print(f"{name}: {len(differ)} differ: " + ", ".join(dict.fromkeys(differ)))
        return 1
    print(f"{name}: all {len(want)} functions and constants match")
    return 0


def refresh(path=None):
    """Rewrite FINGERPRINTS (below) from this file's current code. Return 0."""
    import os
    import re as _re
    path = path or __file__
    src = open(path, encoding="ascii").read()
    listing = "\n".join(_fingerprints(src))
    new = _re.sub(r'^FINGERPRINTS = """\n.*?"""', lambda _: 'FINGERPRINTS = """\n' + listing + '\n"""', src,
                  count=1, flags=_re.M | _re.S)
    if new != src:
        open(path, "w", encoding="ascii").write(new)
    print(f"{os.path.basename(path)}: fingerprint list {'updated' if new != src else 'already current'}")
    return 0


FINGERPRINTS = """
9259cd33 CHUNK_MAX_CHARS
a266394f CHUNK_MIN_CHARS
8901fa45 _MD_HEADING
60c24695 _MD_LIST
a4335ded _MD_CHART
3d10b7f2 _MD_PAGE
4f590e1d _MD_TABLE_RULE
27c143a9 _SENTENCE_END
136a1219 _UNIT_TYPE
b12f2607 check_size
d6dcbed1 _md_units
d18f4cb3 _split_paragraph
d091b840 _split_table
da623149 _chunk_text
13ffa77a chunk_markdown
e4aecbbf chunk
1bf6d0b1 main
a3f20c3b _fingerprints
cc642faa verify
b3a8ba0f refresh
"""


if __name__ == "__main__":
    main()
