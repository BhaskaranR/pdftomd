"""Compare sheet2md with Firecrawl's own converter (anydoc, built locally from tests/anyref).
No API key and no upload: both run on this machine.
Run: cargo build --release --manifest-path tests/anyref/Cargo.toml
     python tests/compare_anydoc.py [--save] [files...]   (default: the synthetic set)
--save writes anydoc's output to tests/out/workbooks/<file>.md as the expected result."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import make_workbooks  # noqa: E402
import sheet2md  # noqa: E402

ANYREF = os.path.join(HERE, "anyref", "target", "release", "anyref")
OUT = os.path.join(HERE, "out", "workbooks")


def main(argv):
    save = "--save" in argv
    files = [a for a in argv if a != "--save"]
    if not os.path.exists(ANYREF):
        sys.exit(f"build the reference first: cargo build --release --manifest-path {HERE}/anyref/Cargo.toml")
    if not files:
        tmp = tempfile.mkdtemp(prefix="workbooks_")
        make_workbooks.main(tmp)
        files = sorted(os.path.join(tmp, f) for f in os.listdir(tmp))
    bad = 0
    for path in files:
        ext = os.path.splitext(path)[1].lower()
        ref = subprocess.run([ANYREF, path, ext], capture_output=True)
        try:
            ours = sheet2md.convert(path).markdown.encode()
        except sheet2md.SheetError as e:
            ours = f"ERROR {e.code}".encode()
        if ref.returncode != 0:
            print(f"anydoc error  {os.path.basename(path)}: {ref.stderr.decode().strip()}  (ours: {ours[:60]!r})")
            continue
        same = ref.stdout == ours
        bad += not same
        print(("SAME  " if same else "DIFF  ") + os.path.basename(path))
        if save:
            os.makedirs(OUT, exist_ok=True)
            with open(os.path.join(OUT, os.path.basename(path) + ".md"), "wb") as f:
                f.write(ref.stdout)
    print(f"{len(files) - bad}/{len(files)} identical to anydoc")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
