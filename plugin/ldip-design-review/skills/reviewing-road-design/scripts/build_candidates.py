#!/usr/bin/env python3
"""Turn skill 10 check results into SPC-0003 candidate findings (agent RDE) — SPC-0006 Rev D §4.13.10.

Inputs: the outputs of check_criteria.py, check_sections.py, check_alignment.py, build_register.py
(ROD-001) and check_required.py. One finding per (rule, parameter, class, mode) with count and up to ten
samples (SPC-0004 §5.7); tier-2 sub-checks (parameters tier2_*) once per (rule, parameter) per submittal,
severity Observation (Rev E §5.9). proposed_status = the lowest of the rule's default status for the
evidence source, the parameter cap from skill 8 (advisory -> Probable) and any link cap (positional
assignment, name-only class — D10-3). Missing information (check_required.py) -> one finding per item,
issue_type missing_information, listing what was searched (M5).
PDF evidence needs a crop (SPC-0003 tier extracted_text_crop): made here with skill 2's crop function from
the source PDF under --review-root; the PNG and its SHA-256 go to --evidence-dir.
0.3.0 (SPC-0006 Rev F §4.13C): also takes cross_check.json, road_class.json and location_checks.json; a
check may carry `status_override` (Missing information for a class not stated, K23) which is still lowered
to the rule / parameter / link caps; a sample whose evidence has `xy` gives the finding an EPSG:32638
location (first sample), and the other locations are listed in the statement and the evidence.
0.4.0 (SPC-0006 Rev G §4.13D): the phase B2 part 2 location kinds (low points, parking areas, calming devices,
school frontages) come through location_checks.json unchanged; a LandXML position [e, n] gives an EPSG:32638
location. The version is the skill version (agent_versions).
0.5.0 (SPC-0006 Rev H §4.13E, DH-1 ... DH-10): takes geometry_checks.json (check_geometry.py, phase C1); a check
may carry `severity_override` (Observation for an advisory rule, DH-7); the drawn-curve checks of ROD-017 / 018
carry `mode_tag` drawn_curves, so they form their own finding apart from the phase-A stated-criteria one (DH-2).
0.6.0 (SPC-0006 Rev I §4.13F, DI-1 ... DI-10): takes layout_checks.json (check_layout.py, phase C2: corner returns
and design vehicle, roundabout ICD / entry width / splitter islands / apron, DD driveways and sight triangles)
unchanged: one finding per (rule, parameter, class or junction type); the DD absences carry `status_override`
Missing information and `issue_type` missing_information; the advisory splitter-island length carries
`severity_override` Observation.

Usage: python build_candidates.py --results <a.json> [...] --rules <rules_RDE.json> --run <run-id> --site <SITE>
           --sub <sub-id> --evidence-dir <dir> [--review-root <LDIP_Review>] [--start 1] --out <candidates.json>
Exit: 0 written · 2 input error · 3 dependency · 4 candidates failed the finding schema (nothing written).
No network, no shell calls.
"""
__version__ = "0.6.0"

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from road_common import (CONFIDENCE, EVIDENCE_TIER, EXIT_DEPENDENCY, EXIT_INPUT, EXIT_OK, EXIT_SCHEMA,  # noqa: E402
                         RoadError, emit, lower, read_json, sha256_file)

