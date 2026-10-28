#!/usr/bin/env python3
"""Email (Outlook .msg, .eml) -> Markdown, attachments included.

One email becomes one document:

  # <subject>

  **From:** Name <address>
  **To:** ... / **Cc:** ... / **Bcc:** ... (when present)
  **Date:** 2026-10-26 14:03 UTC
  **Attachments:** report.pdf, budget.xlsx

  <message text>

  ## Attachment: report.pdf

  <the attachment, read by the caller; its headings pushed down two levels>

Meeting invites show Attendees / Optional / Start / End / Location instead of To and Cc.
The message text is the HTML version when there is one (Firecrawl's html-to-markdown rules),
else Outlook's RTF (unpacked here; HTML wrapped in RTF goes the HTML way), else plain text.
Attached emails are read up to 3 levels deep. Everything else an attachment holds is read by
`read_attachment` (pdf2md passes one that reads PDFs, pictures and workbooks); without it,
attachments are only listed. Pictures under 10 KB (logos, icons) are never read.

Ports (all MIT): the read side of the `cfb` crate (compound files, what anydoc uses for old
Office files); anydoc's RTF reader (src/formats/rtf); firecrawl/html-to-markdown with its
GitHub-flavored and robust-code-block plugins (what Firecrawl uses for web pages).
Standard library only.
"""
import argparse
import codecs
import datetime as dt
import email
import email.policy
import email.utils
import os
import re
import struct
import sys
import unicodedata
import uuid
from html.parser import HTMLParser
from typing import Optional

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
MAX_DEPTH = 3                  # attached emails read this many levels deep
MIN_PICTURE_BYTES = 10 * 1024  # smaller pictures are logos / icons
CONTENT_TYPE_OF = {".msg": "application/vnd.ms-outlook", ".eml": "message/rfc822"}


