"""Handover checks: the code files survive being copied elsewhere (plain ASCII, built-in --verify).
Run: python tests/test_handover.py            (exit code 0 = all passed)"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CODE = ["pdf2md.py", "sheet2md.py", "chunk2md.py", "msg2md.py"]
fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {detail}"))
    fails += 0 if cond else 1


def test_ascii():
    """Copying keeps plain ASCII; invisible or symbol characters can get lost on the way."""
    for name in CODE:
        src = open(os.path.join(ROOT, name), encoding="utf-8").read()
        bad = [(n + 1, line.strip()[:60]) for n, line in enumerate(src.splitlines()) if any(ord(c) > 127 for c in line)]
        check(f"{name}: plain ASCII", not bad, bad[:5])


def verify(path, *extra):
    r = subprocess.run([sys.executable, path, "--verify", *extra], capture_output=True, text=True, cwd=os.path.dirname(path))
    return r.returncode, (r.stdout + r.stderr).strip()


def test_verify():
    tmp = tempfile.mkdtemp(prefix="handover_verify_")
    for name in CODE:
        code, out = verify(os.path.join(ROOT, name))
        check(f"{name} --verify: the file as committed matches", code == 0 and "match" in out and "differ" not in out, out[-300:])
        src = open(os.path.join(ROOT, name), encoding="ascii").read()
        # whitespace and comments don't count
        noisy = re.sub(r"^(def \w+\(.*\):)$", r"\1   # a copied comment", src, count=3, flags=re.M)
        noisy = noisy.replace("\n\n\n", "\n\n\n\n", 5)
        path = os.path.join(tmp, name)
        open(path, "w", encoding="ascii").write(noisy)
        for other in CODE:
            if other != name:
                shutil.copy(os.path.join(ROOT, other), tmp)
        code, out = verify(path)
        check(f"{name} --verify: extra comments and blank lines still match", code == 0, out[-300:])
        # a changed number inside one function is found and named
        m = re.search(r"^def (\w+)\(.*?\n(?:    .*\n)*?    .*?(\b\d+\b)", src, re.M)
        fn, num = m.group(1), m.group(2)
        start = m.start(2)
        broken = src[:start] + str(int(num) + 1) + src[m.end(2):]
        open(path, "w", encoding="ascii").write(broken)
        code, out = verify(path)
        check(f"{name} --verify: a changed number in {fn}() is named", code == 1 and fn in out, out[-300:])
    shutil.rmtree(tmp, ignore_errors=True)


def run(path, flag):
    r = subprocess.run([sys.executable, path, flag], capture_output=True, text=True, cwd=os.path.dirname(path))
    return r.returncode, (r.stdout + r.stderr).strip()


def test_fingerprints_current():
    """The list inside each file must match its code: run `python3 <file> --refresh` after a change."""
    tmp = tempfile.mkdtemp(prefix="handover_refresh_")
    for name in CODE:
        shutil.copy(os.path.join(ROOT, name), tmp)
    for name in CODE:
        path = os.path.join(tmp, name)
        before = open(path, encoding="ascii").read()
        code, out = run(path, "--refresh")
        check(f"{name}: built-in fingerprint list is current (else: python3 {name} --refresh)",
              code == 0 and open(path, encoding="ascii").read() == before, out)
    shutil.rmtree(tmp, ignore_errors=True)


def test_refresh_after_change():
    tmp = tempfile.mkdtemp(prefix="handover_change_")
    for name in CODE:
        shutil.copy(os.path.join(ROOT, name), tmp)
    for name in CODE:
        path = os.path.join(tmp, name)
        src = open(path, encoding="ascii").read()
        m = re.search(r"^def (\w+)\(.*?\n(?:    .*\n)*?    .*?(\b\d+\b)", src, re.M)
        open(path, "w", encoding="ascii").write(src[:m.start(2)] + str(int(m.group(2)) + 1) + src[m.end(2):])
        code1, _ = run(path, "--verify")
        code2, out2 = run(path, "--refresh")
        code3, out3 = run(path, "--verify")
        check(f"{name}: after a code change --verify fails, --refresh updates the list, then --verify passes",
              code1 == 1 and code2 == 0 and code3 == 0, (out2, out3))
    shutil.rmtree(tmp, ignore_errors=True)


def test_sections_identical():
    """The check section is the same text in every file (there's no separate tool holding it)."""
    secs = []
    for name in CODE:
        src = open(os.path.join(ROOT, name), encoding="ascii").read()
        a = src.find("# ---- transcription check ")
        b = src.find('\nFINGERPRINTS = """\n', a)
        secs.append(src[a:b] if a >= 0 and b > a else None)
    check("check section present and identical in all code files", secs[0] and len(set(secs)) == 1,
          [len(x or "") for x in secs])


if __name__ == "__main__":
    test_ascii()
    test_verify()
    test_fingerprints_current()
    test_refresh_after_change()
    test_sections_identical()
    print("all passed" if not fails else f"{fails} FAILED")
    sys.exit(1 if fails else 0)
