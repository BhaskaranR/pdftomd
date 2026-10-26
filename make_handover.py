#!/usr/bin/env python3
"""Keep the check built into each code file current.

  python3 make_handover.py --refresh   # after a code change: rewrite each file's fingerprint list

After a file has been copied or retyped somewhere else:

  python3 pdf2md.py --verify           # "all N functions and constants match", or the names that differ
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = ["pdf2md.py", "sheet2md.py", "chunk2md.py"]
MARK = "# ---- transcription check "

# The section added to the end of each file (before its `if __name__ == "__main__":`). The same rules
# as verify_transcription.py: code, numbers and string text count; whitespace and comments don't.
SECTION = '''# ---- transcription check -----------------------------------------------------------------------
# After this file is copied or retyped somewhere else, `python3 <this file> --verify` compares every
# function and constant with the fingerprints below; whitespace and comments don't count, any change
# to code, numbers or text in strings does. After a code change: `python3 make_handover.py --refresh`.

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
        out.append(hashlib.sha256("\\x00".join(toks).encode()).hexdigest()[:8] + " " + name)
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
    differ = [w.split()[1] for w in want if w not in have] + \\
        [h.split()[1] + " (not in the list)" for h in have if h not in want]
    name = os.path.basename(path)
    if differ:
        print(f"{name}: {len(differ)} differ: " + ", ".join(dict.fromkeys(differ)))
        return 1
    print(f"{name}: all {len(want)} functions and constants match")
    return 0


FINGERPRINTS = """
"""


'''


def _section_funcs():
    ns = {}
    exec(compile(SECTION, "<section>", "exec"), ns)
    return ns


def fingerprints(src: str) -> list:
    """The fingerprint list a file should carry."""
    return _section_funcs()["_fingerprints"](src)


def embedded(path: str) -> list:
    """The fingerprint list a file carries now."""
    src = open(path, encoding="ascii").read()
    m = re.search(r'^FINGERPRINTS = """\n(.*?)"""', src, re.M | re.S)
    if not m:
        return []
    words = m.group(1).split()
    return [f"{words[i]} {words[i + 1]}" for i in range(0, len(words), 2)]


def refresh(path: str) -> bool:
    """Add the check section if missing, then rewrite the fingerprint list. True if the file changed."""
    src = open(path, encoding="ascii").read()
    old = src
    if MARK in src:                     # replace the whole section: SECTION here is the one source
        a = src.index(MARK)
        b = re.search(r'^FINGERPRINTS = """\n.*?"""\n\n\n', src[a:], re.M | re.S).end() + a
        src = src[:a] + SECTION + src[b:]
    else:
        at = src.index('\nif __name__ == "__main__":')
        src = src[:at].rstrip("\n") + "\n\n\n" + SECTION + src[at + 1:]
    listing = "\n".join(fingerprints(src))
    src = re.sub(r'^FINGERPRINTS = """\n.*?"""', lambda _: f'FINGERPRINTS = """\n{listing}\n"""', src,
                 count=1, flags=re.M | re.S)
    if src != old:
        open(path, "w", encoding="ascii").write(src)
    return src != old


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="rewrite each file's fingerprint list")
    a = ap.parse_args(argv)
    if not a.refresh:
        ap.print_help()
        return 0
    for name in CODE:
        print(f"{name}: {'updated' if refresh(os.path.join(HERE, name)) else 'already current'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
