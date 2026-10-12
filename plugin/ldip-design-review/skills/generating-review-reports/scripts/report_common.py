"""Shared helpers for generating-review-reports (skill 24, SPC-0006 §4.10; standalone mode, owner 12 Oct 2026).

Holds the DRAFT marking, the plugin version, atomic writes, the finding-schema check and the SPC-0003 §6
output-route table. No network, no shell calls.
"""
__version__ = "0.1.0"

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

EXIT_OK, EXIT_INPUT, EXIT_DEP, EXIT_SCHEMA, EXIT_REFUSED = 0, 2, 3, 4, 5
MAX_PATH = 250  # SPC-0002 R1
RIYADH = timezone(timedelta(hours=3))
PLUGIN_ROOT = Path(__file__).resolve().parents[3]
SCHEMA = PLUGIN_ROOT / "schemas" / "finding.schema.json"

DRAFT = "DRAFT — not for issue until approved at the Human Review Gate"
STATUSES = ["Confirmed", "Missing information", "Probable", "Not auto-verifiable"]
SEVERITIES = ["Critical", "Major", "Minor", "Observation"]
UNVERIFIED = "Unverified (candidate)"  # report label only, never a schema value
ROUTE_UNVERIFIED = "UNVERIFIED"         # view label only: candidates get no route (P-S22-3)
ROUTE_INCONSISTENT = "INCONSISTENT"     # no row of the route table matches (SPC-0005 §5.9.1)
LEGACY_RULES = ("PKG-001", "PKG-002", "PKG-004", "PKG-006")


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False, sort_keys=True))


def fail(code, detail, **extra):
    emit({"status": "error", "code": code, "detail": detail, **extra})
    return code


def path_ok(p):
    return len(str(Path(p).resolve())) <= MAX_PATH


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def atomic_write_text(path, text):
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, out)


def atomic_write_json(path, obj):
    atomic_write_text(path, json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False))


def atomic_save(doc, path):
    """Save an openpyxl workbook or python-docx document through .tmp + rename (SPC-0002 §6.2, R3)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".tmp" + out.suffix)
    doc.save(str(tmp))
    os.replace(tmp, out)


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def plugin_version():
    try:
        return read_json(PLUGIN_ROOT / ".claude-plugin" / "plugin.json").get("version", "unknown")
    except (OSError, ValueError):
        return "unknown"


def stamp(skill, script, version):
    return f"{DRAFT} · ldip-design-review {plugin_version()} · {skill} ({script} {version})"


def now_text():
    return datetime.now(RIYADH).strftime("%Y-%m-%d %H:%M (+03:00)")


def validator():
    """jsonschema validator for finding.schema.json, or None when jsonschema is missing."""
    try:
        import jsonschema
    except ImportError:
        return None
    schema = read_json(SCHEMA)
    cls = jsonschema.validators.validator_for(schema)
    return cls(schema, format_checker=cls.FORMAT_CHECKER)


def status_of(f):
    """Verification status for verified / classified / registered findings; None for candidates."""
    return (f.get("verification") or {}).get("status") if f.get("stage") != "candidate" else None


def is_legacy(f, legacy_numbers=()):
    """SPC-0003 §6 row 1: a legacy document number on a PKG rule, severity Observation."""
    if f.get("severity") != "Observation" or (f.get("reference") or {}).get("rule_id") not in LEGACY_RULES:
        return False
    if f.get("statement", "").startswith("Legacy number"):
        return True
    text = " ".join([f["location"].get("document_no", "")] + [e.get("extract", "") for e in f["evidence"]]).upper()
    return any(n and n.upper() in text for n in legacy_numbers)


def provisional_route(f, legacy_numbers=()):
    """Route from the SPC-0003 Rev D §6 table, first match. Candidates -> UNVERIFIED (P-S22-3)."""
    if f.get("stage") in ("classified", "registered") and f.get("output_route"):
        return f["output_route"], "registered"
    st = status_of(f)
    if st is None:
        return ROUTE_UNVERIFIED, "candidate: run skill 18 first"
    it = f["issue_type"]
    if is_legacy(f, legacy_numbers):
        return "NONE", "§6 row 1: legacy number, register only"
    if st == "Confirmed" and it in ("design_error", "inconsistency", "package_control"):
        return "CRS", "§6 row 2: Confirmed " + it
    if st == "Missing information" and it == "missing_information":
        return "CRS", "§6 row 3: Missing information (+ EOT assessment)"
    if it == "er_ambiguity":
        return "RFI", "§6 row 4: er_ambiguity"
    if it == "schedule_blocking":
        return "RISK_EOT_REGISTER", "§6 row 5: schedule_blocking"
    if st == "Probable":
        return "ENGINEER_QUEUE", "§6 row 6: Probable"
    if st == "Not auto-verifiable":
        return "MANUAL_CHECK", "§6 row 7: Not auto-verifiable"
    return ROUTE_INCONSISTENT, f"no §6 row matches status {st} with issue type {it} (SPC-0005 §5.9.1)"


def location_text(loc):
    """One readable pointer for a finding location (document, revision, sheet, cells, pages, plots)."""
    parts = [loc.get("document_no", "")]
    if loc.get("revision"):
        parts[0] += f" Rev {loc['revision']}"
    if loc.get("sheet_no"):
        parts.append(f"sheet {loc['sheet_no']}")
    if loc.get("table"):
        cells = loc["table"]["cells"]
        more = f" (+{len(cells) - 6} more)" if len(cells) > 6 else ""
        parts.append(f"sheet '{loc['table']['sheet']}' cells {', '.join(cells[:6])}{more}")
    if loc.get("pdf"):
        bb = loc["pdf"].get("bbox_pt")
        parts.append(f"PDF page {loc['pdf']['page']}" + (f" box {[round(v) for v in bb]}" if bb else ""))
    if loc.get("dwg"):
        d = loc["dwg"]
        bits = [f"layer {d['layer']}" if d.get("layer") else "", f"layout {d['layout']}" if d.get("layout") else ""]
        hs = d.get("handles") or []
        if hs:
            bits.append(f"handles {', '.join(hs[:6])}" + (f" (+{len(hs) - 6} more)" if len(hs) > 6 else ""))
        parts.append("DWG " + ", ".join(b for b in bits if b))
    if loc.get("xy"):
        parts.append(f"E {loc['xy']['e']} / N {loc['xy']['n']} ({loc['xy']['crs']})")
    if loc.get("plot_ids"):
        ids = loc["plot_ids"]
        parts.append(f"plots {', '.join(ids[:10])}" + (f" (+{len(ids) - 10} more)" if len(ids) > 10 else ""))
    if loc.get("chainage"):
        parts.append(f"chainage {loc['chainage']}")
    if loc.get("package_level"):
        parts.append("whole submittal")
    return " — ".join(p for p in parts if p)


def reference_text(ref):
    bits = []
    if ref.get("rule_id"):
        bits.append(ref["rule_id"] + (f" (rulebook {ref['rulebook_version']})" if ref.get("rulebook_version") else ""))
    if ref.get("source_doc"):
        bits.append(ref["source_doc"])
    if ref.get("clause"):
        bits.append(ref["clause"])
    return " — ".join(bits)


SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(s):
    return SAFE.sub("_", s).strip("_")[:80]