class MailError(Exception):
    """code: EMAIL_UNREADABLE | EMAIL_UNSUPPORTED."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Result:
    def __init__(self, markdown, metadata, warning=None):
        self.markdown = markdown
        self.metadata = metadata      # subject, from, to, cc, bcc, date, attachments, ...
        self.warning = warning


# ---------------------------------------------------------------- compound file (cfb crate, read side)
SECTOR_FREE = 0xFFFFFFFF
END_OF_CHAIN = 0xFFFFFFFE
FAT_SECTOR = 0xFFFFFFFD
DIFAT_SECTOR = 0xFFFFFFFC
MAX_REGULAR_SECTOR = 0xFFFFFFFA
NO_STREAM = 0xFFFFFFFF
MAX_REGULAR_STREAM_ID = 0xFFFFFFFA
MINI_STREAM_CUTOFF = 4096
OBJ_STORAGE = 1
OBJ_STREAM = 2
OBJ_ROOT = 5


def _bad(msg):
    return MailError("EMAIL_UNREADABLE", f"damaged compound file: {msg}")


class Compound:
    """A compound file (MS-CFB) opened the way the cfb crate opens it permissively
    (CompoundFile::open, as anydoc does): structural errors raise, cosmetic ones are forgiven."""

    def __init__(self, data: bytes):
        if len(data) < 512 or data[:8] != OLE_MAGIC:
            raise MailError("EMAIL_UNREADABLE", "not a compound file")
        self.data = data
        major, bom, shift, mini_shift = struct.unpack_from("<HHHH", data, 26)
        if bom != 0xFFFE:
            raise _bad(f"byte order mark 0x{bom:04X}")
        if major not in (3, 4):
            raise _bad(f"version {major} is not supported")
        if shift != (9 if major == 3 else 12):
            raise _bad(f"sector shift {shift} for version {major}")
        if mini_shift != 6:
            raise _bad(f"mini sector shift {mini_shift}")
        self.ss = 1 << shift
        self.len_mask = 0xFFFFFFFF if major == 3 else 0xFFFFFFFFFFFFFFFF
        (n_fat, first_dir, _sig, cutoff, first_minifat, n_minifat, first_difat,
         n_difat) = struct.unpack_from("<IIIIIIII", data, 44)
        if cutoff != MINI_STREAM_CUTOFF:
            raise _bad(f"mini stream cutoff {cutoff}")
        self.n_sectors = (len(data) - self.ss + self.ss - 1) // self.ss if len(data) > self.ss else 0

        difat = []
        for k in range(109):
            v = struct.unpack_from("<I", data, 76 + 4 * k)[0]
            if v == SECTOR_FREE:
                break
            if v > MAX_REGULAR_SECTOR:
                raise _bad(f"header DIFAT entry 0x{v:08X}")
            difat.append(v)
        seen = set()
        cur = END_OF_CHAIN if first_difat == SECTOR_FREE else first_difat
        per = self.ss // 4
        while cur not in (END_OF_CHAIN, SECTOR_FREE):
            if cur > MAX_REGULAR_SECTOR or cur >= self.n_sectors:
                raise _bad(f"DIFAT sector {cur}")
            if cur in seen:
                raise _bad("DIFAT chain loops")
            seen.add(cur)
            sec = self.sector(cur)
            for k in range(per - 1):
                v = struct.unpack_from("<I", sec, 4 * k)[0]
                if v != SECTOR_FREE:
                    if v > MAX_REGULAR_SECTOR:
                        raise _bad(f"DIFAT entry 0x{v:08X}")
                    difat.append(v)
            cur = struct.unpack_from("<I", sec, 4 * (per - 1))[0]
        while len(difat) > 109 and len(difat) > n_fat and difat[-1] == 0:
            difat.pop()

        fat = []
        for s in difat:
            if s >= self.n_sectors:
                raise _bad(f"FAT sector {s}")
            fat.extend(struct.unpack(f"<{per}I", self.sector(s)))
        while len(fat) > self.n_sectors and fat[-1] == SECTOR_FREE:
            fat.pop()
        self.fat = fat

        dirdata = self._chain_bytes(first_dir)
        self.entries = [self._entry(dirdata[o:o + 128]) for o in range(0, len(dirdata) - 127, 128)]
        if not self.entries or self.entries[0]["type"] != OBJ_ROOT:
            raise _bad("no root entry")
        if first_minifat == END_OF_CHAIN or n_minifat == 0:
            self.minifat = []
        else:
            mf = self._chain_bytes(first_minifat)
            self.minifat = list(struct.unpack(f"<{len(mf) // 4}I", mf[:len(mf) // 4 * 4]))
        root = self.entries[0]
        self.mini = self._chain_bytes(root["start"])[:root["size"]] if root["size"] else b""
        self._kids = {}

    def sector(self, i):
        off = (i + 1) * self.ss
        if i >= self.n_sectors or off >= len(self.data):
            raise _bad(f"sector {i} is past the end of the file")
        return self.data[off:off + self.ss].ljust(self.ss, b"\0")

    def _chain(self, start, table, limit):
        out, seen, cur = [], set(), start
        while cur != END_OF_CHAIN:
            if cur > MAX_REGULAR_SECTOR or cur >= limit or cur >= len(table):
                raise _bad(f"chain points at sector {cur}")
            if cur in seen:
                raise _bad("chain loops")
            seen.add(cur)
            out.append(cur)
            cur = table[cur]
        return out

    def _chain_bytes(self, start):
        if start == END_OF_CHAIN:
            return b""
        return b"".join(self.sector(s) for s in self._chain(start, self.fat, self.n_sectors))

    def _entry(self, raw):
        name_len = struct.unpack_from("<H", raw, 64)[0]
        if name_len > 64:
            raise _bad(f"name length {name_len}")
        if name_len % 2:
            raise _bad("odd name length")
        n = name_len // 2 - 1 if name_len else 0
        try:
            name = raw[:2 * n].decode("utf-16-le")
        except UnicodeDecodeError:
            raise _bad("name is not UTF-16")
        typ, _color = raw[66], raw[67]
        if typ not in (0, OBJ_STORAGE, OBJ_STREAM, OBJ_ROOT):
            raise _bad(f"object type {typ}")
        left, right, child = struct.unpack_from("<III", raw, 68)
        for v in (left, right, child):
            if v != NO_STREAM and v > MAX_REGULAR_STREAM_ID:
                raise _bad(f"sibling id {v}")
        start, size = struct.unpack_from("<IQ", raw, 116)
        size &= self.len_mask
        if typ == OBJ_STORAGE:
            start, size = 0, 0
        if typ == OBJ_ROOT:
            name = "Root Entry"
        return {"name": name, "type": typ, "left": left, "right": right, "child": child,
                "start": start, "size": size}

    def children(self, sid) -> dict:
        """{upper-case name: entry id} of a storage (names compare case-insensitively)."""
        if sid in self._kids:
            return self._kids[sid]
        out, seen, stack = {}, set(), [self.entries[sid]["child"]]
        while stack:
            i = stack.pop()
            if i == NO_STREAM:
                continue
            if i >= len(self.entries) or i in seen:
                raise _bad("directory tree is broken")
            seen.add(i)
            e = self.entries[i]
            if e["type"] in (OBJ_STORAGE, OBJ_STREAM):
                out[e["name"].upper()] = i
            stack += [e["left"], e["right"]]
        self._kids[sid] = out
        return out

    def stream(self, sid) -> bytes:
        e = self.entries[sid]
        size = e["size"]
        if size == 0:
            return b""
        if size < MINI_STREAM_CUTOFF:
            n_mini = len(self.mini) // 64
            chain = self._chain(e["start"], self.minifat, n_mini)
            data = b"".join(self.mini[s * 64:(s + 1) * 64] for s in chain)
        else:
            data = self._chain_bytes(e["start"])
        if len(data) < size:
            raise _bad(f"stream {e['name']!r} is shorter than its size")
        return data[:size]


# ---------------------------------------------------------------- MAPI properties (MS-OXMSG)
PT_SHORT = 0x0002
PT_LONG = 0x0003
PT_FLOAT = 0x0004
PT_DOUBLE = 0x0005
PT_CURRENCY = 0x0006
PT_APPTIME = 0x0007
PT_ERROR = 0x000A
PT_BOOLEAN = 0x000B
PT_OBJECT = 0x000D
PT_I8 = 0x0014
PT_STRING8 = 0x001E
PT_UNICODE = 0x001F
PT_SYSTIME = 0x0040
PT_BINARY = 0x0102
PS_MAPI = "00020328-0000-0000-c000-000000000046"
PS_PUBLIC_STRINGS = "00020329-0000-0000-c000-000000000046"
PSETID_APPOINTMENT = "00062002-0000-0000-c000-000000000046"
LID_LOCATION = 0x8208
LID_START = 0x820D
LID_END = 0x820E

SUBSTG_RE = re.compile(r"__SUBSTG1\.0_([0-9A-F]{4})([0-9A-F]{4})")
CODEPAGES = {65001: "utf-8", 20127: "ascii", 28591: "latin-1", 1200: "utf-16-le", 50220: "iso2022_jp",
             51932: "euc_jp", 936: "gbk", 54936: "gb18030", 950: "big5", 20866: "koi8_r",
             21866: "koi8_u", **{cp: f"iso8859_{cp - 28590}" for cp in range(28592, 28600)}}


def codec_of(cp) -> str:
    if cp in CODEPAGES:
        return CODEPAGES[cp]
    try:
        return codecs.lookup(f"cp{cp}").name
    except (LookupError, TypeError):
        return "cp1252"


def filetime(v) -> Optional[dt.datetime]:
    if not v or v >= 0x7FFFFFFFFFFFFFFF:
        return None
    try:
        return dt.datetime(1601, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(microseconds=v // 10)
    except OverflowError:
        return None


class Props:
    """The properties of one message, recipient or attachment storage."""

    def __init__(self, cf, sid, header_len, codec="cp1252"):
        self.cf, self.sid, self.codec = cf, sid, codec
        self.kids = cf.children(sid)
        self.values, self.string8 = {}, {}
        fixed = cf.stream(self.kids["__PROPERTIES_VERSION1.0"]) if "__PROPERTIES_VERSION1.0" in self.kids else b""
        for off in range(header_len, len(fixed) - 15, 16):
            tag = struct.unpack_from("<I", fixed, off)[0]
            ptype, pid, v = tag & 0xFFFF, tag >> 16, fixed[off + 8:off + 16]
            if ptype == PT_SHORT:
                self.values[pid] = struct.unpack_from("<h", v)[0]
            elif ptype == PT_LONG:
                self.values[pid] = struct.unpack_from("<i", v)[0]
            elif ptype == PT_FLOAT:
                self.values[pid] = struct.unpack_from("<f", v)[0]
            elif ptype in (PT_DOUBLE, PT_APPTIME):
                self.values[pid] = struct.unpack_from("<d", v)[0]
            elif ptype == PT_BOOLEAN:
                self.values[pid] = struct.unpack_from("<H", v)[0] != 0
            elif ptype in (PT_I8, PT_CURRENCY):
                self.values[pid] = struct.unpack_from("<q", v)[0]
            elif ptype == PT_SYSTIME:
                self.values[pid] = filetime(struct.unpack_from("<Q", v)[0])
        for name, cid in self.kids.items():
            m = SUBSTG_RE.fullmatch(name)
            if not m:
                continue
            pid, ptype = int(m.group(1), 16), int(m.group(2), 16)
            if ptype == PT_UNICODE:
                raw = cf.stream(cid)
                self.values[pid] = raw[:len(raw) // 2 * 2].decode("utf-16-le", "replace").split("\0")[0]
            elif ptype == PT_STRING8:
                self.string8[pid] = cf.stream(cid).split(b"\0")[0]
            elif ptype == PT_BINARY:
                self.values[pid] = cf.stream(cid)
            elif ptype == PT_OBJECT:
                self.values[pid] = ("object", cid)

    def get(self, pid, default=None):
        if pid in self.values:
            return self.values[pid]
        if pid in self.string8:
            return self.string8[pid].decode(self.codec, "replace")
        return default

    def text(self, pid) -> str:
        v = self.get(pid)
        return v.strip() if isinstance(v, str) else ""


def named_ids(cf) -> dict:
    """(property set guid, numeric id) -> property id, from __nameid_version1.0."""
    root = cf.children(0)
    if "__NAMEID_VERSION1.0" not in root:
        return {}
    kids = cf.children(root["__NAMEID_VERSION1.0"])

    def stream(n):
        return cf.stream(kids[n]) if n in kids else b""

    guids = stream("__SUBSTG1.0_00020102")
    entries = stream("__SUBSTG1.0_00030102")
    out = {}
    for off in range(0, len(entries) - 7, 8):
        lid, gk, pidx = struct.unpack_from("<IHH", entries, off)
        if gk & 1:                     # named by string: not used here
            continue
        gi = gk >> 1
        if gi == 1:
            guid = PS_MAPI
        elif gi == 2:
            guid = PS_PUBLIC_STRINGS
        elif 16 * (gi - 3) + 16 <= len(guids):
            b = guids[16 * (gi - 3):16 * (gi - 2)]
            guid = str(uuid.UUID(bytes_le=b))
        else:
            continue
        out[(guid, lid)] = 0x8000 + pidx
    return out


# ---------------------------------------------------------------- the email model
class Attachment:
    def __init__(self, name, data=None, mime=None, cid=None, inline=False, email=None):
        self.name, self.data, self.mime, self.cid = name, data, mime, cid
        self.inline = inline           # a picture placed in the message text, not a paperclip file
        self.email = email             # () -> Mail, for an attached email
        self.placed = False

    @property
    def size(self):
        return len(self.data) if self.data is not None else 0


class Mail:
    def __init__(self):
        self.subject = ""
        self.sender = None             # (name, address or None)
        self.to, self.cc, self.bcc = [], [], []
        self.sent = None               # datetime, UTC
        self.invite = None             # {"start", "end", "location"} for meeting invites
        self.html = None               # str
        self.rtf = None                # compressed RTF bytes
        self.rtf_in_sync = None
        self.text = None               # plain text
        self.attachments = []          # in email order, inline pictures included
        self.kind = ".msg"


def _person(name, address):
    name, address = (name or "").strip(), (address or "").strip()
    if address.startswith("/") or "@" not in address:      # Exchange internal (/O=EXCHANGELABS/...)
        address = ""
    if name.strip("'\"") == address:
        name = ""
    return (name, address or None) if (name or address) else None


def read_msg(data: bytes) -> Mail:
    cf = Compound(data)
    if "__PROPERTIES_VERSION1.0" not in cf.children(0) or \
            not any(SUBSTG_RE.fullmatch(n) for n in cf.children(0)):
        raise MailError("EMAIL_UNSUPPORTED", "a compound file, but not an Outlook email")
    return _message(cf, 0, 32, named_ids(cf))


def _message(cf, sid, header_len, names) -> Mail:
    p = Props(cf, sid, header_len)
    cp = p.get(0x3FFD) or p.get(0x3FDE)
    p.codec = codec_of(cp) if isinstance(cp, int) else "cp1252"
    m = Mail()
    m.subject = " ".join(p.text(0x0037).split())
    addrtype = p.text(0x0C1E).upper()
    addr = p.text(0x5D01) or p.text(0x5D02) or (p.text(0x0C1F) if addrtype in ("SMTP", "") else "")
    m.sender = _person(p.text(0x0C1A) or p.text(0x0042), addr)
    m.sent = p.get(0x0039) or p.get(0x0E06)

    recips = sorted((n, i) for n, i in p.kids.items() if n.startswith("__RECIP_VERSION1.0_#"))
    for _, rid in recips:
        r = Props(cf, rid, 8, p.codec)
        rtype = r.text(0x3002).upper()
        addr = r.text(0x39FE) or (r.text(0x3003) if rtype in ("SMTP", "") else "")
        who = _person(r.text(0x3001), addr)
        if who:
            {1: m.to, 2: m.cc, 3: m.bcc}.get(r.get(0x0C15, 1) & 0xF, m.to).append(who)
    if not recips:                                    # display lists only (names)
        for prop, lst in ((0x0E04, m.to), (0x0E03, m.cc), (0x0E02, m.bcc)):
            lst += [(n.strip(), None) for n in p.text(prop).split(";") if n.strip()]

    klass = p.text(0x001A)
    if klass.upper().startswith(("IPM.SCHEDULE.MEETING", "IPM.APPOINTMENT")):
        def named(lid):
            pid = names.get((PSETID_APPOINTMENT, lid))
            return p.get(pid) if pid is not None else None
        m.invite = {"start": named(LID_START) or p.get(0x0060), "end": named(LID_END) or p.get(0x0061),
                    "location": (named(LID_LOCATION) or "").strip()}

    html = p.get(0x1013)
    if isinstance(html, bytes):
        html = decode_html(html, p.get(0x3FDE))
    m.html = html if isinstance(html, str) and html.strip() else None
    rtf = p.get(0x1009)
    m.rtf = rtf if isinstance(rtf, bytes) and rtf else None
    m.rtf_in_sync = p.get(0x0E1F)
    body = p.get(0x1000)
    m.text = body if isinstance(body, str) and body.strip() else None
    refs = _cid_refs(m)

    for n, aid in sorted((n, i) for n, i in p.kids.items() if n.startswith("__ATTACH_VERSION1.0_#")):
        a = Props(cf, aid, 8, p.codec)
        name = a.text(0x3707) or a.text(0x3704) or a.text(0x3001)
        method = a.get(0x3705, 1)
        obj = a.get(0x3701)
        cid = a.text(0x3712).strip("<>") or None
        inline = bool(a.get(0x7FFE)) or bool((a.get(0x3714) or 0) & 4) or bool(cid and cid.lower() in refs)
        if method == 5 or (isinstance(obj, tuple) and "__SUBSTG1.0_3701000D" in a.kids
                           and _is_message(cf, a.kids["__SUBSTG1.0_3701000D"])):
            sub = a.kids.get("__SUBSTG1.0_3701000D")
            if sub is None:
                continue
            loader = (lambda s=sub: _message(cf, s, 24, names))
            title = name or Props(cf, sub, 24).text(0x0037) or "attached email"
            if not title.lower().endswith((".msg", ".eml")):
                title += ".msg"
            m.attachments.append(Attachment(title, mime="application/vnd.ms-outlook", email=loader))
        else:
            data = obj if isinstance(obj, bytes) else None
            att = Attachment(name or f"attachment {len(m.attachments) + 1}", data, a.text(0x370E) or None, cid)
            att.inline = inline and data is not None and is_picture(att)    # only pictures sit in the text
            m.attachments.append(att)
    return m


CID_REF_RE = re.compile(r"cid:([^\s\"'<>)\]]+)", re.I)


def _cid_refs(m) -> set:
    """Content ids the message text points at (<img src="cid:..">, [cid:..] in plain text)."""
    text = (m.html or "") + "\n" + (m.text or "")
    if m.rtf:
        try:
            text += "\n" + decompress_rtf(m.rtf).decode("latin-1")
        except MailError:
            pass
    return {r.lower() for r in CID_REF_RE.findall(text)}


def _is_message(cf, sid):
    return "__PROPERTIES_VERSION1.0" in cf.children(sid)


def decode_html(raw: bytes, cpid=None) -> str:
    if isinstance(cpid, int) and cpid:
        return raw.decode(codec_of(cpid), "replace")
    m = re.search(rb"""<meta[^>]+charset=["']?([A-Za-z0-9_\-]+)""", raw[:4096], re.I)
    if m:
        try:
            return raw.decode(m.group(1).decode("ascii"), "replace")
        except LookupError:
            pass
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace")


