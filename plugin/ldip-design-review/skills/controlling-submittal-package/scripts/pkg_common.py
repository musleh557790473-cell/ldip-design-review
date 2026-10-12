"""Shared helpers for controlling-submittal-package (skill 6, SPC-0006 §4.3).

Number parsing follows SPC-0004 §5.1 (SRC-02 governs): split on '-'; field 7 (type) DWG or MOD -> 10
fields (program-entity-organization-package-section-discipline-type-level-sequence-revision), any
other type -> 9 fields (no level). No network, no shell calls.
"""
__version__ = "0.2.0"

import hashlib
import json
import os
import re
from pathlib import Path

EXIT_OK, EXIT_INPUT, EXIT_DEP, EXIT_SCHEMA, EXIT_REFUSED = 0, 2, 3, 4, 5
MAX_PATH = 250  # SPC-0002 R1

# A file name carries a document number when its leading token has at least 6 hyphen fields of
# upper-case letters / digits (e.g. "LDIP-RCRC-SAP-01-QRWN-MPL-DWG-00-11000"); anything after the
# token (" - title", "_title") is the descriptive part of the name.
NUMBER_TOKEN = re.compile(r"^([A-Za-z0-9]+(?:-[A-Za-z0-9]+){5,})(?=$|[ _.(\[]| -)")
FIELD_NAMES_10 = ["program", "entity", "organization", "package", "section", "discipline", "type", "level",
                  "sequence", "revision"]
FIELD_NAMES_9 = ["program", "entity", "organization", "package", "section", "discipline", "type", "sequence",
                 "revision"]
TEN_FIELD_TYPES = ("DWG", "MOD")

# File extension -> MIDP exchange-format column (SRC-14 'EXCHANGE FORMAT' header labels).
EXT_FORMAT = {".dwg": "DWG", ".pdf": "PDF", ".nwd": "NWD", ".rvt": "RVT", ".nwc": "NWC", ".ifc": "IFC",
              ".dwfx": "DWFx", ".dwf": "DWF", ".docx": "DOCX", ".doc": "DOCX", ".xlsx": "XLSX", ".xlsm": "XLSX",
              ".xls": "XLSX", ".xml": "XML", ".gdb": "GDB"}
MIDP_FORMATS = ["DWG", "PDF", "NWD", "RVT", "NWC", "IFC", "DWFx", "DOCX", "XLSX", "XML", "GDB"]
IGNORED_NAMES = ("desktop.ini", "thumbs.db", ".ds_store")


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False, sort_keys=True))


def fail(code, detail, **extra):
    emit({"status": "error", "code": code, "detail": detail, **extra})
    return code


def path_ok(p):
    return len(str(Path(p).resolve())) <= MAX_PATH


def atomic_write_json(path, obj):
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def number_from_name(name):
    """Document number token at the start of a file or folder name, or None."""
    stem = name
    for ext in sorted(EXT_FORMAT, key=len, reverse=True):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    m = NUMBER_TOKEN.match(stem.strip())
    return m.group(1).upper() if m else None


MIDP_SEQ = re.compile(r"^\d{5}$")     # SRC-14 "Document Naming Scheme": sequence number
MIDP_SHEET = re.compile(r"^\d{3}$")   # SRC-14 "Document Naming Scheme": sheet number
FIELD_NAMES_MIDP = ["program", "entity", "organization", "package", "section", "discipline", "type", "sequence",
                    "sheet", "revision"]


def midp_layout(f):
    """True when the fields follow the MIDP naming scheme (SRC-14): …-type-sequence(5)-sheet(3)[-revision],
    for drawings, models and documents alike (P-S6-6; owner 12 Oct 2026: the latest MIDP governs)."""
    return len(f) in (9, 10) and bool(MIDP_SEQ.match(f[7]) and MIDP_SHEET.match(f[8]))


def parse_number(number):
    """Field-level parse per SPC-0004 §5.1, plus the MIDP layout. Returns a dict; never raises."""
    f = [x.strip() for x in str(number).strip().split("-")]
    dtype = f[6].upper() if len(f) > 6 else None
    expected = 10 if dtype in TEN_FIELD_TYPES else 9
    res = {"number": str(number).strip(), "fields": f, "field_count": len(f), "type": dtype,
           "expected_fields": expected, "conforming_count": len(f) == expected,
           "layout": "midp" if midp_layout(f) else "src02"}
    if res["layout"] == "midp":
        res["midp"] = {"parts": dict(zip(FIELD_NAMES_MIDP, f)), "base": "-".join(f[:9]),
                       "revision": f[9] if len(f) == 10 else None, "sequence": f[7], "sheet": f[8]}
    names = FIELD_NAMES_10 if expected == 10 else FIELD_NAMES_9
    if res["conforming_count"]:
        res["parts"] = dict(zip(names, f))
        res["base"] = "-".join(f[:-1])
        res["revision"] = f[-1]
    else:
        # Only fields 1-7 have the same position in every structure; the rest is not interpreted.
        res["parts"] = dict(zip(FIELD_NAMES_10[:7], f[:7]))
        res["base"] = res["number"]
        res["revision"] = None
    return res


def register_base(parse):
    """Base used to group carriers: the MIDP base for a MIDP-layout number (equals the MIDP number and the
    title-block number), else the SRC-02 base."""
    return parse["midp"]["base"] if parse.get("layout") == "midp" else parse["base"]


def register_revision(parse):
    return parse["midp"]["revision"] if parse.get("layout") == "midp" else parse["revision"]


def format_of(path):
    p = Path(path)
    return EXT_FORMAT.get(p.suffix.lower())


def section_group(code, site_map):
    """Internal site key(s) a Parsons section code belongs to, from the PKG-006 site map."""
    code = (code or "").upper()
    return tuple(sorted(site for site, codes in site_map.items() if code in [c.upper() for c in codes]))


def norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().upper()


def col_letter(idx0):
    s, n = "", idx0 + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def param(rule, name, default=None):
    p = (rule or {}).get("parameters", {}).get(name)
    if not p or p.get("tag") == "GAP":
        return default
    return p.get("value", default)
