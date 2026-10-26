#!/usr/bin/env python3
"""Build the installable skill folder from this repo.

  python make_skill.py                 # -> dist/pdf-to-markdown/
  python make_skill.py --zip           # also dist/pdf-to-markdown.zip

The skill is skill/SKILL.md plus a copy of pdf2md.py and requirements.txt in
scripts/. Only skill/SKILL.md lives in git; the copy is made here, so there is
one pdf2md.py to edit.
"""
import argparse
import filecmp
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = {"SKILL.md": "skill/SKILL.md",
         "scripts/pdf2md.py": "pdf2md.py",
         "scripts/requirements.txt": "requirements.txt"}


def build(out):
    if os.path.exists(out):
        if os.listdir(out) and not os.path.isfile(os.path.join(out, "SKILL.md")):
            raise SystemExit(f"error: {out} is not empty and is not a built skill (no SKILL.md); "
                             "pick another --out")
        shutil.rmtree(out)
    for dest, src in FILES.items():
        path = os.path.join(out, dest)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        shutil.copyfile(os.path.join(HERE, src), path)
        if not filecmp.cmp(os.path.join(HERE, src), path, shallow=False):
            raise SystemExit(f"error: copy of {src} differs")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(HERE, "dist", "pdf-to-markdown"))
    ap.add_argument("--zip", action="store_true", help="also write <out>.zip")
    a = ap.parse_args(argv)
    out = build(a.out)
    print(f"skill folder: {out}")
    if a.zip:
        print("zip:", shutil.make_archive(out, "zip", os.path.dirname(out), os.path.basename(out)))


if __name__ == "__main__":
    main()