# ---------------------------------------------------------------- .eml (RFC 5322 / MIME)
def read_eml(data: bytes) -> Mail:
    msg = email.message_from_bytes(data, policy=email.policy.default)
    return _eml_message(msg)


def _addresses(msg, header):
    out = []
    for v in msg.get_all(header, []):
        for name, addr in email.utils.getaddresses([str(v)]):
            who = _person(name, addr)
            if who:
                out.append(who)
    return out


def _leaf_parts(msg):
    """Every non-multipart part in order; an attached email (message/rfc822) is one part,
    not opened (Message.walk() would go inside it)."""
    for part in msg.iter_parts() if msg.is_multipart() else ():
        if part.get_content_type() == "message/rfc822" or not part.is_multipart():
            yield part
        else:
            yield from _leaf_parts(part)


def _part_text(part) -> str:
    """A text part's content; an unknown or wrong charset falls back to UTF-8, then cp1252."""
    try:
        return part.get_content()
    except (LookupError, UnicodeError):
        raw = part.get_payload(decode=True) or b""
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("cp1252", "replace")


def _eml_message(msg) -> Mail:
    m = Mail()
    m.kind = ".eml"
    m.subject = " ".join(str(msg.get("Subject", "") or "").split())
    frm = _addresses(msg, "From")
    m.sender = frm[0] if frm else None
    m.to, m.cc, m.bcc = _addresses(msg, "To"), _addresses(msg, "Cc"), _addresses(msg, "Bcc")
    try:
        d = email.utils.parsedate_to_datetime(str(msg.get("Date"))) if msg.get("Date") else None
    except (TypeError, ValueError, IndexError):
        d = None
    if d is not None:
        m.sent = d.astimezone(dt.timezone.utc) if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)

    body_parts = {}
    for kind in ("html", "plain"):
        part = msg.get_body(preferencelist=(kind,))
        if part is not None:
            body_parts[kind] = part
            text = _part_text(part)
            if kind == "html":
                m.html = text if text.strip() else None
            else:
                m.text = text if text.strip() else None
    chosen = set(id(p) for p in body_parts.values())
    html = m.html or ""
    for part in _leaf_parts(msg):
        if id(part) in chosen:
            continue
        ctype = part.get_content_type()
        if ctype == "message/rfc822":
            inner = part.get_payload()[0] if isinstance(part.get_payload(), list) else part.get_content()
            title = part.get_filename() or (str(inner.get("Subject", "") or "attached email").strip() + ".eml")
            m.attachments.append(Attachment(title, mime=ctype, email=(lambda i=inner: _eml_message(i))))
            continue
        if part.get_content_maintype() == "text" and not part.get_filename() and \
                part.get_content_disposition() != "attachment":
            continue                                   # another text version of the body
        cid = (part.get("Content-ID") or "").strip().strip("<>") or None
        inline = bool(cid and f"cid:{cid}".lower() in html.lower()) or (
            part.get_content_disposition() == "inline" and part.get_content_maintype() == "image")
        try:
            data = part.get_payload(decode=True) or b""
        except Exception:  # noqa: BLE001 - a damaged part is just empty
            data = b""
        name = part.get_filename() or cid or f"attachment {len(m.attachments) + 1}"
        att = Attachment(name, data, ctype, cid)
        att.inline = inline and is_picture(att)
        m.attachments.append(att)
    return m


# ---------------------------------------------------------------- markdown
MD_INLINE = re.compile(r"([\\`*_\[\]<>|#])")


def md_escape(s: str) -> str:
    return MD_INLINE.sub(r"\\\1", s)


def person_md(who) -> str:
    name, addr = who
    if name and addr:
        return f"{md_escape(name)} <{addr}>"
    return f"<{addr}>" if addr else md_escape(name)


def when(t) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def iso(t) -> Optional[str]:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if t else None


FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
HEADING_RE = re.compile(r"^(#{1,6})(?=[ \t]|$)")


def shift_headings(md: str, by: int) -> str:
    """Push every ATX heading down `by` levels (at most ######), code blocks untouched."""
    if by <= 0:
        return md
    out, fence = [], None
    for line in md.split("\n"):
        f = FENCE_RE.match(line)
        if fence:
            if f and f.group(1)[0] == fence[0] and len(f.group(1)) >= len(fence):
                fence = None
        elif f:
            fence = f.group(1)
        else:
            h = HEADING_RE.match(line)
            if h:
                line = "#" * min(6, len(h.group(1)) + by) + line[len(h.group(1)):]
        out.append(line)
    return "\n".join(out)


def plain_text_md(text: str) -> str:
    """Plain message text, kept as written: CRLF -> LF, trailing spaces and blank edges trimmed."""
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n")


CID_TEXT_RE = re.compile(r"\[cid:([^\]\s]+)\]", re.I)


def is_picture(att) -> bool:
    if att.mime and att.mime.lower().startswith("image/"):
        return True
    return os.path.splitext(att.name.lower())[1] in (".png", ".jpg", ".jpeg", ".gif", ".bmp",
                                                     ".tif", ".tiff", ".webp")


def render(mail: Mail, read_attachment=None, depth=0):
    """-> (markdown, warnings, metadata). read_attachment(att, depth) -> (markdown | None,
    problem | None, warnings): None/None = listed only; a problem = could not be read."""
    warnings = []
    by_cid = {a.cid.lower(): a for a in mail.attachments if a.cid}

    def picture(att):
        """Markdown for an inline picture placed in the text ('' for logos / icons)."""
        att.placed = True
        if att.size < MIN_PICTURE_BYTES or read_attachment is None:
            return ""
        md, problem, w = read_attachment(att, depth)
        warnings.extend(f"picture {att.name}: {x}" for x in w)
        if problem:
            warnings.append(f"picture {att.name}: could not be read: {problem}")
        return md or ""

    def cid_picture(cid):
        att = by_cid.get(cid.lower())
        return picture(att) if att is not None else None

    blocks = [f"# {md_escape(mail.subject) if mail.subject else '(no subject)'}"]
    head = []
    if mail.sender:
        head.append(f"**From:** {person_md(mail.sender)}")
    if mail.invite:
        if mail.to:
            head.append("**Attendees:** " + ", ".join(person_md(w) for w in mail.to))
        if mail.cc:
            head.append("**Optional:** " + ", ".join(person_md(w) for w in mail.cc))
        if mail.invite["start"]:
            head.append(f"**Start:** {when(mail.invite['start'])}")
        if mail.invite["end"]:
            head.append(f"**End:** {when(mail.invite['end'])}")
        if mail.invite["location"]:
            head.append(f"**Location:** {md_escape(mail.invite['location'])}")
    else:
        for label, lst in (("To", mail.to), ("Cc", mail.cc), ("Bcc", mail.bcc)):
            if lst:
                head.append(f"**{label}:** " + ", ".join(person_md(w) for w in lst))
    if mail.sent:
        head.append(f"**Date:** {when(mail.sent)}")

    files = [a for a in mail.attachments if not a.inline]
    if files:
        head.append("**Attachments:** " + ", ".join(md_escape(a.name) for a in files))
    if head:
        blocks.append("  \n".join(head))

    body = message_text(mail, cid_picture)
    if body:
        blocks.append(body)
    for att in mail.attachments:
        if att.inline and not att.placed:
            md = picture(att)
            if md:
                blocks.append(md)

    meta_atts = []
    for att in files:
        entry = {"name": att.name, "size": att.size, "contentType": att.mime, "read": False}
        meta_atts.append(entry)
        md, problem, w = None, None, []
        if att.email is not None:
            if depth + 1 > MAX_DEPTH:
                problem = f"attached emails are read {MAX_DEPTH} levels deep"
            else:
                try:
                    inner = att.email()
                    md, w, _ = render(inner, read_attachment, depth + 1)
                except MailError as e:
                    problem = str(e)
            entry["size"] = None
        elif is_picture(att) and att.size < MIN_PICTURE_BYTES:
            continue                                         # logo / icon
        elif read_attachment is not None:
            md, problem, w = read_attachment(att, depth)
        warnings.extend(f"attachment {att.name}: {x}" for x in w)
        if problem:
            warnings.append(f"attachment {att.name}: could not be read: {problem}")
            blocks.append(f"## Attachment: {md_escape(att.name)}\n\n_Could not be read: {md_escape(problem)}._")
        elif md is not None:
            entry["read"] = True
            content = shift_headings(md.strip("\n"), 2)
            blocks.append(f"## Attachment: {md_escape(att.name)}" + (f"\n\n{content}" if content else ""))

    meta = {"title": mail.subject, "contentType": CONTENT_TYPE_OF[mail.kind],
            "from": person_text(mail.sender), "to": [person_text(w) for w in mail.to],
            "cc": [person_text(w) for w in mail.cc], "bcc": [person_text(w) for w in mail.bcc],
            "date": iso(mail.sent), "attachments": meta_atts}
    if mail.invite:
        meta.update({"start": iso(mail.invite["start"]), "end": iso(mail.invite["end"]),
                     "location": mail.invite["location"] or None})
    return "\n\n".join(blocks) + "\n", warnings, meta