PLUGIN_ROOT = HERE.parents[2]
SCHEMA = PLUGIN_ROOT / "schemas" / "finding.schema.json"
sys.path.insert(0, str(PLUGIN_ROOT / "skills" / "extracting-pdf-drawings" / "scripts"))
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
        self.crops = {}

    def crop(self, ev):
        """PDF crop of the evidence box; returns (artifact_path, sha256)."""
        key = (ev["path"], ev["page"], tuple(ev["bbox"]))
        if key in self.crops:
            return self.crops[key]
        from crop import crop as do_crop          # skill 2 (extracting-pdf-drawings)
        from pdf_common import PdfError
        pdf = Path(self.a.review_root) / ev["path"]
        x0, top, x1, bottom = ev["bbox"]
        box = [max(0.0, x0 - 4), max(0.0, top - 3), x1 + 4, bottom + 3]
        try:
            data, _ = do_crop(pdf, ev["page"], box, 150)
        except PdfError as exc:
            raise RoadError(EXIT_INPUT, f"crop failed for {ev['path']} p.{ev['page']}: {exc.detail}") from exc
        name = f"rde_{len(self.crops) + 1:03d}_p{ev['page']}.png"
        out = Path(self.a.evidence_dir) / name
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, out)
        sha = sha256_file(out)
        try:
            rel = out.resolve().relative_to(Path(self.a.review_root).resolve()).as_posix()
        except ValueError:
            rel = out.as_posix()
        self.crops[key] = (rel, sha)
        return rel, sha

    def evidence(self, samples, rid):
        out = []
        for i, s in enumerate(samples[:10], 1):
            ev = s["evidence"]
            st = ev["source_type"]
            e = {"evidence_id": f"E{i:02d}", "tier": EVIDENCE_TIER[st], "source_type": st, "extract": ev["extract"][:900],
                 "measured_value": s["observed"] if isinstance(s["observed"], (int, float, str)) else fmt(s["observed"]),
                 "unit": s.get("unit"),
                 "script": {"name": f"reviewing-road-design/scripts/{s['_script']}", "version": s["_version"], "exit_code": 0}}
            if e["tier"] == "extracted_text_crop":
                if not ev.get("bbox") or not ev.get("page"):
                    e["tier"], e["source_type"] = "deterministic", "script_output"
                else:
                    e["artifact_path"], e["artifact_sha256"] = self.crop(ev)
            out.append(e)
        return out

    def location(self, s0, samples):
        ev = s0["evidence"]
        if not ev.get("path"):                       # presence absence (phase B1): the whole submittal
            loc = {"document_no": "PACKAGE", "package_level": True}
            if ev.get("xy"):                          # phase B2: a location of the network (first sample)
                loc = {"document_no": "PACKAGE", "xy": {"crs": "EPSG:32638", "e": float(ev["xy"][0]), "n": float(ev["xy"][1])}}
            return loc
        loc = {"document_no": doc_no(ev), "file_path": ev["path"], "file_sha256": ev["sha256"]}
        st = ev["source_type"]
        if st.startswith("pdf") and ev.get("page"):
            loc["pdf"] = {"page": ev["page"], **({"bbox_pt": ev["bbox"]} if ev.get("bbox") else {})}
        elif st == "dwg_vector":
            hs = []
            for s in samples:
                for h in s["evidence"].get("handles", []):
                    if h not in hs:
                        hs.append(h)
            loc["dwg"] = {"handles": hs[:40], **({"layer": ev["layer"]} if ev.get("layer") else {})}
        elif st == "landxml" and ev.get("xy"):
            v = ev["xy"]                              # Rev G: a LandXML network position is [e, n]
            loc["xy"] = v if isinstance(v, dict) else {"crs": "EPSG:32638", "e": float(v[0]), "n": float(v[1])}
        if ev.get("xy") and isinstance(ev["xy"], (list, tuple)) and "xy" not in loc:
            loc["xy"] = {"crs": "EPSG:32638", "e": float(ev["xy"][0]), "n": float(ev["xy"][1])}
        else:
            loc["package_level"] = True
        return loc

    def finding(self, key, samples):
        rid = key[0]
        samples = sorted(samples, key=lambda s: (s.get("speed_basis") != "stated", s.get("speed_kmh") or 0))
        pname = samples[0]["parameter"]
        rule = self.rules[rid]
        p = (rule.get("parameters") or {}).get(pname) or {}
        s0 = samples[0]
        st = s0["evidence"]["source_type"]
        status = rule["default_status"].get(st) or rule["default_status"].get("script_output") or "Probable"
        if s0.get("status_override"):
            status = s0["status_override"]
        status = lower(status, p.get("cap") or rule["cap"])
        for s in samples:
            if s.get("link_cap"):
                status = lower(status, s["link_cap"])
        tier2 = pname.startswith("tier2_") or pname == "er_difference"
        severity = "Observation" if tier2 else (s0.get("severity_override") or rule["severity_default"])
        cls = sorted({s.get("class") for s in samples if s.get("class")})
        obs = "; ".join(f"{s['subject']}: {fmt(s['observed'])}" + (f" {s['unit']}" if s.get("unit") else "")
                        + (f" (required {OP.get(s['comparison'], s['comparison'])} {fmt(s['required'])})"
                           if s.get("speed_kmh") is not None else "")
                        for s in samples[:5])
        req = f"{OP.get(s0['comparison'], s0['comparison'])} {fmt(s0['required'])}" + (f" {s0['unit']}" if s0.get("unit") else "")
        if s0.get("tolerance"):
            req += f" (tolerance ±{fmt(s0['tolerance'])})"
        src_clause = f"{p.get('source_id', '')} {p.get('clause', '')}".strip()
        statement = (f"{len(samples)} occurrence(s). Observed — {obs}. Required ({rid} {pname}"
                     + (f", {src_clause}" if src_clause else "") + f"): {req}. Requirement ({rid}): {rule['statement']}")
        if tier2:
            statement = "Tier-2 value differs from the governing ER value (Observation, SPC-0004 Rev E §5.9). " + statement
        f = {
            "schema_version": "1.0", "finding_id": f"{self.a.run}/RDE-{self.n:03d}", "stage": "candidate",
            "run_id": self.a.run, "site": self.a.site, "sub_id": self.a.sub, "agent": "RDE",
            "agent_versions": {"reviewing-road-design": __version__},
            "title": f"{rid} {rule['title']}: {pname}" + (f" ({', '.join(cls)})" if cls else ""),
            "statement": statement[:1500], "issue_type": s0.get("issue_type") or rule["issue_type"],
            "location": self.location(s0, samples), "severity": severity,
            "confidence": CONFIDENCE[EVIDENCE_TIER[st]], "evidence": self.evidence(samples, rid),
            "reference": {"rule_id": rid, "parameter": pname, "rulebook_version": self.rbv,
                          "source_doc": f"LDIP Rulebook {self.rbv} (AICC-BIM-SPC-0004); {p.get('source_id', '')}".strip("; "),
                          "clause": f"{rid}: {p.get('clause') or rule['title']}",
                          "required_value": s0["required"] if isinstance(s0["required"], (int, float, str)) else fmt(s0["required"]),
                          "tolerance": s0.get("tolerance"), "unit": s0.get("unit")},
            "proposed_status": status, "owner": {"role": rule["owner_role"], "name": None}, "created_at": self.now,
        }
        f["title"] = f["title"][:120]
        self.n += 1
        return f

    def missing(self, item, rb):
        rid = item["rules"][0]
        rule = self.rules.get(rid)
        if rule is None:
            return None
        status = lower("Missing information", rule["cap"])
        searched = ", ".join(item["searched"]) or "(no file given)"
        statement = (f"{item['item']} is required from {item['required_from_phase']} (submission phase "
                     f"{item['submission_phase']}) in one of: {', '.join(item['carriers'])}; none of them holds it. "
                     f"Searched: {searched}. Rules that need it: {', '.join(item['rules'])}.")
        f = {
            "schema_version": "1.0", "finding_id": f"{self.a.run}/RDE-{self.n:03d}", "stage": "candidate",
            "run_id": self.a.run, "site": self.a.site, "sub_id": self.a.sub, "agent": "RDE",
            "agent_versions": {"reviewing-road-design": __version__},
            "title": f"Missing road information: {item['item']}"[:120], "statement": statement[:1500],
            "issue_type": "missing_information", "location": {"document_no": "PACKAGE", "package_level": True},
            "severity": rule["severity_default"], "confidence": "High",
            "evidence": [{"evidence_id": "E01", "tier": "deterministic", "source_type": "script_output",
                          "extract": f"searched: {searched}"[:900], "measured_value": 0, "unit": "carriers holding the item",
                          "script": {"name": "reviewing-road-design/scripts/check_required.py", "version": item["_version"], "exit_code": 0}}],
            "reference": {"rule_id": rid, "parameter": None, "rulebook_version": self.rbv,
                          "source_doc": f"{item.get('source_id') or 'SRC-07'} (Employer's Requirements)",
                          "clause": item.get("clause") or "SPC-0004 Rev E §5.11"},
            "proposed_status": status, "owner": {"role": rule["owner_role"], "name": None}, "created_at": self.now,
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
    except RoadError as e:
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
                    not_run.append({"item": it["key"], "reason": it.get("reason")})
            continue
        not_run += d.get("not_run", [])
        for c in d.get("checks", []):
            if c["result"] != "fail" or c["rule_id"] not in rules:
                continue
            c = dict(c, _script=tool.get("script", "?"), _version=tool.get("version", "?"))
            tier2 = c["parameter"].startswith("tier2_")
            if tier2:
                key = (c["rule_id"], c["parameter"])
            elif c.get("mode_tag"):                  # phase B1 presence: one finding per (rule, kind) (M4)
                key = (c["rule_id"], c["parameter"], c["mode_tag"])
            elif c.get("speed_kmh") is not None:     # D10-5: stated and ER-minimum speed in one finding
                key = (c["rule_id"], "speed_keyed", c.get("class"), d.get("mode"))
            else:
                key = (c["rule_id"], c["parameter"], c.get("class"), d.get("mode"))
            groups.setdefault(key, []).append(c)
    try:
        findings = [b.finding(k, v) for k, v in sorted(groups.items(), key=lambda kv: tuple(str(x) for x in kv[0]))]
        findings += [f for f in (b.missing(m, rb) for m in missing) if f]
    except RoadError as e:
        emit({"status": "error", "code": e.code, "detail": e.detail})
        return e.code
    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError:
        emit({"status": "error", "code": EXIT_DEPENDENCY, "detail": "jsonschema is not installed"})
        return EXIT_DEPENDENCY
    v = Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")), format_checker=FormatChecker())
    errs = [f"/{i}/{'/'.join(map(str, e.absolute_path))}: {e.message[:150]}" for i, f in enumerate(findings) for e in v.iter_errors(f)]
    if errs:
        emit({"status": "error", "code": EXIT_SCHEMA, "detail": "candidates failed finding.schema.json", "errors": errs[:30]})
        return EXIT_SCHEMA
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(findings, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out)
    emit({"status": "ok", "out": str(out), "findings": len(findings), "crops": len(b.crops),
          "by_rule": {r: sum(1 for f in findings if f["reference"]["rule_id"] == r) for r in sorted({f["reference"]["rule_id"] for f in findings})},
          "not_run": len(not_run)})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
