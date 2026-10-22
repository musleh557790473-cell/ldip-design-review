#!/usr/bin/env python3
"""Turn skill 13 check results into SPC-0003 candidate findings (agent RDE).

SPC-0006 Rev L §4.14.13 + L-a. Copied pattern from skill 10 — skills stay independent.
Skill version 0.1.0. Surface-derived values → Probable (DL-9).

Usage: python build_candidates.py --results <a.json> [...] --rules <rules_RDE.json>
           --run <run-id> --site <SITE> --sub <sub-id> --evidence-dir <dir>
           [--review-root <LDIP_Review>] [--start 1] --out <candidates.json>
"""
__version__ = "0.1.1"

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ewk_common import (CONFIDENCE, EVIDENCE_TIER, EXIT_DEPENDENCY, EXIT_INPUT, EXIT_OK,  # noqa: E402
                        EXIT_SCHEMA, EwkError, emit, lower, read_json, sha256_file)

PLUGIN_ROOT = HERE.parents[2]
SCHEMA = PLUGIN_ROOT / "schemas" / "finding.schema.json"
RIYADH = timezone(timedelta(hours=3))
OP = {"ge": "≥", "le": "≤", "eq": "=", "in": "∈", "consistent": "consistent:", "present": "shown:"}
NUMBER = re.compile(r"^([A-Za-z0-9]+(?:-[A-Za-z0-9]+){5,})")


def doc_no(src):
    if src.get("document_no"):
        return src["document_no"]
    m = NUMBER.match(Path(src.get("path") or "x").stem)
    return m.group(1) if m else Path(src.get("path") or "unknown").stem


def fmt(v):
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, dict):
        return ", ".join(f"{k}: {fmt(x)}" for k, x in v.items())
    if isinstance(v, (list, tuple)):
        return " / ".join(fmt(x) for x in v)
    return str(v)


