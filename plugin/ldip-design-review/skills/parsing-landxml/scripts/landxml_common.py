"""Shared helpers for skill 3 parsing-landxml (SPC-0006 §2.3; Rev C §4.12). No network, no shell calls."""
__version__ = "0.2.0"

import hashlib
import json
import os
import re
from pathlib import Path

EXIT_OK, EXIT_INPUT, EXIT_DEPENDENCY, EXIT_SCHEMA = 0, 2, 3, 4
MAX_PATH = 250                    # SPC-0002 R1
MAX_BYTES = 500 * 1024 * 1024     # SPC-0006 Rev C §4.12.9 (owner, 14 Oct 2026)

# Document number at the start of the file name: same rule as skill 6 pkg_common.number_from_name and
# parse_number (SPC-0004 §5.1; MIDP layout SRC-14). Copied, not imported: skills stay self-contained.
NUMBER_TOKEN = re.compile(r"^([A-Za-z0-9]+(?:-[A-Za-z0-9]+){5,})(?=$|[ _.(\[]| -)")
TEN_FIELD_TYPES = ("DWG", "MOD")
MIDP_SEQ, MIDP_SHEET = re.compile(r"^\d{5}$"), re.compile(r"^\d{3}$")
AICC_EXPORT_SUFFIX = "-AICC-EXPORT"   # SPC-0006 Rev C §4.12.4
SUBMITTAL_FILE = re.compile(r"^submittals/(QRWN|NMAR|NRJS)/[^/]+/.+")


class LandXmlError(Exception):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code, self.detail = code, detail


def check_path(path, must_exist=False):
    p = Path(path)
    if len(str(p.resolve())) > MAX_PATH:
        raise LandXmlError(EXIT_INPUT, f"path longer than {MAX_PATH} characters: {path}")
    if must_exist and not p.is_file():
        raise LandXmlError(EXIT_INPUT, f"file not found: {path}")
    return p


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def dumps_main(obj):
    """Main ingestion file: same layout as skills 1 and 2 (indent 1, sorted keys, UTF-8)."""
    return json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False).encode("utf-8")


def dumps_side(obj):
    """Surface side file: sorted keys, compact (a TIN of 100,000 faces stays a few MB)."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def atomic_write_bytes(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, p)


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False))


def r3(x):
    """Coordinates, lengths, stations, elevations to 0.001 m (SPC-0006 §2.3.6)."""
    return None if x is None else round(float(x), 3)


def r2(x):
    """Areas to 0.01 m2."""
    return None if x is None else round(float(x), 2)


def number_from_name(name):
    stem = name[:-4] if name.lower().endswith(".xml") else name
    if stem.upper().endswith(AICC_EXPORT_SUFFIX):
        stem = stem[: -len(AICC_EXPORT_SUFFIX)]
    m = NUMBER_TOKEN.match(stem.strip())
    return m.group(1).upper() if m else None


def revision_of(number):
    """Revision field of a document number (SPC-0004 §5.1 / MIDP layout), or None."""
    if not number:
        return None
    f = number.split("-")
    if len(f) in (9, 10) and MIDP_SEQ.match(f[7]) and MIDP_SHEET.match(f[8]):
        return f[9] if len(f) == 10 else None
    dtype = f[6].upper() if len(f) > 6 else None
    expected = 10 if dtype in TEN_FIELD_TYPES else 9
    return f[-1] if len(f) == expected else None


def expected_provenance(source_path):
    """Folder rule used in a review run (SPC-0006 Rev C §4.12.4; P7, owner 15 Oct 2026): a file under
    submittals/<SITE>/<sub-id>/ is a Parsons deliverable (`parsons`) unless its name ends -AICC-EXPORT
    (`aicc_export`). No other location has a rule in a run yet: Employer reference data has no agreed
    folder or read access (brief Rev G §11 item 26) -> None. One implementation, used by write_manifest.py
    (skill 1) and format_check.py."""
    p = str(source_path).replace("\\", "/")
    if not SUBMITTAL_FILE.match(p):
        return None
    name = p.rsplit("/", 1)[-1]
    stem = name[:-4] if name.lower().endswith(".xml") else name
    return "aicc_export" if stem.upper().endswith(AICC_EXPORT_SUFFIX) else "parsons"