def person_text(who):
    if not who:
        return None
    name, addr = who
    return f"{name} <{addr}>" if name and addr else (addr or name)


def message_text(mail: Mail, cid_picture) -> str:
    """The message text as markdown: HTML, else RTF, else plain text."""
    if mail.html:
        return shift_headings(html_to_markdown(mail.html, cid_picture, email=True), 1)
    if mail.rtf and not (mail.rtf_in_sync is False and mail.text):
        try:
            kind, content = rtf_body(decompress_rtf(mail.rtf))
        except MailError:
            kind, content = None, None
        if kind == "html":
            return shift_headings(html_to_markdown(content, cid_picture, email=True), 1)
        if kind == "markdown":
            return content
    if mail.text:
        return CID_TEXT_RE.sub(lambda mt: cid_picture(mt.group(1)) or "", plain_text_md(mail.text)).strip("\n")
    return ""


# ---------------------------------------------------------------- compressed RTF (MS-OXRTFCP)
RTF_PREBUF = (b"{\\rtf1\\ansi\\mac\\deff0\\deftab720{\\fonttbl;}{\\f0\\fnil \\froman \\fswiss \\fmodern "
              b"\\fscript \\fdecor MS Sans SerifSymbolArialTimes New RomanCourier{\\colortbl\\red0\\green0"
              b"\\blue0\r\n\\par \\pard\\plain\\f0\\fs20\\b\\i\\u\\tab\\tx")


def decompress_rtf(data: bytes) -> bytes:
    if len(data) < 16:
        raise MailError("EMAIL_UNREADABLE", "compressed RTF is too short")
    comp_size, raw_size, magic, _crc = struct.unpack_from("<IIII", data)
    if magic == 0x414C454D:                           # "MELA": stored uncompressed
        return data[16:16 + raw_size]
    if magic != 0x75465A4C:                           # "LZFu"
        raise MailError("EMAIL_UNREADABLE", "unknown RTF compression")
    d = bytearray(4096)
    d[:len(RTF_PREBUF)] = RTF_PREBUF
    w = len(RTF_PREBUF)
    out = bytearray()
    src, i, end = data, 16, min(len(data), comp_size + 4)
    while i < end:
        ctrl = src[i]
        i += 1
        for bit in range(8):
            if i >= end:
                break
            if ctrl & (1 << bit):
                if i + 1 >= end:
                    i = end
                    break
                ref = (src[i] << 8) | src[i + 1]
                i += 2
                off, n = ref >> 4, (ref & 0xF) + 2
                if off == w:
                    return bytes(out[:raw_size])
                for k in range(n):
                    c = d[(off + k) % 4096]
                    out.append(c)
                    d[w] = c
                    w = (w + 1) % 4096
            else:
                c = src[i]
                i += 1
                out.append(c)
                d[w] = c
                w = (w + 1) % 4096
    return bytes(out[:raw_size])


# ---------------------------------------------------------------- RTF message text
# Outlook keeps some message text only as RTF. HTML wrapped in RTF (\fromhtml, MS-OXRTFEX) is
# unwrapped and converted like any HTML text; other RTF gives its words, paragraphs and table
# rows (formatting is not kept). Tokens as anydoc's lexer (src/formats/rtf/lexer.rs) reads them.

def _ascii_letter(c):
    return 0x41 <= c <= 0x5A or 0x61 <= c <= 0x7A


def rtf_tokens(b: bytes):
    """("open",) ("close",) ("word", name, param) ("sym", ch) ("hex", byte) ("byte", byte) ("bin", data)"""
    pos, n = 0, len(b)
    while pos < n:
        c = b[pos]
        pos += 1
        if c == 0x7B:
            yield ("open",)
        elif c == 0x7D:
            yield ("close",)
        elif c in (0x0D, 0x0A, 0x00):
            continue
        elif c != 0x5C:
            yield ("byte", c)
        elif pos >= n:
            return
        elif not _ascii_letter(b[pos]):
            s = b[pos]
            pos += 1
            if s in (0x0D, 0x0A):
                yield ("word", "par", None)
            elif s == 0x27:
                pair = b[pos:pos + 2]
                try:
                    if len(pair) < 2:
                        raise ValueError
                    yield ("hex", int(pair.decode("ascii"), 16))
                    pos += 2
                except (ValueError, UnicodeDecodeError):
                    yield ("byte", 0x27)
            else:
                yield ("sym", chr(s))
        else:
            start = pos
            while pos < n and _ascii_letter(b[pos]):
                pos += 1
            name = b[start:pos].decode("ascii")
            param, neg = None, False
            if pos < n and b[pos] == 0x2D:
                neg = True
                pos += 1
            num = pos
            while pos < n and 0x30 <= b[pos] <= 0x39:
                pos += 1
            if pos > num:
                v = int(b[num:pos])
                param = max(-2 ** 31, min(2 ** 31 - 1, -v if neg else v))
            elif neg:
                pos -= 1
            if pos < n and b[pos] == 0x20:
                pos += 1
            if name == "bin":
                end = min(n, pos + max(0, param or 0))
                yield ("bin", b[pos:end])
                pos = end
            else:
                yield ("word", name, param)


RTF_CODEPAGES = {932: "cp932", 936: "gbk", 949: "cp949", 950: "big5", 874: "cp874"}
RTF_CHARSETS = {128: "cp932", 129: "cp949", 134: "gbk", 136: "big5", 161: "cp1253", 162: "cp1254",
                163: "cp1258", 177: "cp1255", 178: "cp1256", 179: "cp1256", 180: "cp1256", 186: "cp1257",
                204: "cp1251", 222: "cp874", 238: "cp1250"}
RTF_SKIP = {"fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "listtable", "listoverridetable",
            "rsidtbl", "generator", "xmlnstbl", "themedata", "colorschememapping", "datastore", "latentstyles",
            "pgdsctbl", "filetbl", "revtbl", "header", "footer", "headerl", "headerr", "headerf", "footerl",
            "footerr", "footerf", "footnote", "nonshppict", "shppict", "fldinst", "mhtmltag", "listtext",
            "pntext", "pntxta", "pntxtb"}
RTF_SYMBOLS = {"lquote": "\u2018", "rquote": "\u2019", "ldblquote": "\u201c", "rdblquote": "\u201d",
               "bullet": "\u2022", "emdash": "\u2014", "endash": "\u2013", "emspace": "\u2003",
               "enspace": "\u2002", "qmspace": "\u2005", "tab": "\t"}


def _rtf_codec(cp):
    if cp in RTF_CODEPAGES:
        return RTF_CODEPAGES[cp]
    return f"cp{cp}" if 1250 <= (cp or 0) <= 1258 else "cp1252"


def rtf_body(raw: bytes):
    """-> ("html", str) for HTML wrapped in RTF, else ("markdown", str): words, paragraphs, tables."""
    if not raw.lstrip().startswith(b"{\\rtf"):
        raise MailError("EMAIL_UNREADABLE", "not RTF")
    mode, default = "native", "cp1252"
    fonts, depth, font_group, font_id = {}, 0, None, None
    for t in rtf_tokens(raw):                              # header pass: code page, font charsets, kind
        if t[0] == "open":
            depth += 1
        elif t[0] == "close":
            if font_group is not None and depth == font_group:
                font_group = None
            depth -= 1
        elif t[0] == "word":
            name, p = t[1], t[2]
            if name == "ansicpg" and p is not None:
                default = _rtf_codec(p)
            elif name == "fromhtml" and mode == "native":
                mode = "html"
            elif name == "fromtext" and mode == "native":
                mode = "text"
            elif name == "fonttbl":
                font_group = depth
            elif font_group is not None and name == "f":
                font_id = p
            elif font_group is not None and name == "fcharset" and font_id is not None and p in RTF_CHARSETS:
                fonts[font_id] = RTF_CHARSETS[p]

    out, pending = [], bytearray()
    st = {"skip": False, "suppress": False, "tag": False, "uc": 1, "codec": default, "intbl": False}
    stack, group_start, star, skip_n = [], False, False, 0
    cells, rows, cell = [], [], []

    def flush():
        if pending:
            (cell if st["intbl"] and mode == "native" else out).append(pending.decode(st["codec"], "replace"))
            pending.clear()

    def emit(s):
        flush()
        (cell if st["intbl"] and mode == "native" else out).append(s)

    def end_table():
        if rows:
            width = max(len(r) for r in rows)
            grid = [r + [""] * (width - len(r)) for r in rows]
            lines = ["| " + " | ".join(c.replace("|", "\\|") for c in grid[0]) + " |",
                     "|" + " --- |" * width] + ["| " + " | ".join(c.replace("|", "\\|") for c in r) + " |"
                                                for r in grid[1:]]
            out.append("\n\n" + "\n".join(lines) + "\n\n")
            rows.clear()

    for t in rtf_tokens(raw):
        kind = t[0]
        if kind == "open":
            flush()
            stack.append(dict(st))
            group_start, star = True, False
            continue
        if kind == "close":
            flush()
            if stack:
                st = stack.pop()
            group_start = False
            continue
        if kind == "sym" and t[1] == "*" and group_start:
            star = True
            continue
        if kind == "word" and group_start:
            group_start = False
            if t[1] == "htmltag" and mode == "html":
                st["skip"], st["tag"] = False, True
                continue
            if star or t[1] in RTF_SKIP:
                st["skip"] = True
                continue
        group_start = False
        if st["skip"]:
            continue
        if kind == "word":
            name, p = t[1], t[2]
            if name == "htmlrtf":
                flush()
                st["suppress"] = p != 0
                continue
            if name == "uc":
                st["uc"] = max(0, p or 0)
                continue
            if name == "f" and p is not None:
                flush()
                st["codec"] = fonts.get(p, default)
                continue
            if st["suppress"] and not st["tag"]:
                continue
            if name == "u" and p is not None:
                code = p + 65536 if p < 0 else p
                flush()
                if 0xD800 <= code <= 0xDBFF:
                    st["high"] = code
                elif 0xDC00 <= code <= 0xDFFF and st.get("high"):
                    emit(chr(0x10000 + ((st.pop("high") - 0xD800) << 10) + (code - 0xDC00)))
                else:
                    emit(chr(code))
                skip_n = st["uc"]
                continue
            if name in ("par", "line", "sect", "page"):
                emit(" " if st["intbl"] and mode == "native" else "\n\n" if mode == "native" else "\n")
            elif name in RTF_SYMBOLS:
                emit(RTF_SYMBOLS[name])
            elif mode == "native" and name in ("intbl", "pard"):
                flush()
                st["intbl"] = name == "intbl"
            elif mode == "native" and name == "cell":
                flush()
                cells.append(" ".join("".join(cell).split()))
                cell.clear()
            elif mode == "native" and name == "row":
                flush()
                if cell and "".join(cell).strip():
                    cells.append(" ".join("".join(cell).split()))
                cell.clear()
                rows.append(list(cells))
                cells.clear()
            continue
        if st["suppress"] and not st["tag"]:
            continue
        if kind == "sym":
            ch = t[1]
            if ch in "\\{}":
                emit(ch)
            elif ch == "~":
                emit("\xa0")
            elif ch == "_":
                emit("\u2011")
            continue
        if kind in ("hex", "byte"):
            if skip_n:
                skip_n -= 1
                continue
            if mode == "native" and rows and not st["intbl"] and kind == "byte" and t[1] not in b" \t":
                end_table()
            pending.append(t[1])
    flush()
    end_table()
    text = "".join(out)
    if mode == "html":
        return "html", text
    return "markdown", plain_text_md(text)


