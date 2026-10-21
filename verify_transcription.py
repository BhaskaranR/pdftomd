import ast, hashlib, io, sys, tokenize
# usage: python verify_transcription.py pdf2md.py   -> compare with fingerprints.txt
src = open(sys.argv[1], encoding="utf-8").read()
lines = src.splitlines(keepends=True)
SKIP = {"COMMENT", "NL", "NEWLINE", "ENDMARKER"}
for node in ast.parse(src).body:
    if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Assign)):
        name = getattr(node, "name", None) or node.targets[0].id
        seg = lines[node.lineno - 1:node.end_lineno]
        out, depth, start = [], 0, None
        for t in tokenize.generate_tokens(io.StringIO("".join(seg)).readline):
            kind = tokenize.tok_name[t.type]
            if kind == "FSTRING_START":          # Python 3.12+: rebuild whole f-string
                depth += 1
                start = start or t.start
            elif kind == "FSTRING_END":
                depth -= 1
                if depth == 0:
                    (r0, c0), (r1, c1) = start, t.end
                    text = "".join(seg[r0 - 1:r1])[c0:len("".join(seg[r0 - 1:r1 - 1])) + c1]
                    out.append("STRING:" + text)
                    start = None
            elif depth == 0 and kind not in SKIP:
                out.append(kind + ":" + t.string)
        print(hashlib.sha256("\x00".join(out).encode()).hexdigest()[:8], name)
