"""Synthetic emails (invented data) for msg2md: Outlook .msg files written byte by byte as
compound files (MS-CFB) with MAPI properties (MS-OXMSG), plus .eml equivalents.
Deterministic: same bytes every run.
Run: python tests/make_mail.py tests/samples/mail"""
import datetime as dt
import os
import struct
import sys
import uuid
from email.message import EmailMessage
from email.utils import format_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# ---------------------------------------------------------------- compound file (MS-CFB v3)
SECTOR, MINI, CUTOFF = 512, 64, 4096
FREESECT, ENDOFCHAIN, FATSECT, NOSTREAM = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFF
MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")


def cfb_key(name):
    return len(name), name.upper()


def cfb_bytes(root):
    """root: {name: bytes (stream) | dict (storage)} -> compound file bytes. Siblings are
    stored as a balanced tree sorted the CFB way (length, then upper-case name)."""
    entries = []

    def add(name, node, typ):
        e = {"name": name, "type": typ, "data": b"", "kids": [], "l": NOSTREAM, "r": NOSTREAM,
             "child": NOSTREAM, "start": ENDOFCHAIN}
        idx = len(entries)
        entries.append(e)
        if isinstance(node, dict):
            e["kids"] = [add(k, node[k], 1 if isinstance(node[k], dict) else 2)
                         for k in sorted(node, key=cfb_key)]
        else:
            e["data"] = node
        return idx

    def tree(ids):
        if not ids:
            return NOSTREAM
        mid = len(ids) // 2
        entries[ids[mid]]["l"] = tree(ids[:mid])
        entries[ids[mid]]["r"] = tree(ids[mid + 1:])
        return ids[mid]

    add("Root Entry", root, 5)
    for e in entries:
        e["child"] = tree(e["kids"])

    def sectors(n, size):
        return -(-n // size)

    # mini stream: every stream under the cutoff
    mini, minifat = b"", []
    for e in entries:
        if e["type"] == 2 and 0 < len(e["data"]) < CUTOFF:
            first = len(mini) // MINI
            n = sectors(len(e["data"]), MINI)
            e["start"] = first
            minifat += [first + k + 1 for k in range(n - 1)] + [ENDOFCHAIN]
            mini += e["data"].ljust(n * MINI, b"\0")
    entries[0]["data"] = mini

    # regular sectors: big streams, mini stream, minifat, directory, then the FAT
    body, fat = [], []

    def place(data, mark=None):
        n = sectors(len(data), SECTOR)
        first = len(body)
        for k in range(n):
            body.append(data[k * SECTOR:(k + 1) * SECTOR].ljust(SECTOR, b"\0"))
            fat.append(mark if mark is not None else (first + k + 1 if k < n - 1 else ENDOFCHAIN))
        return first if n else ENDOFCHAIN

    for e in entries:
        if e["type"] == 2 and len(e["data"]) >= CUTOFF:
            e["start"] = place(e["data"])
    entries[0]["start"] = place(mini) if mini else ENDOFCHAIN
    minifat += [FREESECT] * (-len(minifat) % (SECTOR // 4))
    mf = b"".join(struct.pack("<I", x) for x in minifat)
    first_minifat = place(mf) if mf else ENDOFCHAIN
    n_minifat = sectors(len(mf), SECTOR)

    def entry_bytes(e):
        name = e["name"].encode("utf-16-le")
        size = len(e["data"]) if e["type"] in (2, 5) else 0
        start = e["start"] if e["type"] in (2, 5) and size else (ENDOFCHAIN if e["type"] != 1 else 0)
        return (name.ljust(64, b"\0") + struct.pack("<HBB", len(name) + 2, e["type"], 1)
                + struct.pack("<III", e["l"], e["r"], e["child"]) + b"\0" * 16
                + struct.pack("<I", 0) + b"\0" * 16 + struct.pack("<IQ", start, size))

    unused = b"\0" * 64 + struct.pack("<HBB", 0, 0, 0) + struct.pack("<III", NOSTREAM, NOSTREAM, NOSTREAM) \
        + b"\0" * 16 + b"\0" * 4 + b"\0" * 16 + struct.pack("<IQ", 0, 0)
    dirdata = b"".join(entry_bytes(e) for e in entries)
    while len(dirdata) % SECTOR:
        dirdata += unused
    first_dir = place(dirdata)

    n_fat = 1
    while len(body) + n_fat > n_fat * (SECTOR // 4):
        n_fat += 1
    assert n_fat <= 109, "file too big for a header-only DIFAT"
    fat_ids = list(range(len(body), len(body) + n_fat))
    fat += [FATSECT] * n_fat
    fat += [FREESECT] * (n_fat * (SECTOR // 4) - len(fat))
    fatdata = b"".join(struct.pack("<I", x) for x in fat)

    difat = fat_ids + [FREESECT] * (109 - n_fat)
    header = (MAGIC + b"\0" * 16 + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6) + b"\0" * 6
              + struct.pack("<IIIIIIIII", 0, n_fat, first_dir, 0, CUTOFF, first_minifat, n_minifat,
                            ENDOFCHAIN, 0)
              + b"".join(struct.pack("<I", x) for x in difat))
    assert len(header) == 512
    return header + b"".join(body) + fatdata


# ---------------------------------------------------------------- MAPI properties (MS-OXMSG)
PT_LONG, PT_BOOL, PT_TIME, PT_STRING8, PT_UNICODE, PT_BINARY, PT_OBJECT = \
    0x0003, 0x000B, 0x0040, 0x001E, 0x001F, 0x0102, 0x000D
PS_MAPI = uuid.UUID("00020328-0000-0000-C000-000000000046")
PS_PUBLIC_STRINGS = uuid.UUID("00020329-0000-0000-C000-000000000046")
PSETID_APPOINTMENT = uuid.UUID("00062002-0000-0000-C000-000000000046")


def filetime(t):
    return int((t - dt.datetime(1601, 1, 1, tzinfo=dt.timezone.utc)).total_seconds() * 10_000_000)


def prop_streams(props, header):
    """props: [(id, type, value)] -> {stream: bytes}; strings are str (PT_STRING8: bytes)."""
    out, fixed = {}, b""
    for pid, ptype, val in props:
        tag = (pid << 16) | ptype
        if ptype in (PT_UNICODE, PT_STRING8, PT_BINARY):
            data = (val.encode("utf-16-le") + b"\0\0" if ptype == PT_UNICODE
                    else val + b"\0" if ptype == PT_STRING8 else val)
            out[f"__substg1.0_{tag:08X}"] = data
            fixed += struct.pack("<IIII", tag, 6, len(data), 0)
        elif ptype == PT_LONG:
            fixed += struct.pack("<IIiI", tag, 6, val, 0)
        elif ptype == PT_BOOL:
            fixed += struct.pack("<IIII", tag, 6, 1 if val else 0, 0)
        elif ptype == PT_TIME:
            fixed += struct.pack("<IIQ", tag, 6, filetime(val))
        elif ptype == PT_OBJECT:
            fixed += struct.pack("<IIII", tag, 6, 0xFFFFFFFF, 4)
    out["__properties_version1.0"] = header + fixed
    return out


class Names:
    """Named properties of one .msg (shared by its attached emails)."""

    def __init__(self):
        self.guids, self.entries, self.strings = [], [], b""

    def tag(self, guid, lid):
        if guid == PS_MAPI:
            gi = 1
        elif guid == PS_PUBLIC_STRINGS:
            gi = 2
        else:
            if guid not in self.guids:
                self.guids.append(guid)
            gi = 3 + self.guids.index(guid)
        self.entries.append(struct.pack("<IHH", lid, gi << 1, len(self.entries)))
        return 0x8000 + len(self.entries) - 1

    def storage(self):
        return {"__substg1.0_00020102": b"".join(g.bytes_le for g in self.guids),
                "__substg1.0_00030102": b"".join(self.entries),
                "__substg1.0_00040102": self.strings}


def recipient(name, email, kind=1, addrtype="SMTP"):
    return {"name": name, "email": email, "kind": kind, "addrtype": addrtype}


def attachment(name, data=None, mime=None, cid=None, hidden=False, email=None):
    return {"name": name, "data": data, "mime": mime, "cid": cid, "hidden": hidden, "email": email}


def message(subject=None, sender=None, sent=None, body=None, html=None, rtf=None, recips=(),
            attachments=(), msg_class="IPM.Note", extra=(), codepage=None, string8=False):
    return dict(subject=subject, sender=sender, sent=sent, body=body, html=html, rtf=rtf,
                recips=list(recips), attachments=list(attachments), msg_class=msg_class,
                extra=list(extra), codepage=codepage, string8=string8)


def msg_storage(m, names, top=True):
    def s(pid, text):
        if m["string8"]:
            return (pid, PT_STRING8, text.encode(f"cp{m['codepage'] or 1252}"))
        return (pid, PT_UNICODE, text)

    props = [s(0x001A, m["msg_class"])]
    if m["subject"] is not None:
        props.append(s(0x0037, m["subject"]))
    if m["sender"]:
        name, email, addrtype = m["sender"]
        props += [s(0x0C1A, name), s(0x0C1E, addrtype), s(0x0C1F, email)]
        if addrtype == "SMTP":
            props.append(s(0x5D01, email))
    if m["sent"]:
        props += [(0x0039, PT_TIME, m["sent"]), (0x0E06, PT_TIME, m["sent"])]
    if m["codepage"]:
        props.append((0x3FFD, PT_LONG, m["codepage"]))
    if m["body"] is not None:
        props.append(s(0x1000, m["body"]))
    if m["html"] is not None:
        props += [(0x1013, PT_BINARY, m["html"].encode("utf-8")), (0x3FDE, PT_LONG, 65001)]
    if m["rtf"] is not None:
        props.append((0x1009, PT_BINARY, compress_rtf(m["rtf"].encode("latin-1"))))
    for kind, prop in ((1, 0x0E04), (2, 0x0E03), (3, 0x0E02)):
        shown = "; ".join(r["name"] for r in m["recips"] if r["kind"] == kind)
        if shown:
            props.append(s(prop, shown))
    for pid, ptype, val in m["extra"]:
        if isinstance(pid, tuple):                       # (guid, lid): named property
            pid = names.tag(*pid)
        props.append((pid, ptype, val))
    n_r, n_a = len(m["recips"]), len(m["attachments"])
    head = b"\0" * 8 + struct.pack("<IIII", n_r, n_a, n_r, n_a) + (b"\0" * 8 if top else b"")
    st = prop_streams(props, head)

    for i, r in enumerate(m["recips"]):
        rp = [(0x0C15, PT_LONG, r["kind"]), s(0x3001, r["name"]), s(0x3002, r["addrtype"]),
              s(0x3003, r["email"])]
        if r["addrtype"] == "SMTP":
            rp.append(s(0x39FE, r["email"]))
        st[f"__recip_version1.0_#{i:08X}"] = prop_streams(rp, b"\0" * 8)
    for i, a in enumerate(m["attachments"]):
        ap = [s(0x3707, a["name"]), s(0x3704, a["name"][:12]), s(0x3001, a["name"])]
        if a["mime"]:
            ap.append(s(0x370E, a["mime"]))
        if a["cid"]:
            ap.append(s(0x3712, a["cid"]))
        if a["hidden"]:
            ap.append((0x7FFE, PT_BOOL, True))
        if a["email"] is not None:
            ap += [(0x3705, PT_LONG, 5), (0x3701, PT_OBJECT, None)]
            sub = prop_streams(ap, b"\0" * 8)
            sub["__substg1.0_3701000D"] = msg_storage(a["email"], names, top=False)
        else:
            ap += [(0x3705, PT_LONG, 1), (0x3701, PT_BINARY, a["data"]), (0x0E20, PT_LONG, len(a["data"]))]
            sub = prop_streams(ap, b"\0" * 8)
        st[f"__attach_version1.0_#{i:08X}"] = sub
    return st


def msg_bytes(m):
    names = Names()
    root = msg_storage(m, names)
    root["__nameid_version1.0"] = names.storage()
    return cfb_bytes(root)


# ---------------------------------------------------------------- compressed RTF (MS-OXRTFCP)
PREBUF = (b"{\\rtf1\\ansi\\mac\\deff0\\deftab720{\\fonttbl;}{\\f0\\fnil \\froman \\fswiss \\fmodern "
          b"\\fscript \\fdecor MS Sans SerifSymbolArialTimes New RomanCourier{\\colortbl\\red0\\green0"
          b"\\blue0\r\n\\par \\pard\\plain\\f0\\fs20\\b\\i\\u\\tab\\tx")
assert len(PREBUF) == 207


def crc32_rtf(data):
    crc = 0
    for b in data:
        c = (crc ^ b) & 0xFF
        for _ in range(8):
            c = (c >> 1) ^ 0xEDB88320 if c & 1 else c >> 1
        crc = c ^ (crc >> 8)
    return crc


def compress_rtf(raw):
    """LZFu: greedy longest match (2..17 bytes) in the 4 KB dictionary, as the decoder replays it."""
    d = bytearray(4096)
    d[:207] = PREBUF
    w, out, i = 207, bytearray(), 0
    tokens = []
    while i < len(raw):
        best_len, best_off = 0, 0
        for off in range(4096):
            if off == w or d[off] != raw[i]:
                continue
            # replay the copy: a source byte may be one this copy has just written
            n = 0
            while n < 17 and i + n < len(raw):
                p = (off + n) % 4096
                k = (p - w) % 4096
                if (raw[i + k] if k < n else d[p]) != raw[i + n]:
                    break
                n += 1
            if n > best_len:
                best_len, best_off = n, off
                if n == 17:
                    break
        if best_len >= 2:
            tokens.append(("ref", best_off, best_len))
            for k in range(best_len):
                d[(w + k) % 4096] = d[(best_off + k) % 4096]
            w = (w + best_len) % 4096
            i += best_len
        else:
            tokens.append(("lit", raw[i]))
            d[w] = raw[i]
            w = (w + 1) % 4096
            i += 1
    tokens.append(("ref", w, 2))                          # end marker: offset == write position
    for g in range(0, len(tokens), 8):
        group = tokens[g:g + 8]
        ctrl, chunk = 0, bytearray()
        for k, t in enumerate(group):
            if t[0] == "lit":
                chunk.append(t[1])
            else:
                ctrl |= 1 << k
                chunk += struct.pack(">H", (t[1] << 4) | (t[2] - 2))
        out.append(ctrl)
        out += chunk
    return struct.pack("<IIII", len(out) + 12, len(raw), 0x75465A4C, crc32_rtf(out)) + bytes(out)


# ---------------------------------------------------------------- attachment files
def pdf_bytes(title, rows, encrypt=None, pages=2):
    import io
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, encrypt=encrypt)
    for p in range(pages):
        c.setFont("Helvetica-Bold", 20)
        c.drawString(72, 740, title if p == 0 else f"{title} (continued)")
        c.setFont("Helvetica", 11)
        y = 700
        for r in rows:
            for k, cell in enumerate(r):
                c.drawString(72 + k * 150, y, cell)
            y -= 18
        c.drawString(72, y - 20, f"Figures on page {p + 1} are invented for testing.")
        c.showPage()
    c.save()
    return buf.getvalue()


def xlsx_bytes(tmp):
    import make_workbooks as mw
    path = os.path.join(tmp, "_budget.xlsx")
    mw.build(path, [mw.Sheet("Budget", {"A1": "Item", "B1": "Amount", "A2": "Rent", "B2": 1200,
                                        "A3": "Travel", "B3": 340.5})])
    with open(path, "rb") as f:
        data = f.read()
    os.remove(path)
    return data


SENT = dt.datetime(2026, 10, 26, 14, 3, 27, tzinfo=dt.timezone.utc)
ALICE = ("Alice Moreno", "alice.moreno@example.com", "SMTP")
TO = [recipient("Bram Visser", "bram.visser@example.org"), recipient("Chen Wei", "chen.wei@example.org")]
CC = [recipient("Dana Okafor", "dana.okafor@example.net", kind=2)]
BCC = [recipient("Eli Navarro", "eli.navarro@example.net", kind=3)]
ROWS = [["Region", "Q3 sales", "Q4 sales"], ["North", "1,250", "1,410"], ["South", "980", "1,020"]]

HTML = """<html><head><meta charset="utf-8"><style>p {margin:0}</style></head><body>
<h2>Quarterly update</h2>
<p>Hi team,</p>
<p>The numbers are <b>up</b> this quarter. See the <a href="https://example.com/q3">full report</a>.</p>
<ul><li>North: 1,410</li><li>South: 1,020</li></ul>
<table><tr><th>Region</th><th>Q4</th></tr><tr><td>North</td><td>1,410</td></tr>
<tr><td>South</td><td>1,020</td></tr></table>
<p><img src="cid:scan001@example.com" alt="scan"></p>
<p>Regards,<br>Alice <img src="cid:logo@example.com" width="40"></p>
</body></html>"""

RTF_HTML = r"""{\rtf1\ansi\ansicpg1252\fromhtml1 \deff0{\fonttbl{\f0\fswiss Arial;}}
{\*\htmltag19 <html>}{\*\htmltag50 <body>}\htmlrtf {\htmlrtf0
{\*\htmltag64 <p>}Meeting moved to {\*\htmltag84 <b>}Thursday{\*\htmltag92 </b>}.\htmlrtf\par\htmlrtf0
{\*\htmltag72 </p>}{\*\htmltag64 <p>}Caf\'e9 at 10:00\htmlrtf\par\htmlrtf0{\*\htmltag72 </p>}
{\*\htmltag58 </body>}{\*\htmltag27 </html>}\htmlrtf }\htmlrtf0}"""

RTF_TEXT = r"""{\rtf1\ansi\ansicpg1252\fromtext \deff0{\fonttbl{\f0\fswiss Arial;}}
\pard Plain note one.\par
Second line with a tab\tab here.\par
}"""

NEWSLETTER = """<html><body>
<img src="https://track.example.com/open.gif" width="1" height="1" alt="">
<table role="presentation" width="100%"><tr><td align="center">
 <table width="600"><tr><td>
  <h1>Weekly digest</h1>
  <p>Welcome to issue #1, a roundup of three invented trends.</p>
  <table><tr><td><img src="https://img.example.com/banner.png" alt="banner"></td></tr></table>
  <h2>Trend one</h2>
  <p>What it is<br>A made-up format for testing.</p>
  <ul><li>Hook: short</li><li>Editing: simple</li></ul>
  <p>Watch it <a href="https://example.com/one">here</a>.</p>
  <table border="1"><tr><th>Metric</th><th>Value</th></tr><tr><td><p class="MsoNormal">Views</p></td>
   <td><p class="MsoNormal">1,200</p></td></tr></table>
 </td></tr></table>
 <table role="presentation"><tr><td>Left column</td><td>Right column</td></tr></table>
</td></tr></table>
<img src="https://track.example.com/px.png" style="display:none">
<img src="https://track.example.com/px2.png" width="0" height="0">
</body></html>"""

RTF_NATIVE = r"""{\rtf1\ansi\ansicpg1252\deff0{\fonttbl{\f0\fswiss Arial;}}
\pard\plain\f0\fs22 Status for {\b October}: on {\i track}.\par
\pard Budget table:\par
\trowd\cellx2000\cellx4000\intbl Item\cell Amount\cell\row
\trowd\cellx2000\cellx4000\intbl Rent\cell 1,200\cell\row
\pard Thanks.\par
}"""


def build_all(out):
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(HERE, "samples", "invoice.png"), "rb") as f:
        scan = f.read()                                   # 85 KB: a real picture
    with open(os.path.join(HERE, "samples", "chart.png"), "rb") as f:
        logo = f.read()                                   # 1.3 KB: logo-sized
    report = pdf_bytes("Sales report", ROWS)
    xlsx = xlsx_bytes(out)
    csv = b"name,score\nAda,91\nGrace,88\n"
    old_xls = cfb_bytes({"Workbook": b"\x09\x08" + b"\0" * 30})
    files = {}

    files["plain.msg"] = msg_bytes(message(
        "Q3 sales report", ALICE, SENT,
        body="Hi Bram,\r\n\r\nAttached are the Q3 figures.\r\n\r\nThanks,\r\nAlice\r\n\r\n"
             "From: Bram Visser\r\nSent: Friday\r\nSubject: figures?\r\n\r\nCan you send them?\r\n",
        recips=TO + CC + BCC,
        attachments=[attachment("report.pdf", report, "application/pdf"),
                     attachment("invoice.png", scan, "image/png"),
                     attachment("budget.xlsx", xlsx),
                     attachment("scores.csv", csv, "text/csv"),
                     attachment("old.xls", old_xls),
                     attachment("notes.txt", b"just notes\n", "text/plain"),
                     attachment("locked.pdf", pdf_bytes("Secret", ROWS, encrypt="pw123", pages=1)),
                     attachment("broken.pdf", b"%PDF-1.4\n%garbage that is not a pdf\n")]))

    files["html.msg"] = msg_bytes(message(
        "Résumé — 季度报告", ("Ops Team", "/O=EXCHANGELABS/OU=EXCHANGE ADMINISTRATIVE GROUP/CN=RECIPIENTS/"
                                     "CN=OPS", "EX"), None, html=HTML,
        attachments=[attachment("image001.png", scan, "image/png", cid="scan001@example.com", hidden=True),
                     attachment("image002.png", logo, "image/png", cid="logo@example.com", hidden=True),
                     attachment("summary.pdf", pdf_bytes("Summary", ROWS[:2], pages=1))]))

    files["rtf_html.msg"] = msg_bytes(message("Moved", ALICE, SENT, rtf=RTF_HTML, recips=TO[:1]))
    files["rtf_text.msg"] = msg_bytes(message("Note", ALICE, SENT, rtf=RTF_TEXT, recips=TO[:1]))
    files["rtf_native.msg"] = msg_bytes(message("Status", ALICE, SENT, rtf=RTF_NATIVE, recips=TO[:1]))
    with open(os.path.join(out, "rtf_native.rtf"), "w", encoding="latin-1", newline="") as f:
        f.write(RTF_NATIVE)                               # anydoc reference input

    start = dt.datetime(2026, 11, 3, 9, 30, tzinfo=dt.timezone.utc)
    files["invite.msg"] = msg_bytes(message(
        "Planning review", ALICE, SENT, body="Agenda: budget, hiring.\r\n",
        recips=[recipient("Bram Visser", "bram.visser@example.org"),
                recipient("Dana Okafor", "dana.okafor@example.net", kind=2)],
        msg_class="IPM.Schedule.Meeting.Request",
        extra=[(0x0060, PT_TIME, start), (0x0061, PT_TIME, start + dt.timedelta(hours=1)),
               ((PSETID_APPOINTMENT, 0x820D), PT_TIME, start),
               ((PSETID_APPOINTMENT, 0x820E), PT_TIME, start + dt.timedelta(hours=1)),
               ((PSETID_APPOINTMENT, 0x8208), PT_UNICODE, "Room 4B")]))

    inner = message("Level 4", ALICE, SENT, body="deepest\r\n")
    for level in (3, 2, 1):
        inner = message(f"Level {level}", ALICE, SENT, body=f"text at level {level}\r\n",
                        attachments=[attachment(f"Level {level + 1}.msg", email=inner)]
                        + ([attachment("deep.csv", csv)] if level == 1 else []))
    files["nested.msg"] = msg_bytes(message("Fwd: chain", ALICE, SENT, body="See below.\r\n",
                                            recips=TO[:1], attachments=[attachment("Level 1.msg", email=inner)]))

    files["ansi.msg"] = msg_bytes(message("Café menu", ("René Dupont", "rene@example.fr", "SMTP"), SENT,
                                          body="Crème brûlée: 6 €\r\n", recips=TO[:1],
                                          codepage=1252, string8=True))
    files["empty.msg"] = msg_bytes(message(None, None, None))
    files["not_email.doc"] = cfb_bytes({"WordDocument": b"\xec\xa5" + b"\0" * 40})

    # .eml equivalents
    e = EmailMessage()
    e["Subject"] = "Q3 sales report"
    e["From"] = "Alice Moreno <alice.moreno@example.com>"
    e["To"] = "Bram Visser <bram.visser@example.org>, Chen Wei <chen.wei@example.org>"
    e["Cc"] = "Dana Okafor <dana.okafor@example.net>"
    e["Date"] = format_datetime(SENT.astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30))))
    e.set_content("Hi Bram,\n\nAttached are the Q3 figures.\n\nThanks,\nAlice\n")
    e.add_attachment(report, maintype="application", subtype="pdf", filename="report.pdf")
    e.add_attachment(csv, maintype="text", subtype="csv", filename="scores.csv")
    files["plain.eml"] = bytes(e)

    h = EmailMessage()
    h["Subject"] = "Résumé — 季度报告"
    h["From"] = "Ops Team <ops@example.com>"
    h["To"] = "Bram Visser <bram.visser@example.org>"
    h["Date"] = format_datetime(SENT)
    h.set_content("Plain fallback.\n")
    h.add_alternative(HTML, subtype="html")
    related = h.get_payload()[1]
    related.add_related(scan, maintype="image", subtype="png", cid="<scan001@example.com>")
    related.add_related(logo, maintype="image", subtype="png", cid="<logo@example.com>")
    files["html.eml"] = bytes(h)

    n = EmailMessage()
    n["Subject"] = "Fwd: chain"
    n["From"] = "Alice Moreno <alice.moreno@example.com>"
    n["To"] = "Bram Visser <bram.visser@example.org>"
    n["Date"] = format_datetime(SENT)
    n.set_content("See below.\n")
    n.add_attachment(e)                                   # message/rfc822
    files["nested.eml"] = bytes(n)

    w = EmailMessage()                                    # a marketing email laid out with tables
    w["Subject"] = "Weekly digest"
    w["From"] = "Digest <digest@example.com>"
    w["To"] = "Bram Visser <bram.visser@example.org>"
    w["Date"] = format_datetime(SENT)
    w.set_content("Plain fallback.\n")
    w.add_alternative(NEWSLETTER, subtype="html")
    files["newsletter.eml"] = bytes(w)

    for name, data in files.items():
        with open(os.path.join(out, name), "wb") as f:
            f.write(data)
    return sorted(files)


if __name__ == "__main__":
    print("\n".join(build_all(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "samples", "mail"))))
