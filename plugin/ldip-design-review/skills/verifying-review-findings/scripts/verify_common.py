"""Shared helpers for skill 18 verifying-review-findings (SPC-0006 §4.7; SPC-0005 §5.7).

Independent cell readers: the finders read xlsx with openpyxl and CSV with the csv module; the
Verifier reads xlsx straight from the package XML (zipfile + ElementTree) and CSV with its own
parser, so a reader defect cannot confirm itself (SPC-0005 §5.7.1). No network, no shell calls.
"""
__version__ = "0.1.0"

import hashlib
import json
import os
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

EXIT_OK, EXIT_INPUT, EXIT_DEPENDENCY, EXIT_SCHEMA, EXIT_REFUSED = 0, 2, 3, 4, 5
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "pr": "http://schemas.openxmlformats.org/package/2006/relationships"}
CELL_RE = re.compile(r"^([A-Z]{1,3})([0-9]{1,7})$")


class VerifyError(Exception):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code, self.detail = code, detail


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerifyError(EXIT_INPUT, f"cannot read {path}: {exc}") from exc


def atomic_write_json(path, obj):
    p = Path(path)
    if len(str(p.resolve())) > 250:
        raise VerifyError(EXIT_INPUT, f"path longer than 250 characters: {path}")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def col_index(letters):
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


# ------------------------------------------------------------------ xlsx (package XML)
def xlsx_cells(path, sheet, refs):
    """Return {ref: raw string value or None} for the requested refs of one sheet."""
    want = set(refs)
    out = {r: None for r in want}
    with zipfile.ZipFile(path) as z:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rid = None
        for s in wb.find("m:sheets", NS):
            if s.get("name") == sheet:
                rid = s.get(f"{{{NS['r']}}}id")
        if rid is None:
            raise VerifyError(EXIT_INPUT, f"sheet '{sheet}' not in {Path(path).name}")
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        target = next(r.get("Target") for r in rels.findall("pr:Relationship", NS) if r.get("Id") == rid)
        target = target.lstrip("/")
        part = target if target.startswith("xl/") else f"xl/{target}"
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
        with z.open(part) as fh:
            for _, el in ET.iterparse(fh):
                if el.tag != f"{{{NS['m']}}}c":
                    continue
                ref = el.get("r")
                if ref in want:
                    t, v = el.get("t"), el.find("m:v", NS)
                    if t == "s" and v is not None:
                        out[ref] = shared[int(v.text)]
                    elif t == "inlineStr":
                        out[ref] = "".join(x.text or "" for x in el.iter(f"{{{NS['m']}}}t"))
                    else:
                        out[ref] = None if v is None else v.text
                el.clear()
    return out


# ------------------------------------------------------------------ CSV (own parser)
def csv_rows(path):
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    rows, row, field, q, i = [], [], [], False, 0
    while i < len(text):
        ch = text[i]
        if q:
            if ch == '"':
                if i + 1 < len(text) and text[i + 1] == '"':
                    field.append('"')
                    i += 1
                else:
                    q = False
            else:
                field.append(ch)
        elif ch == '"':
            q = True
        elif ch == ",":
            row.append("".join(field))
            field = []
        elif ch in "\r\n":
            row.append("".join(field))
            rows.append(row)
            row, field = [], []
            if ch == "\r" and i + 1 < len(text) and text[i + 1] == "\n":
                i += 1
        else:
            field.append(ch)
        i += 1
    if field or row:
        row.append("".join(field))
        rows.append(row)
    return rows


def csv_cells(path, refs):
    rows = csv_rows(path)
    out = {}
    for ref in refs:
        m = CELL_RE.match(ref)
        r, c = int(m.group(2)) - 1, col_index(m.group(1))
        out[ref] = rows[r][c] if r < len(rows) and c < len(rows[r]) else None
    return out


# ------------------------------------------------------------------ value comparison
NUM_HEAD = re.compile(r"^\(?[-+]?[0-9][0-9,]*(\.[0-9]+)?([eE][-+]?[0-9]+)?\)?%?")


def as_number(s):
    if s is None:
        return None
    t = str(s).strip().replace(" ", "")
    if t in ("-", "–"):
        return 0.0
    m = NUM_HEAD.match(t)
    if not m or m.group(0) != t:
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    pct = t.endswith("%")
    try:
        x = float(t.rstrip("%").replace(",", ""))
    except ValueError:
        return None
    x = x / 100.0 if pct else x
    return -x if neg else x


def expected_value(token_value):
    """Split an extract value into (number or None, printed decimals, text)."""
    v = token_value.strip()
    m = NUM_HEAD.match(v.replace(" ", ""))
    if m and m.group(0):
        head = m.group(0)
        mant, _, exp = head.rstrip(")%").lower().partition("e")
        dec = len(mant.split(".")[1]) if "." in mant else 0
        if exp:                       # "%g" printing: 3.57095e+06 → tolerance of the last printed digit
            dec -= int(exp)
        return as_number(head), dec, v
    return None, 0, v


def values_agree(expected_token, found_raw):
    num, dec, text = expected_value(expected_token)
    if num is not None:
        f = as_number(found_raw)
        if f is None:
            return False
        tol = 0.5 * 10 ** (-dec) + 1e-9 * max(1.0, abs(num))
        return abs(f - num) <= tol
    return str(found_raw or "").strip().casefold() == text.casefold()