class Builder:
    def __init__(self, a, rules, rb_version, now):
        self.a, self.rules, self.rbv, self.now, self.n = a, rules, rb_version, now, a.start

    def evidence(self, samples, rid):
        out = []
        for i, s in enumerate(samples[:10], 1):
            ev = s["evidence"]
            st = ev.get("source_type") or "script_output"
            if st not in EVIDENCE_TIER:
                st = "script_output"
            e = {"evidence_id": f"E{i:02d}", "tier": EVIDENCE_TIER[st], "source_type": st,
                 "extract": (ev.get("extract") or "")[:900],
                 "measured_value": s["observed"] if isinstance(s["observed"], (int, float, str)) else fmt(s["observed"]),
                 "unit": s.get("unit"),
                 "script": {"name": f"reviewing-earthworks-geotech/scripts/{s['_script']}",
                            "version": s["_version"], "exit_code": 0}}
            if ev.get("path") and ev.get("sha256"):
                e["artifact_path"] = ev["path"]
                e["artifact_sha256"] = ev["sha256"]
            out.append(e)
        return out

    def location(self, s0, samples):
        ev = s0["evidence"]
        if not ev.get("path"):
            loc = {"document_no": "PACKAGE", "package_level": True}
            if ev.get("xy"):
                loc = {"document_no": "PACKAGE",
                       "xy": {"crs": "EPSG:32638", "e": float(ev["xy"][0]), "n": float(ev["xy"][1])}}
            return loc
        loc = {"document_no": doc_no(ev), "file_path": ev["path"],
               "file_sha256": ev.get("sha256") or ("0" * 64)}
        if ev.get("xy") and isinstance(ev["xy"], (list, tuple)):
            loc["xy"] = {"crs": "EPSG:32638", "e": float(ev["xy"][0]), "n": float(ev["xy"][1])}
        else:
            loc["package_level"] = True
        return loc

    def finding(self, key, samples):
        rid = key[0]
        pname = samples[0]["parameter"]
        rule = self.rules[rid]
        p = (rule.get("parameters") or {}).get(pname) or {}
        s0 = samples[0]
        st = s0["evidence"].get("source_type") or "script_output"
        status = rule["default_status"].get(st) or rule["default_status"].get("script_output") or "Probable"
        if s0.get("status_override"):
            status = s0["status_override"]
        status = lower(status, p.get("cap") or rule.get("cap") or "Confirmed")
        for s in samples:
            if s.get("link_cap"):
                status = lower(status, s["link_cap"])
        # DL-9: surface-derived → Probable
        if st in ("script_output", "landxml") and status == "Confirmed":
            status = "Probable"
        severity = s0.get("severity_override") or rule["severity_default"]
        obs = "; ".join(
            f"{s['subject']}: {fmt(s['observed'])}" + (f" {s['unit']}" if s.get("unit") else "")
            for s in samples[:5])
        req = f"{OP.get(s0['comparison'], s0['comparison'])} {fmt(s0['required'])}"
        if s0.get("unit"):
            req += f" {s0['unit']}"
        src_clause = f"{p.get('source_id', '')} {p.get('clause', '')}".strip()
        note = samples[0].get("note") or ""
        # D8: statement uses total_count (all items); evidence lists up to ten largest
        total = s0.get("total_count")
        if total is None:
            total = max((s.get("total_count") or 0 for s in samples), default=0) or len(samples)
        statement = (f"{total} occurrence(s). Observed — {obs}. Required ({rid} {pname}"
                     + (f", {src_clause}" if src_clause else "") + f"): {req}. "
                     f"Requirement ({rid}): {rule['statement']}")
        if note:
            statement += f" Note: {note}."
        # 3H:1V preferred mentioned in finding text only (DL-1)
        if rid == "EWK-002" and "slope" in pname:
            statement += " (3H:1V preferred is stated in the rule but not checked — no parameter)."
        f = {
            "schema_version": "1.0", "finding_id": f"{self.a.run}/RDE-{self.n:03d}", "stage": "candidate",
            "run_id": self.a.run, "site": self.a.site, "sub_id": self.a.sub, "agent": "RDE",
            "agent_versions": {"reviewing-earthworks-geotech": __version__},
            "title": f"{rid} {rule['title']}: {pname}"[:120],
            "statement": statement[:1500],
            "issue_type": s0.get("issue_type") or rule["issue_type"],
            "location": self.location(s0, samples), "severity": severity,
            "confidence": CONFIDENCE.get(EVIDENCE_TIER.get(st, "deterministic"), "High"),
            "evidence": self.evidence(samples, rid),
            "reference": {"rule_id": rid, "parameter": pname, "rulebook_version": self.rbv,
                          "source_doc": f"LDIP Rulebook {self.rbv} (AICC-BIM-SPC-0004); {p.get('source_id', '')}".strip("; "),
                          "clause": f"{rid}: {p.get('clause') or rule['title']}",
                          "required_value": s0["required"] if isinstance(s0["required"], (int, float, str)) else fmt(s0["required"]),
                          "tolerance": s0.get("tolerance"), "unit": s0.get("unit")},
            "proposed_status": status, "owner": {"role": rule["owner_role"], "name": None},
            "created_at": self.now,
        }
        self.n += 1
        return f

    def missing(self, item):
        rid = item["rules"][0]
        rule = self.rules.get(rid)
        if rule is None:
            return None
        status = lower("Missing information", rule.get("cap") or "Confirmed")
        searched = ", ".join(item.get("searched") or []) or "(no file given)"
        statement = (f"{item['item']} is required from {item.get('required_from_phase')} "
                     f"(submission phase {item.get('submission_phase')}) in one of: "
                     f"{', '.join(item.get('carriers') or [])}; none of them holds it. "
                     f"Searched: {searched}. Rules that need it: {', '.join(item['rules'])}.")
        # D2: prefer deliverable / document number on MIDP MI locations
        doc = item.get("document_no") or "PACKAGE"
        loc = {"document_no": doc, "package_level": True} if doc == "PACKAGE" \
            else {"document_no": doc, "package_level": True}
        f = {
            "schema_version": "1.0", "finding_id": f"{self.a.run}/RDE-{self.n:03d}", "stage": "candidate",
            "run_id": self.a.run, "site": self.a.site, "sub_id": self.a.sub, "agent": "RDE",
            "agent_versions": {"reviewing-earthworks-geotech": __version__},
            "title": f"Missing earthworks information: {item['item']}"[:120],
            "statement": statement[:1500], "issue_type": "missing_information",
            "location": loc,
            "severity": rule["severity_default"], "confidence": "High",
            "evidence": [{"evidence_id": "E01", "tier": "deterministic", "source_type": "script_output",
                          "extract": f"searched: {searched}"[:900], "measured_value": 0,
                          "unit": "carriers holding the item",
                          "script": {"name": "reviewing-earthworks-geotech/scripts/check_presence.py",
                                     "version": item.get("_version", "?"), "exit_code": 0}}],
            "reference": {"rule_id": rid, "parameter": None, "rulebook_version": self.rbv,
                          "source_doc": f"{item.get('source_id') or 'SRC-14'} (MIDP / ER)",
                          "clause": item.get("clause") or "SPC-0006 Rev L Amendment L-a DL-15"},
            "proposed_status": status, "owner": {"role": rule["owner_role"], "name": None},
            "created_at": self.now,
        }
        self.n += 1
        return f


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", nargs="+", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--site", required=True, choices=["QRWN", "NMAR", "NRJS"])
    ap.add_argument("--sub", required=True)
    ap.add_argument("--evidence-dir", required=True)
    ap.add_argument("--review-root", default=".")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        rb = read_json(a.rules)
        docs = [read_json(p) for p in a.results]
    except EwkError as e:
        emit({"status": "error", "code": e.code, "detail": e.detail})
        return e.code
    if not re.match(r"^(QRWN|NMAR|NRJS)-[A-Za-z0-9._-]+-[0-9]{8}T[0-9]{4}$", a.run):
        emit({"status": "error", "code": EXIT_INPUT, "detail": f"invalid run id {a.run}"})
        return EXIT_INPUT
    rules = {r["rule_id"]: r for r in rb["rules"]}
    b = Builder(a, rules, rb.get("rulebook_version"), datetime.now(RIYADH).isoformat(timespec="seconds"))
    groups, not_run, missing = {}, [], []
    for d in docs:
        tool = d.get("tool") or {}
        if d.get("mode") == "required":
            for it in d.get("items", []):
                if it["result"] == "fail":
                    missing.append(dict(it, _version=tool.get("version", "?")))
                elif it["result"] == "not_run":
                    not_run.append({"item": it.get("key"), "reason": it.get("reason")})
            continue
        not_run += d.get("not_run", [])
        for c in d.get("checks", []):
            if c.get("result") != "fail" or c["rule_id"] not in rules:
                continue
            c = dict(c, _script=tool.get("script", "?"), _version=tool.get("version", "?"))
            if c.get("mode_tag"):
                key = (c["rule_id"], c["parameter"], c["mode_tag"])
            else:
                key = (c["rule_id"], c["parameter"], d.get("mode"))
            groups.setdefault(key, []).append(c)
    try:
        findings = [b.finding(k, v) for k, v in sorted(groups.items(), key=lambda kv: tuple(str(x) for x in kv[0]))]
        findings += [f for f in (b.missing(m) for m in missing) if f]
    except EwkError as e:
        emit({"status": "error", "code": e.code, "detail": e.detail})
        return e.code
    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError:
        emit({"status": "error", "code": EXIT_DEPENDENCY, "detail": "jsonschema is not installed"})
        return EXIT_DEPENDENCY
    # Relax artifact presence for script_output paths that are temp side files
    v = Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")), format_checker=FormatChecker())
    # Strip artifact_path when file is missing (synthetic runs) — keep sha if present only with file
    for f in findings:
        for e in f.get("evidence") or []:
            apath = e.get("artifact_path")
            if apath and not Path(apath).is_file() and not (Path(a.review_root) / apath).is_file():
                e.pop("artifact_path", None)
                e.pop("artifact_sha256", None)
    errs = [f"/{i}/{'/'.join(map(str, e.absolute_path))}: {e.message[:150]}"
            for i, f in enumerate(findings) for e in v.iter_errors(f)]
    if errs:
        emit({"status": "error", "code": EXIT_SCHEMA, "detail": "candidates failed finding.schema.json",
              "errors": errs[:30]})
        return EXIT_SCHEMA
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    Path(a.evidence_dir).mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(findings, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out)
    emit({"status": "ok", "out": str(out), "findings": len(findings),
          "by_rule": {r: sum(1 for f in findings if f["reference"]["rule_id"] == r)
                      for r in sorted({f["reference"]["rule_id"] for f in findings})},
          "not_run": len(not_run)})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
