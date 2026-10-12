"""Shared helpers for drafting-review-correspondence (skill 22, SPC-0006 §4.8; standalone mode, owner 12 Oct 2026).

The draft text is composed mechanically from the finding fields (P-S22-5): the same functions build the
drafts (draft_crs.py) and re-build them for the check (validate_drafts.py), so any hand edit is found.
No network, no shell calls.
"""
__version__ = "0.1.0"

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

EXIT_OK, EXIT_INPUT, EXIT_DEP, EXIT_SCHEMA, EXIT_REFUSED = 0, 2, 3, 4, 5
MAX_PATH = 250
RIYADH = timezone(timedelta(hours=3))
PLUGIN_ROOT = Path(__file__).resolve().parents[3]
DRAFTS_SCHEMA = Path(__file__).resolve().parents[1] / "templates" / "drafts.schema.json"
DRAFT = "DRAFT — not for issue until approved at the Human Review Gate"
SKILL = "drafting-review-correspondence"

# Header values of the CRS / RFI forms per site [PROPOSED — owner may override with --header key=value].
SITE_HEADER = {
    "QRWN": {"project": "LDIP - Package (1) – Al-Qairwan & Al-Narjis", "section": "Package-1",
             "sub_section": "QRWN", "location": "AL-QAIRAWAN"},
    "NRJS": {"project": "LDIP - Package (1) – Al-Qairwan & Al-Narjis", "section": "Package-1",
             "sub_section": "NRJS", "location": "AL-NARJIS"},
    "NMAR": {"project": "LDIP - Package (3) – Dahiyat Namar", "section": "Package-3",
             "sub_section": "NMAR", "location": "DAHIYAT NAMAR"},
}
COMMON_HEADER = {"program": "LDIP", "crs_ref": "DRAFT — number assigned on issue",
                 "rfi_ref": "DRAFT — number assigned on issue",
                 "reviewer_org": "JV LADUN & AL-AYUNI",
                 "contractor": "Saudi Arabian Parsons Engineering Consultancy (SAPEC)",
                 "review_no": "R0"}
DISCIPLINE = {"INT": "Document control", "GIS": "GIS / Masterplan", "RDE": "Roads", "WET": "Wet utilities",
              "DRY": "Dry utilities", "COO": "Coordination", "STR": "Structures"}
EVIDENCE_IN_COMMENT = 2
EXTRACT_CHARS = 300


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False, sort_keys=True))


def fail(code, detail, **extra):
    emit({"status": "error", "code": code, "detail": detail, **extra})
    return code


def path_ok(p):
    return len(str(Path(p).resolve())) <= MAX_PATH


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def atomic_write_json(path, obj):
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
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


def marking(script, version):
    return f"{DRAFT} · ldip-design-review {plugin_version()} · {SKILL} ({script} {version})"


def evidence_lines(row):
    out = []
    for e in row["evidence"][:EVIDENCE_IN_COMMENT]:
        x = " ".join(e["extract"].split())
        out.append(x if len(x) <= EXTRACT_CHARS else x[:EXTRACT_CHARS - 1] + "…")
    return out


def crs_comment(row):
    """Column 'Review Comment': the finding statement, its reference and up to two evidence extracts."""
    text = row["statement"].strip()
    if row.get("reference"):
        text += f"\nReference: {row['reference']}"
    ev = evidence_lines(row)
    if ev:
        text += "\nEvidence: " + " | ".join(ev)
    return text


def crs_row(no, row, review_no):
    return {"no": no, "finding_id": row["finding_id"], "rule_id": row["rule_id"],
            "location": row["location"], "comment": crs_comment(row), "review_no": review_no,
            "comment_status": "Open", "severity": row["severity"], "status": row["status"],
            "route_basis": row["route_basis"], "eot_assessment": row["status"] == "Missing information"}


def header_for(site, sub_id, rows, overrides):
    h = {**COMMON_HEADER, **SITE_HEADER[site]}
    docs = sorted({r["document_no"] for r in rows})
    h["document_no"] = docs[0] if len(docs) == 1 else "Multiple — see column 'Page/Para/Clause/ Doc. No.'"
    h["subject"] = f"Design review comments — {SITE_HEADER[site]['location'].title()} — submittal {sub_id}"
    h.update({k: v for k, v in overrides.items() if k in h or k in ("pmcm",)})
    return h


def rfi_item(no, row, overrides):
    site = SITE_HEADER[row["site"]]
    info = row["statement"].strip()
    if row.get("reference"):
        info += f"\nReference: {row['reference']}"
    return {"no": no, "finding_id": row["finding_id"], "site": row["site"], "sub_id": row["sub_id"],
            "rfi_ref": overrides.get("rfi_ref", COMMON_HEADER["rfi_ref"]), "rev_no": "00",
            "discipline": DISCIPLINE.get(row["agent"], row["agent"]), "subject": row["title"],
            "drawing_nos": row["document_no"] + (f" Rev {row['revision']}" if row.get("revision") else ""),
            "document": row.get("source_doc") or "", "specification": row.get("clause") or "",
            "location": site["location"], "information_requested": info,
            "attachments": sorted({e["artifact_path"] for e in row["evidence"] if e.get("artifact_path")})}


def build_drafts(view, review_no="R0", overrides=None):
    """Drafts from a findings view. Pure function: draft_crs.py writes it, validate_drafts.py re-runs it."""
    overrides = overrides or {}
    rows = view["findings"]
    crs_groups, rfis, eq, mc, not_drafted = {}, [], [], [], []
    for r in rows:
        route = r["route"]
        if route == "CRS":
            crs_groups.setdefault((r["site"], r["sub_id"]), []).append(r)
        elif route == "RFI":
            rfis.append(r)
        elif route == "ENGINEER_QUEUE":
            eq.append({"finding_id": r["finding_id"], "rule_id": r["rule_id"], "severity": r["severity"],
                       "location": r["location"], "statement": r["statement"], "recheck_result": r["recheck_result"]})
        elif route == "MANUAL_CHECK":
            mc.append({"finding_id": r["finding_id"], "rule_id": r["rule_id"], "location": r["location"],
                       "statement": r["statement"], "manual_check": r["manual_check"]})
        else:
            reason = {"UNVERIFIED": "candidate — run skill 18 (verifying-review-findings) first",
                      "NONE": "register only (legacy number, SPC-0003 §6 row 1)",
                      "RISK_EOT_REGISTER": "internal risk / EOT register, not correspondence",
                      "INCONSISTENT": r["route_basis"]}.get(route, r["route_basis"])
            not_drafted.append({"finding_id": r["finding_id"], "route": route, "reason": reason})
    crs = []
    for (site, sub), grp in sorted(crs_groups.items()):
        grp = sorted(grp, key=lambda x: x["finding_id"])
        crs.append({"site": site, "sub_id": sub, "run_ids": sorted({g["run_id"] for g in grp}),
                    "header": header_for(site, sub, grp, overrides),
                    "rows": [crs_row(i, g, review_no) for i, g in enumerate(grp, 1)]})
    rfi = [rfi_item(i, r, overrides) for i, r in enumerate(sorted(rfis, key=lambda x: x["finding_id"]), 1)]
    return {"crs": crs, "rfi": rfi, "engineer_queue": eq, "manual_check": mc,
            "not_drafted": sorted(not_drafted, key=lambda x: x["finding_id"]),
            "counts": {"crs_rows": sum(len(c["rows"]) for c in crs), "crs_sheets": len(crs), "rfi": len(rfi),
                       "engineer_queue": len(eq), "manual_check": len(mc), "not_drafted": len(not_drafted)}}
