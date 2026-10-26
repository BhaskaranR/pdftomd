"""Handover checks: the code files survive being sent as PDFs and copied back out.
Run: python tests/test_handover.py            (exit code 0 = all passed)"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CODE = ["pdf2md.py", "sheet2md.py", "chunk2md.py"]
fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {detail}"))
    fails += 0 if cond else 1


def test_ascii():
    """A PDF's monospace font and copy-paste keep plain ASCII; invisible or symbol characters get lost."""
    for name in CODE:
        src = open(os.path.join(ROOT, name), encoding="utf-8").read()
        bad = [(n + 1, line.strip()[:60]) for n, line in enumerate(src.splitlines()) if any(ord(c) > 127 for c in line)]
        check(f"{name}: plain ASCII", not bad, bad[:5])


if __name__ == "__main__":
    test_ascii()
    print("all passed" if not fails else f"{fails} FAILED")
    sys.exit(1 if fails else 0)
