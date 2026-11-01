"""Shared helpers for reviewing-wet-utilities (skill 11, SPC-0006 Rev Q §4.19).

I/O, exits, params, unit conversion, finding stubs. Report only (DQ-11): no rule_id.
Agent code WUT; owner role DMR (DQ-5). No network, no shell.
"""
__version__ = "0.1.0"

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

EXIT_OK, EXIT_INPUT, EXIT_DEP, EXIT_SCHEMA = 0, 2, 3, 4
MAX_PATH = 250  # SPC-0002 R1

SKILL_ROOT = Path(__file__).resolve().parents[1]
PARAMS_PATH = SKILL_ROOT / "references" / "params.json"
LAYERS_PATH = SKILL_ROOT / "references" / "wet_layers.json"
PLUGIN_ROOT = SKILL_ROOT.parents[1]
FINDING_SCHEMA = PLUGIN_ROOT / "schemas" / "finding.schema.json"

FT_TO_M = 0.3048
FT_TO_MM = FT_TO_M * 1000.0
AGENT = "WUT"
SKILL_NAME = "reviewing-wet-utilities"


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
    if not path_ok(path):
        raise ValueError(f"path longer than {MAX_PATH} characters: {path}")
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_params(path=None):
    doc = read_json(path or PARAMS_PATH)
    tol = doc.get("tolerances") or {}
    return {
        "length_m": float(tol.get("length_m", 0.05)),
        "slope_pct": float(tol.get("slope_pct", 0.01)),
        "level_m": float(tol.get("level_m", 0.01)),
        "en_m": float(tol.get("en_m", 0.05)),
        "report_pct": float(tol.get("report_pct", 0.02)),
        "w3_threshold_tol": float(tol.get("w3_threshold_tol", 0.01)),
        "raw": doc,
    }


def load_wet_layers(path=None, site=None):
    doc = read_json(path or LAYERS_PATH)
    site_key = (site or doc.get("default_site") or "NMAR").upper()
    sites = doc.get("sites") or {}
    site_doc = sites.get(site_key) or {}
    shared = doc.get("shared_layers") or {}
    raw_layers = site_doc.get("layers")
    if raw_layers == "shared" or raw_layers is None:
        layer_src = shared
    elif isinstance(raw_layers, dict) and raw_layers:
        layer_src = {**shared, **raw_layers}
    else:
        layer_src = shared
    layers = {}
    for name, meta in layer_src.items():
        layers[name.casefold()] = dict(meta)
        layers[name] = dict(meta)
    patterns = list(doc.get("layer_patterns") or [])
    patterns.extend(site_doc.get("layer_patterns") or [])
    return {"site": site_key, "layers": layers, "patterns": patterns, "raw": doc}


def layer_short(name):
    if not name:
        return ""
    return name.rsplit("$", 1)[-1].strip()


def _match_layer_pattern(short_name, patterns):
    for pat in patterns or []:
        rx = pat.get("regex")
        if not rx:
            continue
        m = re.search(rx, short_name)
        if not m:
            continue
        meta = {k: v for k, v in pat.items() if k not in ("regex", "diameter_group", "note")}
        g = pat.get("diameter_group")
        if g is not None:
            try:
                meta["diameter_mm"] = float(m.group(int(g)))
            except (IndexError, TypeError, ValueError):
                pass
        return meta
    return None


def resolve_layer(layer_name, layers_map):
    if not layer_name:
        return None
    layers = layers_map.get("layers") or {}
    short = layer_short(layer_name)
    for cand in (layer_name, short, (layer_name or "").strip()):
        if cand in layers:
            return dict(layers[cand])
        cf = cand.casefold()
        if cf in layers:
            return dict(layers[cf])
        for key, meta in layers.items():
            if isinstance(key, str) and key.casefold() == cf:
                return dict(meta)
            if isinstance(key, str) and (cf in key.casefold() or key.casefold() in cf):
                return dict(meta)
    # Diameter / utility from Pipe Dia (N) / %%c(N)mm patterns (DQ-6)
    matched = _match_layer_pattern(short, layers_map.get("patterns"))
    if matched:
        return matched
    return None


def ft_to_m(v):
    if v is None:
        return None
    return float(v) * FT_TO_M


def ft_to_mm(v):
    if v is None:
        return None
    return float(v) * FT_TO_MM


def slope_pct(length_m, drop_m):
    if length_m is None or length_m <= 0 or drop_m is None:
        return None
    return 100.0 * float(drop_m) / float(length_m)


def evid(eid, source_type, extract, tier="deterministic", **extra):
    e = {"evidence_id": eid, "tier": tier, "source_type": source_type, "extract": extract}
    e.update(extra)
    return e


def finding_stub(check, title, statement, issue_type, severity, status, evidence,
                 site, sub, run_id, location=None, status_cap=None, source_doc=None,
                 note=None, required_value=None, unit=None):
    """SPC-0003-shaped candidate — report only; reference.rule_id is always null (DQ-11)."""
    fid_run = run_id or f"{site or 'QRWN'}-SYN-19700101T0000"
    n = abs(hash(f"{check}:{title}:{site}:{sub}")) % 1000
    if status_cap == "Probable" and status in (
            "Confirmed", "Missing information", "Not auto-verifiable"):
        status = "Probable"
    ref = {
        "rule_id": None,
        "source_doc": source_doc or "Employer's Requirements (connected 83-page edition)",
        "clause": check,
        "rulebook_version": None,
    }
    if required_value is not None:
        ref["required_value"] = required_value
    if unit is not None:
        ref["unit"] = unit
    return {
        "schema_version": "1.0",
        "finding_id": f"{fid_run}/{AGENT}-{n:03d}",
        "stage": "candidate",
        "run_id": fid_run,
        "site": site or "QRWN",
        "sub_id": sub or "SYN",
        "agent": AGENT,
        "agent_versions": {SKILL_NAME: __version__},
        "title": title[:120],
        "statement": statement[:1500],
        "issue_type": issue_type,
        "location": location or {"document_no": "WET-NETWORK", "package_level": True},
        "severity": severity,
        "confidence": "Medium",
        "evidence": evidence,
        "reference": ref,
        "proposed_status": status,
        "owner": {"role": "DMR", "name": None},
        "created_at": now_iso(),
        "report_only": True,
        "note": note or (
            "DQ-11: report only until WUT rule states are set in the next rulebook "
            "(skill 14 DO-4 pattern). Rule IDs cited in text only."
        ),
    }


def comparison_row(check, quantity, sources, values, units, difference, pct, result,
                   severity=None, status=None, evidence=None, note=None):
    return {
        "check": check,
        "quantity": quantity,
        "sources": sources,
        "values": values,
        "units": units,
        "difference": difference,
        "pct_difference": pct,
        "result": result,
        "severity": severity,
        "proposed_status": status,
        "evidence": evidence or [],
        "note": note,
    }


def stage_pct(stage):
    s = str(stage or "").strip()
    m = re.search(r"(\d{2,3})\s*%", s)
    if m:
        return int(m.group(1))
    low = s.casefold()
    if "concept" in low or "pd" in low.split():
        return 30
    if "detailed" in low or "dd" in low.split():
        return 60
    return None


def main_guard(fn):
    try:
        return fn()
    except ValueError as exc:
        return fail(EXIT_INPUT, str(exc))
    except FileNotFoundError as exc:
        return fail(EXIT_INPUT, str(exc))
    except ImportError as exc:
        return fail(EXIT_DEP, str(exc))