# ---------------------------------------------------------------- HTML -> markdown
# A port of firecrawl/html-to-markdown as Firecrawl runs it (NewConverter("", true, nil) +
# GitHubFlavored + RobustCodeBlock), on a tree built like an HTML5 parser for the cases emails
# use. <head> is dropped, as Firecrawl's clean-up does before converting.

GO_WS = "\t\n\v\f\r \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a" \
        "\u2028\u2029\u202f\u205f\u3000"


def go_trim(s):
    return s.strip(GO_WS)


def go_space(c):
    return c in GO_WS


def go_punct(c):
    return unicodedata.category(c).startswith("P")


class Node:
    __slots__ = ("name", "data", "attrs", "children", "parent")

    def __init__(self, name, data="", attrs=None):
        self.name, self.data, self.attrs = name, data, attrs if attrs is not None else {}
        self.children, self.parent = [], None

    def append(self, child):
        child.parent = self
        self.children.append(child)
        return child

    def index(self):
        return next(i for i, c in enumerate(self.parent.children) if c is self)

    @property
    def prev(self):
        if self.parent is None:
            return None
        i = self.index()
        return self.parent.children[i - 1] if i > 0 else None

    @property
    def next(self):
        if self.parent is None:
            return None
        i = self.index()
        return self.parent.children[i + 1] if i + 1 < len(self.parent.children) else None

    def elements(self):
        return [c for c in self.children if not c.name.startswith("#")]

    def next_element(self):
        if self.parent is None:
            return None
        sibs = self.parent.children
        for c in sibs[self.index() + 1:]:
            if not c.name.startswith("#"):
                return c
        return None

    def find(self, name):
        """Descendants named `name`, in document order (a loop: emails nest deeper than Python recurses)."""
        work = list(reversed(self.children))
        while work:
            c = work.pop()
            if c.name == name:
                yield c
            work.extend(reversed(c.children))

    def first(self, name):
        return next(self.find(name), None)

    def has_ancestor(self, *names):
        p = self.parent
        while p is not None:
            if p.name in names:
                return True
            p = p.parent
        return False

    def text(self):
        return _collect_text(self)


VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
        "track", "wbr", "keygen"}
HEAD_TAGS = {"meta", "title", "link", "style", "base", "script", "noscript"}
CLOSES_P = {"address", "article", "aside", "blockquote", "center", "details", "dialog", "dir", "div", "dl",
            "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
            "hgroup", "hr", "main", "menu", "nav", "ol", "p", "pre", "section", "summary", "table", "ul", "li",
            "dd", "dt", "listing", "xmp", "plaintext"}
SCOPE = {"table", "td", "th", "caption", "html", "body", "button", "marquee", "object", "applet", "template"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


class _OpenElements(list):
    """The stack of open elements, counting names so 'is a <p> open?' needs no scan."""

    def __init__(self, nodes):
        super().__init__(nodes)
        self.open = {}
        for n in nodes:
            self.open[n.name] = self.open.get(n.name, 0) + 1

    def append(self, node):
        super().append(node)
        self.open[node.name] = self.open.get(node.name, 0) + 1

    def pop(self):
        node = super().pop()
        self.open[node.name] -= 1
        return node

    def truncate(self, i):
        while len(self) > i:
            self.pop()

    def has(self, names):
        return any(self.open.get(n) for n in names)


class _TreeBuilder(HTMLParser):
    """html.parser tokens -> a DOM shaped like an HTML5 parser's: html/head/body, void
    elements, implied end tags (p, li, dt/dd, table rows and cells), implicit tbody/tr."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.doc = Node("#document")
        self.html = self.doc.append(Node("html"))
        self.head = self.html.append(Node("head"))
        self.body = self.html.append(Node("body"))
        self.stack = _OpenElements([self.html, self.head])
        self.in_body = False
        self.skip_newline = False

    def _to_body(self):
        if not self.in_body:
            self.in_body = True
            self.stack = _OpenElements([self.html, self.body])

    def _close_until(self, names, stop=SCOPE):
        """Pop up to and including the nearest open element in `names`, unless a scope boundary
        comes first. True when one was closed."""
        if not self.stack.has(names):
            return False
        for i in range(len(self.stack) - 1, 1, -1):
            n = self.stack[i].name
            if n in names:
                self.stack.truncate(i)
                return True
            if n in stop:
                return False
        return False

    def handle_starttag(self, tag, attrs):
        attrs = {k: (v if v is not None else "") for k, v in attrs}
        if tag == "html":
            for k, v in attrs.items():
                self.html.attrs.setdefault(k, v)
            return
        if tag == "head":
            return
        if tag == "body":
            self._to_body()
            for k, v in attrs.items():
                self.body.attrs.setdefault(k, v)
            return
        if not self.in_body and tag in HEAD_TAGS:
            node = self.stack[-1].append(Node(tag, attrs=attrs))
            if tag not in VOID:
                self.stack.append(node)
            return
        self._to_body()
        if tag in CLOSES_P:
            self._close_until({"p"})
        if tag == "li":
            self._close_until({"li"}, SCOPE | {"ul", "ol"})
        elif tag in ("dd", "dt"):
            self._close_until({"dd", "dt"}, SCOPE | {"dl"})
        elif tag in HEADINGS and self.stack[-1].name in HEADINGS:
            self.stack.pop()
        elif tag == "option":
            self._close_until({"option"})
        elif tag in ("tbody", "thead", "tfoot"):
            self._close_until({"tbody", "thead", "tfoot", "tr", "td", "th"}, {"table"})
        elif tag == "tr":
            self._close_until({"tr", "td", "th"}, {"table", "tbody", "thead", "tfoot"})
            if self.stack[-1].name == "table":
                self.stack.append(self.stack[-1].append(Node("tbody")))
        elif tag in ("td", "th"):
            self._close_until({"td", "th"}, {"table", "tr", "tbody", "thead", "tfoot"})
            if self.stack[-1].name == "table":
                self.stack.append(self.stack[-1].append(Node("tbody")))
            if self.stack[-1].name in ("tbody", "thead", "tfoot"):
                self.stack.append(self.stack[-1].append(Node("tr")))
        node = self.stack[-1].append(Node(tag, attrs=attrs))
        if tag not in VOID:
            self.stack.append(node)
        self.skip_newline = tag in ("pre", "listing", "textarea")   # HTML5 drops one newline after these

    def handle_startendtag(self, tag, attrs):
        depth = len(self.stack)
        self.handle_starttag(tag, attrs)
        if tag not in VOID and len(self.stack) > depth and self.stack[-1].name == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag in ("html", "body", "head"):
            if tag == "head" and not self.in_body:
                self.stack = _OpenElements([self.html, self.head])
            return
        if tag == "br":
            self.handle_starttag("br", [])
            return
        if not self.in_body and len(self.stack) > 2 and self.stack[-1].name == tag:
            self.stack.pop()
            return
        if tag == "p" and not self.stack.has(("p",)):
            self._to_body()
            self.stack[-1].append(Node("p"))
            return
        if tag in HEADINGS:
            self._close_until(HEADINGS, set())
            return
        self._close_until({tag}, {"table"} if tag not in ("table", "tbody", "thead", "tfoot", "tr", "td", "th",
                                                          "caption") else set())

    def handle_data(self, data):
        if self.skip_newline:
            self.skip_newline = False
            if data.startswith("\n"):
                data = data[1:]
            if not data:
                return
        top = self.stack[-1]
        if not self.in_body and top in (self.head, self.html):
            if not go_trim(data):
                return
            self._to_body()
            top = self.stack[-1]
        if top.children and top.children[-1].name == "#text":
            top.children[-1].data += data
        else:
            top.append(Node("#text", data))

    def handle_comment(self, data):
        self.stack[-1].append(Node("#comment", data))

    def unknown_decl(self, data):                          # <![if !supportLists]> (Word / Outlook)
        self.stack[-1].append(Node("#comment", data))


def parse_html(html: str) -> Node:
    b = _TreeBuilder()
    b.feed(html)
    b.close()
    return b.doc


INLINE = {"b", "big", "i", "small", "tt", "abbr", "acronym", "cite", "code", "dfn", "em", "kbd", "strong",
          "samp", "var", "a", "bdo", "br", "img", "map", "object", "q", "script", "span", "sub", "sup",
          "button", "input", "label", "select", "textarea", "iframe"}
TRIMMED = {"a", "strong", "b", "i", "em", "del", "s", "strike", "code"}
LIST_PREFIX = "data-converter-list-prefix"
LIST_COUNT = "data-converter-prefix-count"
LIST_PREV = "data-converter-prev-prefix-counts"
MARKDOWN_IMAGE_RE = re.compile(r'^!\[([^\]]*)\]\((.+?)(?:\s+"[^"]*")?\)\Z', re.S)


def _escape_markdown(text):
    """escape.MarkdownCharacters."""
    text = re.sub(r"\\(\S)", r"\\\\\1", text, flags=re.A)
    text = re.sub(r"(?m)^(#{1,6} )", r"\\\1", text)

    def hr(m):
        t = m.group(0)
        for ch in "-_*":
            if ch in t:
                return t.replace(ch, "\\" + ch, 3)
        return t
    text = re.sub(r"(?m)^([-*_] *){3,}$", hr, text)
    text = re.sub(r"(?m)^(\W* {0,3})(\d+)\. ", r"\1\2\\. ", text, flags=re.A)
    text = re.sub(r"(?m)^([^\\\w]*)[*+-] ", lambda m: re.sub(r"([*+-])", r"\\\1", m.group(0)), text, flags=re.A)
    text = re.sub(r"(?m)^(\W* {0,3})> ", r"\1\\> ", text, flags=re.A)
    for ch in "*_`|":
        text = text.replace(ch, "\\" + ch)
    return re.sub(r"([\[\]])", r"\\\1", text)


def _collect_text(n):
    out, work = [], [n]
    while work:
        x = work.pop()
        if x.name == "#text":
            out.append(x.data)
        else:
            work.extend(reversed(x.children))
    return "".join(out)


def _prev_text(node):
    while node is not None:
        text = _collect_text(node)
        if node.name == "br":
            return "\n"
        if go_trim(text):
            return go_trim(text) if node.name in TRIMMED else text
        node = node.prev
    return None


def _next_text(node):
    while node is not None:
        text = _collect_text(node)
        if node.name == "br":
            return "\n"
        if go_trim(text):
            return " " if node.name in TRIMMED else text
        node = node.next
    return None


def _add_space(node, md):
    """AddSpaceIfNessesary: a space beside delimiters when the neighbour text touches them."""
    prev = _prev_text(node.prev)
    if prev and not go_space(prev[-1]):
        md = " " + md
    nxt = _next_text(node.next)
    if nxt and not go_space(nxt[0]) and not go_punct(nxt[0]):
        md = md + " "
    return md


LINE_TRIM_RE = re.compile("(?m)^(?:```|[" + GO_WS.replace("\n", "") + "])")


def _trim_leading_spaces(text):
    """TrimpLeadingSpaces: strip line indents, but not list items or code. Only lines that start
    with whitespace or a fence can change, so only those are visited (nested divs re-run this on
    ever longer text)."""
    out, pos, inside = [], 0, False
    for m in LINE_TRIM_RE.finditer(text):
        start = m.start()
        end = text.find("\n", start)
        end = len(text) if end < 0 else end
        line = text[start:end]
        if line[:3] == "```":
            inside = not inside
        if inside:
            continue
        spaces = 0
        for i, ch in enumerate(line):
            if go_space(ch):
                spaces += 4 if ch == "\t" else 1
                continue
            if ch != "-" and spaces < 4 and i:
                out += [text[pos:start], line[i:]]
                pos = end
            break
    out.append(text[pos:])
    return "".join(out)


def _trim_trailing_spaces(text):
    return "\n".join(ln.rstrip(GO_WS) for ln in text.split("\n"))


def _escape_multiline(content):
    content = go_trim(content).replace("\n", "\\\n")
    return re.sub(r"(\n\\)+", "\n\\\\", content)


def _fence_count(ch, content):
    best = run = 0
    for c in content:
        if c == ch:
            run += 1
        else:
            best, run = max(best, run), 0
    return max(best, run)


def _code_fence(ch, content):
    return ch * max(3, _fence_count(ch, content) + 1)


def _every_line(text, delim):
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if go_trim(ln):
            lines[i] = delim + go_trim(ln) + delim
    return "\n".join(lines)


def _is_list_item(line, marker="-"):
    has_number = has_marker = has_space = False
    for ch in line:
        if has_marker and has_space:
            if ch == marker:
                return False
            if not go_space(ch):
                return True
        if has_marker:
            if go_space(ch):
                has_space = True
                continue
            return False
        if ch == marker:
            has_marker = True
            continue
        if has_number and ch == ".":
            has_marker = True
            continue
        if ch.isdigit():
            has_number = True
            continue
        if go_space(ch):
            continue
        return False
    return False


def _indent_list_item(text, spaces):
    parts = text.split("\n")
    for i in range(1, len(parts)):
        if _is_list_item(parts[i]):
            return "\n".join(parts)
        parts[i] = " " * spaces + parts[i]
    return "\n".join(parts)


def _annotate_lists(root, marker="-"):
    """annotateListIndentation: list prefix, reserved width and outer indent on every <li>."""
    def wrapper_li(li):
        has_list = False
        for c in li.children:
            if c.name in ("ul", "ol"):
                has_list = True
                continue
            if c.name == "#text" and go_trim(c.data) or not c.name.startswith("#") and go_trim(_collect_text(c)):
                return False
        return has_list

    def start_of(ol):
        v = go_trim(ol.attrs.get("start", ""))
        try:
            n = int(v)
        except ValueError:
            return 1
        return n if n >= 0 else 1

    stack, work = [], [(root, False)]
    while work:
        n, leaving = work.pop()
        if leaving:
            stack.pop()
            continue
        if n.name in ("ul", "ol"):
            elems = n.elements()
            first_li = next(((i, c) for i, c in enumerate(elems) if c.name == "li"), None)
            max_len = len(str(len(elems)))
            start = start_of(n) if n.name == "ol" else 1
            prefix_len = 0
            if first_li and not wrapper_li(first_li[1]):
                prefix_len = len(marker + " ") if n.name == "ul" else len(f"{start + first_li[0]:0{max_len}d}. ")
            prev_sum = stack[-1][1] + stack[-1][0] if stack else 0
            stack.append((prefix_len, prev_sum))
            for k, c in enumerate(elems):
                if c.name == "li":
                    prefix = ""
                    if not wrapper_li(c):
                        prefix = marker + " " if n.name == "ul" else f"{start + k:0{max_len}d}. "
                    c.attrs[LIST_PREFIX] = prefix
                    c.attrs[LIST_COUNT] = str(prefix_len)
                    c.attrs[LIST_PREV] = str(prev_sum)
            work.append((n, True))                          # pop this level after its children
        elif n.name == "li" and (n.parent is None or n.parent.name not in ("ul", "ol")):
            n.attrs[LIST_PREFIX], n.attrs[LIST_COUNT], n.attrs[LIST_PREV] = "", "0", "0"
        work.extend((c, False) for c in reversed(n.children))


LAYOUT_INSIDE = {"table", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "blockquote"}
PIXEL_STYLE_RE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden|(?:^|;)\s*(?:width|height)\s*:\s*[01](?:px)?\s*(?:;|$)",
                            re.I)


def _own_rows(table):
    for c in table.children:
        if c.name == "tr":
            yield c
        elif c.name in ("thead", "tbody", "tfoot"):
            yield from (r for r in c.children if r.name == "tr")


def _cells(row):
    return [c for c in row.children if c.name in ("td", "th")]


def _is_layout_table(t) -> bool:
    """A table that positions an email's content rather than holding data: marked
    role=presentation, one column wide, or with tables, headings, lists or quotes in its cells.
    (A <p> per cell is how Outlook writes data tables, so paragraphs don't count.)"""
    if t.attrs.get("role", "").lower() in ("presentation", "none"):
        return True
    rows = list(_own_rows(t))
    if max((len(_cells(r)) for r in rows), default=0) <= 1:
        return True
    return any(next((d for name in LAYOUT_INSIDE for d in cell.find(name)), None) is not None
               for r in rows for cell in _cells(r))


def _is_pixel(img) -> bool:
    """A tracking pixel: 0 or 1 px wide or tall, or hidden."""
    for dim in ("width", "height"):
        v = img.attrs.get(dim, "").strip().lower().removesuffix("px")
        if v in ("0", "1"):
            return True
    return bool(PIXEL_STYLE_RE.search(img.attrs.get("style", "")))


def _unwrap_layout(doc):
    """Emails only (not in Firecrawl): drop tracking pixels and turn layout tables into their
    cells' content, one block per cell, innermost tables first; data tables stay tables."""
    for img in list(doc.find("img")):
        if _is_pixel(img):
            img.parent.children.remove(img)
    tables = [t for t in doc.find("table") if _is_layout_table(t)]
    for t in reversed(tables):
        box = Node("div")
        for c in t.children:
            if c.name == "caption":
                box.append(Node("div")).children.extend(c.children)
        for r in _own_rows(t):
            for cell in _cells(r):
                part = box.append(Node("div"))
                for k in cell.children:
                    part.append(k)
        i = t.index()
        box.parent = t.parent
        t.parent.children[i] = box


class _HtmlToMarkdown:
    REMOVE = {"script", "style", "textarea", "head"}

    def __init__(self, cid_picture=None, gfm=True, domain="", email=False):
        self.cid_picture, self.gfm, self.domain, self.email = cid_picture, gfm, domain, email

    def convert(self, doc):
        if self.email:
            _unwrap_layout(doc)
        for cap in list(doc.find("caption")) if self.gfm else []:        # Table plugin: caption after table
            table = cap.parent
            if table is not None and table.name == "table":
                table.children.remove(cap)
                i = table.index()
                cap.parent = table.parent
                table.parent.children.insert(i + 1, cap)
        _annotate_lists(doc)
        md = go_trim(self.to_md(doc))
        md = re.sub(r"\n{2,}", "\n\n", md)
        return _trim_trailing_spaces(md)

    def to_md(self, node):
        """selecToMD: every child's content with its rule applied, children first. A loop with
        its own stack, so deeply nested HTML doesn't hit Python's recursion limit."""
        stack = [[node, 0, []]]
        while True:
            frame = stack[-1]
            kids = frame[0].children
            while frame[1] < len(kids) and kids[frame[1]].name in self.REMOVE:
                frame[1] += 1
            if frame[1] < len(kids):
                child = kids[frame[1]]
                frame[1] += 1
                stack.append([child, 0, []])
                continue
            stack.pop()
            content = "".join(frame[2])
            if not stack:
                return content
            res = self.apply(frame[0], content)
            stack[-1][2].append(content if res is None else res)

    def apply(self, n, content):
        name = n.name
        if self.gfm:
            if name in ("pre", "code"):
                r = self.r_robust_pre(n) if name == "pre" else self.r_robust_code(n)
                if r is not None:
                    return r
            elif name in ("del", "s", "strike"):
                return _add_space(n, "~~" + go_trim(content) + "~~")
            elif name == "table":
                return self.r_table(content, n)
            elif name in ("td", "th"):
                return self.cell(content, n)
            elif name == "tr":
                return self.r_tr(content, n)
            elif name == "input":
                if n.parent is not None and n.parent.name == "li" and n.attrs.get("type", "") == "checkbox":
                    return "[x] " if "checked" in n.attrs else "[ ] "
        rule = {"#text": self.r_text, "li": self.r_li, "img": self.r_img, "a": self.r_a, "pre": self.r_pre,
                "hr": self.r_hr, "br": self.r_br, "blockquote": self.r_blockquote, "noscript": self.r_noscript,
                "iframe": self.r_iframe}.get(name)
        if name in HEADINGS:
            rule = self.r_heading
        elif name in ("ul", "ol"):
            rule = self.r_list
        elif name in ("p", "div"):
            rule = self.r_p
        elif name in ("strong", "b"):
            rule = self.r_strong
        elif name in ("i", "em"):
            rule = self.r_em
        elif name in ("code", "kbd", "samp", "tt"):
            rule = self.r_code
        return rule(content, n) if rule else None

    def abs_url(self, raw):
        """DefaultGetAbsoluteURL (Go net/url): relative links get http://<domain>. Emails have no
        domain, so their links are kept as written."""
        if not self.domain:
            return raw
        import urllib.parse
        if re.search(r"%(?![0-9A-Fa-f]{2})|[\x00-\x1f\x7f]", raw):
            return raw                                      # url.Parse fails: kept as written
        try:
            u = urllib.parse.urlsplit(raw)
        except ValueError:
            return raw
        if u.scheme == "data" or (u.scheme and not raw[len(u.scheme) + 1:].startswith("/")):
            return raw                                      # data: and opaque (mailto:) URLs
        path = u.path
        if not re.fullmatch(r"[A-Za-z0-9\-._~!$&'()*+,;=:@/%]*", path):
            path = urllib.parse.quote(urllib.parse.unquote(path), safe="!$&'()*+,;=:@/-._~")
        return urllib.parse.urlunsplit((u.scheme or "http", u.netloc or self.domain, path, u.query, u.fragment))

    # commonmark rules
    def r_list(self, content, n):
        parent = n.parent
        if parent is not None and parent.name in ("li", "ul", "ol") and parent.elements() and \
                parent.elements()[-1] is n:
            first = parent.children[0]
            data = first.data if first.name in ("#text", "#comment") else first.name
            if not data.rstrip(" \t").endswith("\n"):
                content = "\n" + content
            if content.rstrip(" \t").endswith("\n"):
                content = content.rstrip(GO_WS)
        else:
            content = "\n\n" + content + "\n\n"
        return content

    def r_li(self, content, n):
        if not go_trim(content):
            return None
        content = re.sub(r"^\n+", "", content)
        content = re.sub(r"\n+$", "\n", content)
        content = content.lstrip(" ")
        prefix = n.attrs.get(LIST_PREFIX, "")
        count = int(n.attrs.get(LIST_COUNT, "0") or 0)
        prev = int(n.attrs.get(LIST_PREV, "0") or 0)
        if prefix == "":
            prefix = " " * count
        prefix = " " * prev + prefix
        return prefix + _indent_list_item(content, count + prev) + "\n"

    def r_text(self, content, n):
        text = n.data
        if not go_trim(text):
            return ""
        text = re.sub(r"\t+", " ", text)
        text = re.sub(r"  +", " ", text)
        text = _escape_markdown(text)
        parent, nxt = n.parent, n.next_element()
        if parent is not None and parent.name in ("li", "ol", "ul") and nxt is not None and \
                nxt.name in ("ul", "ol") and not any(c.name == "#text" and go_trim(c.data)
                                                     for c in parent.children[:n.index()]):
            text = text.strip(" ")
        return text

    def r_p(self, content, n):
        parent = n.parent.name if n.parent is not None else ""
        if parent in INLINE or parent == "li":
            return "\n" + content + "\n"
        return "\n\n" + _trim_leading_spaces(content) + "\n\n"

    def r_heading(self, content, n):
        if not go_trim(content):
            return None
        content = go_trim(content.replace("\n", " ").replace("\r", " ").replace("#", "\\#"))
        if n.has_ancestor("a"):
            return _add_space(n, "**" + content + "**")
        return "\n\n" + "#" * int(n.name[1]) + " " + content + "\n\n"

    def r_strong(self, content, n):
        if n.parent is not None and n.parent.name in ("strong", "b"):
            return content
        t = go_trim(content)
        return _add_space(n, _every_line(t, "**")) if t else t

    def r_em(self, content, n):
        if n.parent is not None and n.parent.name in ("i", "em"):
            return content
        t = go_trim(content)
        return _add_space(n, _every_line(t, "_")) if t else t

    def r_img(self, content, n):
        src = go_trim(n.attrs.get("src", ""))
        if not src:
            return ""
        if src.lower().startswith("cid:") and self.cid_picture is not None:
            md = self.cid_picture(src[4:])
            if md is not None:
                return "\n\n" + md + "\n\n" if md else ""
        alt = n.attrs.get("alt", "").replace("\n", " ")
        return f"![{alt}]({self.abs_url(src)})"

    def r_a(self, content, n):
        href = n.attrs.get("href")
        if href is None or go_trim(href) in ("", "#"):
            return content
        href = self.abs_url(href)
        content = _escape_multiline(content)
        title = ""
        if "title" in n.attrs:
            title = ' "' + n.attrs["title"].replace("\n", " ").replace('"', '\\"') + '"'
        if not go_trim(content):
            content = n.attrs["title"] if "title" in n.attrs else n.attrs.get("aria-label", "")
        if content == "":
            return None
        trimmed = go_trim(content)
        if not title and trimmed.startswith("!["):
            m = MARKDOWN_IMAGE_RE.match(trimmed)
            if m and m.group(2) == href:
                return _add_space(n, trimmed)
        return _add_space(n, f"[{content}]({href}{title})")

    def inline_code(self, n):
        out, work = [], [n]
        while work:
            x = work.pop()
            if x.name == "#text":
                out.append(x.data)
            elif x.name in ("style", "script", "textarea"):
                continue
            elif x.name == "br":
                out.append("\n")
            elif x.name == "a" and x is not n:
                content = self.to_md(x)
                r = self.apply(x, content)
                out.append(content if r is None else r)
            else:
                work.extend(reversed(x.children))
        return "".join(out)

    def r_code(self, content, n):
        code = re.sub(r"\n{2,}", "\n", self.inline_code(n))
        fence = "`" * (_fence_count("`", code) + 1)
        if code.startswith("`"):
            code = " " + code
        if code.endswith("`"):
            code = code + " "
        return _add_space(n, fence + code + fence)

    def r_pre(self, content, n):
        code_el = n.first("code")
        language = (code_el.attrs.get("class", "") if code_el else "").replace("language-", "", 1)
        code = self.inline_code(n)
        fence = _code_fence("`", code)
        return "\n\n" + fence + language + "\n" + code + "\n" + fence + "\n\n"

    def r_hr(self, content, n):
        return "" if n.has_ancestor(*HEADINGS) else "\n\n* * *\n\n"

    def r_br(self, content, n):
        return "\n\n"

    def r_blockquote(self, content, n):
        content = go_trim(content)
        if not content:
            return None
        content = re.sub(r"\n{2,}", "\n\n", content)
        return "\n\n" + re.sub(r"(?m)^", "> ", content) + "\n\n"

    def r_noscript(self, content, n):
        return None

    def r_iframe(self, content, n):
        src = n.attrs.get("src")
        if src is None:
            return ""
        if src.startswith("data:text/html"):
            parts = src.split(",", 1)
            if len(parts) != 2:
                return ""
            import urllib.parse
            return _HtmlToMarkdown(self.cid_picture, self.gfm, self.domain).convert(
                parse_html(urllib.parse.unquote_plus(parts[1])))
        return f"[iframe]({self.abs_url(src)})"

    # RobustCodeBlock
    @staticmethod
    def _lang(n):
        if n is None:
            return ""
        for part in n.attrs.get("class", "").lower().split():
            for pre in ("language-", "lang-"):
                if part.startswith(pre):
                    return part[len(pre):]
        return ""

    @staticmethod
    def _collect(n, out):
        work = [n]
        while work:
            x = work.pop()
            if x is None:                                   # end of a block element
                out.append("\n")
                continue
            if x.name == "#text":
                out.append(x.data)
                continue
            if x.name.startswith("#"):
                continue
            cls = x.attrs.get("class", "").lower()
            if "gutter" in cls or "line-numbers" in cls:
                continue
            if x.name == "br":
                out.append("\n")
            if x.name in ("p", "div", "li", "tr", "table", "thead", "tbody", "tfoot", "section", "article",
                          "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6"):
                work.append(None)
            work.extend(reversed(x.children))

    def r_robust_pre(self, n):
        lang = self._lang(n.first("code")) or self._lang(n)
        out = []
        self._collect(n, out)
        content = "".join(out).rstrip("\n")
        fence = _code_fence("`", content)
        return "\n\n" + fence + lang + "\n" + content + "\n" + fence + "\n\n"

    def r_robust_code(self, n):
        if n.has_ancestor("pre"):
            return None
        out = []
        self._collect(n, out)
        code = _trim_trailing_spaces("".join(out).replace("\r\n", "\n"))
        fence = "```" if "``" in code else "``" if "`" in code else "`"
        return fence + code + fence

    # Table plugin
    def r_table(self, content, n):
        if n.first("thead") is None and n.first("th") is None:
            width = max((len(tr.elements()) for tr in n.find("tr")), default=0)
            content = "|" + "     |" * width + "\n" + "|" + " --- |" * width + content
        return "\n\n" + content + "\n\n"

    @staticmethod
    def cell(content, s):
        content = go_trim(content)
        if s.first("table") is None:
            content = re.sub(r"(\r?\n)+", "<br>", content)
        first = s.parent is not None and s.parent.elements() and s.parent.elements()[0] is s
        return ("| " if first else " ") + content + " |"

    def r_tr(self, content, n):
        border = ""
        if self._heading_row(n):
            for c in n.elements():
                b = {"left": ":--", "right": "--:", "center": ":-:"}.get(c.attrs.get("align"), "---") \
                    if "align" in c.attrs else "---"
                border += self.cell(b, c)
        return "\n" + content + ("\n" + border if border else "")

    @staticmethod
    def _heading_row(s):
        parent = s.parent
        if parent is None:
            return False
        if parent.name == "thead":
            return True
        table_or_body = parent.name == "table" or (
            parent.name == "tbody" and parent.parent is not None and parent.parent.elements() == [parent])
        if not table_or_body or not all(c.name == "th" for c in s.elements()):
            return False
        return parent.elements()[0] is s


def html_to_markdown(html: str, cid_picture=None, gfm=True, domain="", email=False) -> str:
    """HTML -> markdown the way Firecrawl converts a web page. cid_picture(cid) -> markdown for
    <img src="cid:..."> (None: keep the image link). email=True also unwraps layout tables and
    drops tracking pixels (emails are laid out with tables; web pages keep Firecrawl's output)."""
    return _HtmlToMarkdown(cid_picture, gfm, domain, email).convert(parse_html(html))


# ---------------------------------------------------------------- entry points
EML_HEADER_RE = re.compile(rb"^(?:From |[A-Za-z][A-Za-z0-9-]*:)")
EML_KEYS = (b"from:", b"to:", b"subject:", b"date:", b"message-id:", b"mime-version:", b"received:")


def email_ext(name: str = "", content_type: Optional[str] = None, data: bytes = b"") -> Optional[str]:
    """".msg" / ".eml" when the source is an email: file name or URL path, then content type,
    then the bytes. None otherwise."""
    path = (name or "").split("?")[0].split("#")[0].lower()
    for ext in CONTENT_TYPE_OF:
        if path.endswith(ext) or ext + "/" in path:
            return ext
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in ("application/vnd.ms-outlook", "application/x-msg"):
        return ".msg"
    if ct == "message/rfc822":
        return ".eml"
    if data[:8] == OLE_MAGIC:
        try:
            cf = Compound(data)
            root = cf.children(0)
            if "__PROPERTIES_VERSION1.0" in root and ("__SUBSTG1.0_001A001F" in root or
                                                      "__SUBSTG1.0_001A001E" in root):
                return ".msg"
        except MailError:
            return None
        return None
    head = data[:4096].lstrip(b"\r\n")
    if EML_HEADER_RE.match(head):
        block = head.split(b"\r\n\r\n")[0].split(b"\n\n")[0].lower()
        found = sum(1 for k in EML_KEYS if block.startswith(k) or b"\n" + k in block)
        if found >= 2:
            return ".eml"
    return None


def convert(src, read_attachment=None, ext: Optional[str] = None) -> Result:
    """src: path or bytes. ext: ".msg" / ".eml" (found from the bytes when not given)."""
    if isinstance(src, (bytes, bytearray)):
        data = bytes(src)
    else:
        with open(src, "rb") as f:
            data = f.read()
        if ext is None:
            ext = email_ext(str(src), None, data)
    ext = ext or email_ext("", None, data)
    if data[:8] == OLE_MAGIC:
        mail = read_msg(data)
    elif ext == ".eml":
        mail = read_eml(data)
    elif ext == ".msg":
        raise MailError("EMAIL_UNREADABLE", "not an Outlook .msg file (no compound file signature)")
    else:
        raise MailError("EMAIL_UNSUPPORTED", "not an email")
    md, warnings, meta = render(mail, read_attachment)
    return Result(md, meta, "; ".join(warnings) or None)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--verify"]:
        sys.exit(verify())
    if argv[:1] == ["--refresh"]:
        sys.exit(refresh())
    ap = argparse.ArgumentParser(description="Email (Outlook .msg, .eml) -> Markdown. Attachments are "
                                             "listed; use pdf2md.py to read them too.")
    ap.add_argument("src")
    ap.add_argument("-o", "--output")
    a = ap.parse_args(argv)
    try:
        r = convert(a.src)
    except MailError as e:
        print(f"{e.code}: {e}", file=sys.stderr)
        return 1
    if r.warning:
        print(f"warning: {r.warning}", file=sys.stderr)
    if a.output:
        with open(a.output, "w", encoding="utf-8") as f:
            f.write(r.markdown)
    else:
        sys.stdout.write(r.markdown)
    return 0



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
a463c93b OLE_MAGIC
f7213c24 MAX_DEPTH
71021000 MIN_PICTURE_BYTES
baafb203 CONTENT_TYPE_OF
8dc21f07 MailError
52263620 Result
1b164927 SECTOR_FREE
0d698a62 END_OF_CHAIN
1134b517 FAT_SECTOR
67918c49 DIFAT_SECTOR
4f9489e8 MAX_REGULAR_SECTOR
bdde6402 NO_STREAM
e6adf5b6 MAX_REGULAR_STREAM_ID
4a392685 MINI_STREAM_CUTOFF
06fb5132 OBJ_STORAGE
0844ef58 OBJ_STREAM
4040e0bc OBJ_ROOT
a6598261 _bad
bf9f6dca Compound
eb4023c5 PT_SHORT
f6761800 PT_LONG
e7bf251b PT_FLOAT
67aa2c8b PT_DOUBLE
dc58d348 PT_CURRENCY
46e972fa PT_APPTIME
200a2e5b PT_ERROR
ed5be63b PT_BOOLEAN
f6a80f5e PT_OBJECT
da3a8b30 PT_I8
815642cd PT_STRING8
f7c46173 PT_UNICODE
efd94e5d PT_SYSTIME
7a07c461 PT_BINARY
2d378218 PS_MAPI
73f87657 PS_PUBLIC_STRINGS
1d3b62eb PSETID_APPOINTMENT
903e7b28 LID_LOCATION
c1f27101 LID_START
6f33b2e7 LID_END
ca6daf48 SUBSTG_RE
542f32ab CODEPAGES
52e91287 codec_of
3a634a64 filetime
af62c80b Props
1ecb1252 named_ids
2d7418f9 Attachment
6d0b7fd3 Mail
5621534d _person
10bf03f5 read_msg
36fb24fc _message
5c458f87 CID_REF_RE
839faa05 _cid_refs
19740b20 _is_message
4dfa68a8 decode_html
f0f01f8c read_eml
8f12242b _addresses
f35c29df _leaf_parts
b25c0683 _part_text
ef073dec _eml_message
dc0ba3b9 MD_INLINE
5ca0b1c9 md_escape
dddee035 person_md
05312bec when
b353c037 iso
2d92db02 FENCE_RE
7e3fbd60 HEADING_RE
0e960282 shift_headings
77ccd5f2 plain_text_md
7403780a CID_TEXT_RE
944027fa is_picture
9b6c1430 render
f5184884 person_text
79ce6038 message_text
6aaf8261 RTF_PREBUF
d8ae9ab2 decompress_rtf
c68438e5 _ascii_letter
72fb2f21 rtf_tokens
02ed6281 RTF_CODEPAGES
d25117cb RTF_CHARSETS
46de13f7 RTF_SKIP
a0a4ccea RTF_SYMBOLS
3a7d7534 _rtf_codec
340d9991 rtf_body
87acfea0 GO_WS
62a5e392 go_trim
ea8fe1b8 go_space
096025ae go_punct
efc38fe2 Node
b8bfb3f4 VOID
91af5b90 HEAD_TAGS
a4261c89 CLOSES_P
482801ca SCOPE
8e83b3e7 HEADINGS
f3f3d35e _OpenElements
eff97c57 _TreeBuilder
8ee105f9 parse_html
e1498239 INLINE
c3473646 TRIMMED
6dfc4356 LIST_PREFIX
3971e973 LIST_COUNT
415078f4 LIST_PREV
81fc00f0 MARKDOWN_IMAGE_RE
e1b285e3 _escape_markdown
5d5984ca _collect_text
759b799e _prev_text
0e4ce90a _next_text
1a9aef8b _add_space
9ca5f74b LINE_TRIM_RE
3945f69e _trim_leading_spaces
540584d5 _trim_trailing_spaces
fe31b736 _escape_multiline
166f3718 _fence_count
79f1dd89 _code_fence
77f990ba _every_line
71b7b2d6 _is_list_item
bdcf5065 _indent_list_item
eb0dd217 _annotate_lists
4c01c9f5 LAYOUT_INSIDE
95d0c26c PIXEL_STYLE_RE
388e496e _own_rows
7e84b560 _cells
5f5479f6 _is_layout_table
4cd3bc76 _is_pixel
0fac7310 _unwrap_layout
f98f94db _HtmlToMarkdown
0cc83141 html_to_markdown
7809039b EML_HEADER_RE
1cccad35 EML_KEYS
6670673f email_ext
824d8349 convert
d3c60045 main
a3f20c3b _fingerprints
cc642faa verify
b3a8ba0f refresh
"""


if __name__ == "__main__":
    sys.exit(main())
