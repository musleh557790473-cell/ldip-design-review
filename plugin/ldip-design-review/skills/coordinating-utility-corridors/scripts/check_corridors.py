#!/usr/bin/env python3
"""C1–C6 utility-corridor checks (skill 14, SPC-0006 Rev O §4.17).

DO-4: report only — findings carry issue_type but **no rule_id**.
DO-5: C2 value findings wait for R-O5 (information rows only).
DO-6: C4 at PD = Not auto-verifiable (+ spacing table).
DO-7: C5 tolerance = band half-width + 0.5 m.

Usage:
  python check_corridors.py --sections <sections.json> --out <corridors.json>
      [--plan <plan_linework.json>] [--params <params.json>]
      [--site QRWN] [--sub <sub-id>] [--run <run-id>] [--stage "30% Concept Design"]

Exit: 0 done · 2 input · 4 schema. No network, no shell.
"""
__version__ = "0.1.0"

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corridor_common import (  # noqa: E402
    EXIT_OK, EXIT_SCHEMA, PARAMS_PATH, SCHEMA_PATH, atomic_write_json, emit, fail,
    load_params, main_guard, path_ok, read_json, side_of_cl,
)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def evid(eid, source_type, extract, tier="deterministic", **extra):
    e = {"evidence_id": eid, "tier": tier, "source_type": source_type, "extract": extract}
    e.update(extra)
    return e


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


def finding_stub(check, title, statement, issue_type, severity, status, evidence,
                 site, sub, run_id, location=None, status_cap=None):
    """SPC-0003-shaped candidate — report only; reference.rule_id is always null (DO-4)."""
    agent = "COO"
    fid_run = run_id or f"{site or 'QRWN'}-SYN-19700101T0000"
    n = abs(hash(f"{check}:{title}:{site}:{sub}")) % 1000
    # PDF route caps every finding status at Probable (DO-2 / T14-5)
    if status_cap == "Probable" and status in (
            "Confirmed", "Missing information", "Not auto-verifiable"):
        status = "Probable"
    return {
        "schema_version": "1.0",
        "finding_id": f"{fid_run}/{agent}-{n:03d}",
        "stage": "candidate",
        "run_id": fid_run,
        "site": site or "QRWN",
        "sub_id": sub or "SYN",
        "agent": agent,
        "agent_versions": {"coordinating-utility-corridors": __version__},
        "title": title[:120],
        "statement": statement[:1500],
        "issue_type": issue_type,
        "location": location or {"document_no": "UTL-30000", "package_level": False},
        "severity": severity,
        "confidence": "Medium",
        "evidence": evidence,
        "reference": {
            "rule_id": None,
            "source_doc": "SPC-0006 Rev O §4.17",
            "clause": check,
            "rulebook_version": None,
        },
        "proposed_status": status,
        "owner": {"role": "IM / CM", "name": None},
        "created_at": _now(),
        "report_only": True,
        "note": "DO-4: report only until COR-001 / 002 / 101 states change in rules_v9.",
    }


def run_c1(sections, params, rows, findings, meta, status_cap):
    """COR-101 presence — each required utility in each section."""
    required = params["required"]
    for sec in sections:
        label = sec.get("label") or "?"
        present = set((sec.get("utilities") or {}).keys())
        for util in required:
            if util in present:
                rows.append(comparison_row(
                    "C1", f"presence:{label}:{util}", ["section"],
                    {"section": label, "utility": util, "present": True},
                    None, None, None, "pass",
                    note="COR-101 presence (report-only)",
                ))
            else:
                st = "Probable" if status_cap == "Probable" else "Missing information"
                rows.append(comparison_row(
                    "C1", f"presence:{label}:{util}", ["section"],
                    {"section": label, "utility": util, "present": False},
                    None, None, None, "flag",
                    severity="Minor", status=st,
                    note="COR-101 missing utility band",
                ))
                findings.append(finding_stub(
                    "C1", f"Missing {util} band in section {label}",
                    f"Section {label} has no band for required utility '{util}' "
                    f"(COR-101 presence). Searched layers mapped in utility_layers.",
                    "missing_information", "Minor", st,
                    [evid("E01", "script_output",
                          f"section={label} missing={util} present={sorted(present)}")],
                    meta["site"], meta["sub"], meta["run"],
                    location={"document_no": meta.get("doc") or "UTL-30000",
                              "section": label, "utility": util},
                    status_cap=status_cap,
                ))


def run_c2(sections, params, rows, findings, meta, status_cap):
    """COR-001 offsets — information only until R-O5 (DO-5). No value findings."""
    for sec in sections:
        label = sec.get("label") or "?"
        for util, rec in (sec.get("utilities") or {}).items():
            off = rec.get("offset_to_curb")
            rows.append(comparison_row(
                "C2", f"offset:{label}:{util}", ["section"],
                {
                    "section": label, "utility": util,
                    "offset_to_curb_m": off, "side": rec.get("side"),
                    "centre": rec.get("centre"),
                    "curb_left": sec.get("curb_left"), "curb_right": sec.get("curb_right"),
                },
                "m", None, None, "information",
                note="DO-5: C2 value judgement waits for R-O5 (ER §7.10(e) wording)",
            ))
    # Explicitly no findings from C2
    return


def run_c3(sections, params, rows, findings, meta, status_cap):
    """COR-001 side rule — dry not on the same side of CL as wet."""
    wet_set, dry_set = params["wet"], params["dry"]
    for sec in sections:
        label = sec.get("label") or "?"
        cl = sec.get("cl_x") or 0.0
        wet_sides, dry_sides = set(), set()
        wet_on, dry_on = defaultdict(list), defaultdict(list)
        for util, rec in (sec.get("utilities") or {}).items():
            side = rec.get("side") or side_of_cl(rec.get("centre"), cl)
            if side in (None, "on_cl"):
                continue
            if util in wet_set:
                wet_sides.add(side)
                wet_on[side].append(util)
            if util in dry_set:
                dry_sides.add(side)
                dry_on[side].append(util)
        conflict = sorted(wet_sides & dry_sides)
        if not conflict:
            rows.append(comparison_row(
                "C3", f"side:{label}", ["section"],
                {"section": label, "wet_sides": sorted(wet_sides),
                 "dry_sides": sorted(dry_sides)},
                None, None, None, "pass",
                note="Dry utilities opposite wet (COR-001 side rule)",
            ))
            continue
        rows.append(comparison_row(
            "C3", f"side:{label}", ["section"],
            {"section": label, "conflict_sides": conflict,
             "wet_on": {s: wet_on[s] for s in conflict},
             "dry_on": {s: dry_on[s] for s in conflict}},
            None, None, None, "flag",
            severity="Major", status="Probable",
            note="COR-001: dry utilities on same side of CL as wet",
        ))
        findings.append(finding_stub(
            "C3", f"Dry utilities share the wet side in section {label}",
            f"Section {label}: dry utilities on side(s) {conflict} also occupied by wet "
            f"utilities (COR-001 side rule). Wet={ {s: wet_on[s] for s in conflict} }; "
            f"dry={ {s: dry_on[s] for s in conflict} }.",
            "design_error", "Major", "Probable",
            [evid("E01", "script_output",
                  f"section={label} conflict={conflict}")],
            meta["site"], meta["sub"], meta["run"],
            location={"document_no": meta.get("doc") or "UTL-30000", "section": label},
            status_cap=status_cap,
        ))


def _edge_spacing(a, b):
    """Min and max horizontal spacing between two bands (edge-to-edge / far-edge)."""
    # centre-to-centre
    cc = abs(float(a["centre"]) - float(b["centre"]))
    # gap between near edges (0 if overlapping/touching)
    if a["x_max"] < b["x_min"]:
        near = b["x_min"] - a["x_max"]
        far = b["x_max"] - a["x_min"]
    elif b["x_max"] < a["x_min"]:
        near = a["x_min"] - b["x_max"]
        far = a["x_max"] - b["x_min"]
    else:
        # overlapping or touching
        near = 0.0
        far = max(a["x_max"], b["x_max"]) - min(a["x_min"], b["x_min"])
    return cc, near, far


def run_c4(sections, params, rows, findings, meta, status_cap):
    """COR-002 horizontal — spacing table; < 1.0 m → Not auto-verifiable (DO-6)."""
    power = params["power"]
    min_m = params["c4_min_m"]
    for sec in sections:
        label = sec.get("label") or "?"
        utils = sec.get("utilities") or {}
        power_utils = {u: r for u, r in utils.items() if u in power or r.get("power")}
        if not power_utils:
            rows.append(comparison_row(
                "C4", f"spacing:{label}", ["section"],
                {"section": label}, None, None, None, "not_run",
                note="no power (MV/LV) band in section",
            ))
            continue
        for pu, prec in power_utils.items():
            for ou, orec in utils.items():
                if ou == pu:
                    continue
                cc, near, far = _edge_spacing(prec, orec)
                nav = cc < min_m
                rows.append(comparison_row(
                    "C4", f"spacing:{label}:{pu}:{ou}", ["section"],
                    {
                        "section": label, "power": pu, "other": ou,
                        "centre_to_centre_m": round(cc, 6),
                        "near_edge_m": round(near, 6),
                        "max_edge_m": round(far, 6),
                        "threshold_m": min_m,
                    },
                    "m", round(cc, 6), None,
                    "information" if nav else "pass",
                    status="Not auto-verifiable" if nav else None,
                    note=("DO-6: position inside band unknown at PD — Not auto-verifiable"
                          if nav else "COR-002 horizontal spacing table"),
                ))
                if nav:
                    findings.append(finding_stub(
                        "C4", f"{pu}↔{ou} spacing not auto-verifiable in {label}",
                        f"Section {label}: {pu} to {ou} centre-to-centre {cc:.3f} m "
                        f"(< {min_m} m) with max edge spacing {far:.3f} m. "
                        f"Position inside each band is unknown at PD (DO-6) → "
                        f"Not auto-verifiable. Vertical clearances wait for DD.",
                        "design_error", "Major", "Not auto-verifiable",
                        [evid("E01", "script_output",
                              f"cc={cc:.3f} max_edge={far:.3f} threshold={min_m}")],
                        meta["site"], meta["sub"], meta["run"],
                        location={"document_no": meta.get("doc") or "UTL-30000",
                                  "section": label, "utilities": [pu, ou]},
                        status_cap=status_cap,
                    ))


def run_c5(sections, plan, params, rows, findings, meta, status_cap):
    """Plan network vs corridor band — offset ≤ half-width + 0.5 m (DO-7)."""
    if not plan:
        rows.append(comparison_row(
            "C5", "plan_vs_band", ["plan"], {}, None, None, None, "not_run",
            note="no --plan linework; C5 skipped",
        ))
        return
    # plan schema: {networks: [{utility, points: [[x,y],...] or offset_m, section?}]}
    # or {samples: [{utility, section, offset_from_band_m, band_half_width_m}]}
    samples = list(plan.get("samples") or [])
    if not samples and plan.get("networks"):
        # Derive samples: for each network point near a section cut, compare to band centre
        # Minimal support: networks carry precomputed offset_from_band_m
        for net in plan["networks"]:
            if net.get("offset_from_band_m") is None:
                continue
            samples.append({
                "utility": net.get("utility"),
                "section": net.get("section"),
                "offset_from_band_m": net["offset_from_band_m"],
                "band_half_width_m": net.get("band_half_width_m"),
            })
    if not samples:
        rows.append(comparison_row(
            "C5", "plan_vs_band", ["plan"], {}, None, None, None, "not_run",
            note="plan provided but no samples/networks with offsets",
        ))
        return

    sec_index = {s.get("label"): s for s in sections}
    extra = params["c5_extra_m"]
    for sample in samples:
        util = sample.get("utility")
        label = sample.get("section")
        sec = sec_index.get(label) or {}
        band = (sec.get("utilities") or {}).get(util) if sec else None
        half = sample.get("band_half_width_m")
        if half is None and band:
            half = float(band.get("width") or 0) / 2.0
        if half is None:
            half = 0.0
        tol = half + extra
        off = abs(float(sample.get("offset_from_band_m") or 0.0))
        flag = off > tol + 1e-9
        rows.append(comparison_row(
            "C5", f"plan:{label}:{util}", ["plan", "section"],
            {"section": label, "utility": util, "offset_m": off,
             "tolerance_m": tol, "band_half_width_m": half, "extra_m": extra},
            "m", off, None,
            "flag" if flag else "pass",
            severity="Minor" if flag else None,
            status="Probable" if flag else None,
            note="DO-7: tolerance = band half-width + 0.5 m",
        ))
        if flag:
            findings.append(finding_stub(
                "C5", f"Plan {util} outside band on section {label}",
                f"Discipline network for '{util}' is {off:.3f} m from its corridor band "
                f"on section {label} (tolerance {tol:.3f} m = half-width {half:.3f} + "
                f"{extra} m).",
                "inconsistency", "Minor", "Probable",
                [evid("E01", "script_output", f"offset={off:.3f} tol={tol:.3f}")],
                meta["site"], meta["sub"], meta["run"],
                location={"document_no": meta.get("doc") or "UTL-10000",
                          "section": label, "utility": util},
                status_cap=status_cap,
            ))


def run_c6(sections, plan, params, rows, findings, meta, status_cap):
    """Section vs plan — every section type cut on plan; every road covered; duplicates."""
    sec_labels = [s.get("label") for s in sections if s.get("label")]
    # Duplicates among section sheets
    seen = {}
    dupes = []
    for lab in sec_labels:
        seen[lab] = seen.get(lab, 0) + 1
    for lab, n in seen.items():
        if n > 1:
            dupes.append(lab)

    plan_cuts = list((plan or {}).get("section_cuts") or (plan or {}).get("cuts") or [])
    plan_roads = list((plan or {}).get("roads") or [])
    covered = set(plan_cuts)
    missing_on_plan = [lab for lab in seen if lab not in covered] if plan_cuts else []
    # Roads without a section: plan may list road ids with required section type
    uncovered_roads = []
    for road in plan_roads:
        need = road.get("section") or road.get("section_type")
        if need and need not in seen:
            uncovered_roads.append(road.get("id") or road.get("name") or need)

    note_parts = []
    if dupes:
        note_parts.append(f"duplicate section sheets: {dupes}")
    if missing_on_plan:
        note_parts.append(f"section types not cut on plan: {missing_on_plan}")
    if uncovered_roads:
        note_parts.append(f"roads without section: {uncovered_roads}")

    if not plan:
        rows.append(comparison_row(
            "C6", "section_vs_plan", ["section"],
            {"section_types": sorted(seen)}, None, None, None, "information",
            note="no --plan; duplicate check only: " + (str(dupes) if dupes else "none"),
        ))
        if dupes:
            findings.append(finding_stub(
                "C6", f"Duplicate section sheets: {', '.join(dupes)}",
                f"Section label(s) appear more than once: {dupes}.",
                "inconsistency", "Observation", "Probable",
                [evid("E01", "script_output", f"duplicates={dupes}")],
                meta["site"], meta["sub"], meta["run"],
                location={"document_no": meta.get("doc") or "UTL-30000"},
                status_cap=status_cap,
            ))
        return

    result = "observation" if note_parts else "pass"
    rows.append(comparison_row(
        "C6", "section_vs_plan", ["section", "plan"],
        {"section_types": sorted(seen), "plan_cuts": plan_cuts,
         "missing_on_plan": missing_on_plan, "uncovered_roads": uncovered_roads,
         "duplicates": dupes},
        None, None, None, result,
        severity="Observation" if note_parts else None,
        status="Probable" if note_parts else None,
        note="; ".join(note_parts) if note_parts else "all section types cut; no duplicates",
    ))
    if note_parts:
        findings.append(finding_stub(
            "C6", "Section vs plan coverage observation",
            "; ".join(note_parts) + ".",
            "inconsistency", "Observation", "Probable",
            [evid("E01", "script_output", "; ".join(note_parts))],
            meta["site"], meta["sub"], meta["run"],
            location={"document_no": meta.get("doc") or "UTL-10000"},
            status_cap=status_cap,
        ))


def _rows_for_corridors(sections):
    """One corridors.json row per section × utility."""
    out = []
    for sec in sections:
        label = sec.get("label")
        for util, rec in (sec.get("utilities") or {}).items():
            out.append({
                "section": label,
                "utility": util,
                "x_min": rec.get("x_min"),
                "x_max": rec.get("x_max"),
                "centre": rec.get("centre"),
                "width": rec.get("width"),
                "offset_to_curb": rec.get("offset_to_curb"),
                "side": rec.get("side"),
                "class": rec.get("class"),
                "power": rec.get("power"),
                "layer": rec.get("layer"),
                "handle": rec.get("handle"),
                "page": rec.get("page"),
                "cl_x": sec.get("cl_x"),
                "curb_left": sec.get("curb_left"),
                "curb_right": sec.get("curb_right"),
            })
    return out


def build_spacing_table(sections, params):
    power = params["power"]
    table = []
    for sec in sections:
        utils = sec.get("utilities") or {}
        for pu, prec in utils.items():
            if pu not in power and not prec.get("power"):
                continue
            for ou, orec in utils.items():
                if ou == pu:
                    continue
                cc, near, far = _edge_spacing(prec, orec)
                table.append({
                    "section": sec.get("label"),
                    "power": pu, "other": ou,
                    "centre_to_centre_m": round(cc, 6),
                    "near_edge_m": round(near, 6),
                    "max_edge_m": round(far, 6),
                })
    return table


def validate_schema(doc):
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        return  # optional in local runs; CI has jsonschema
    schema = read_json(SCHEMA_PATH)
    Draft202012Validator(schema).validate(doc)


def main(argv=None):
    ap = argparse.ArgumentParser(description="C1–C6 corridor checks (skill 14)")
    ap.add_argument("--sections", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--plan", default=None)
    ap.add_argument("--params", default=str(PARAMS_PATH))
    ap.add_argument("--site", default="QRWN")
    ap.add_argument("--sub", default="SYN")
    ap.add_argument("--run", default=None)
    ap.add_argument("--stage", default="30% Concept Design")
    ap.add_argument("--doc", default=None, help="Document number for finding locations")
    a = ap.parse_args(argv)

    def run():
        if not path_ok(a.out):
            raise ValueError(f"path longer than 250 characters: {a.out}")
        sections_doc = read_json(a.sections)
        sections = list(sections_doc.get("sections") or [])
        if not sections:
            raise ValueError("sections.json has no sections")
        params = load_params(a.params)
        plan = read_json(a.plan) if a.plan else None
        status_cap = sections_doc.get("status_cap")
        meta = {
            "site": a.site, "sub": a.sub,
            "run": a.run or f"{a.site}-{a.sub}-19700101T0000",
            "doc": a.doc, "stage": a.stage,
        }
        rows, findings = [], []
        run_c1(sections, params, rows, findings, meta, status_cap)
        run_c2(sections, params, rows, findings, meta, status_cap)
        run_c3(sections, params, rows, findings, meta, status_cap)
        run_c4(sections, params, rows, findings, meta, status_cap)
        run_c5(sections, plan, params, rows, findings, meta, status_cap)
        run_c6(sections, plan, params, rows, findings, meta, status_cap)

        # Cap all statuses at Probable when PDF route (DO-2 / T14-5)
        if status_cap == "Probable":
            for f in findings:
                if f.get("proposed_status") in (
                        "Confirmed", "Missing information", "Not auto-verifiable"):
                    f["proposed_status"] = "Probable"

        doc = {
            "corridors_version": "1.0",
            "rows": _rows_for_corridors(sections),
            "spacing_table": build_spacing_table(sections, params),
            "comparisons": rows,
            "findings": findings,
            "counts": {
                "sections": len(sections),
                "rows": len(sections),  # filled below
                "comparisons": len(rows),
                "findings": len(findings),
                "c3_flags": sum(1 for r in rows if r["check"] == "C3" and r["result"] == "flag"),
                "c1_missing": sum(1 for r in rows if r["check"] == "C1" and r["result"] == "flag"),
            },
            "meta": {
                "marking": "report only (DO-4); C2 information until R-O5 (DO-5); "
                           "C4 Not auto-verifiable at PD (DO-6)",
                "site": a.site, "sub": a.sub, "run": meta["run"], "stage": a.stage,
                "route": sections_doc.get("route"),
                "status_cap": status_cap,
                "generated_at": _now(),
                "checker": f"check_corridors/{__version__}",
                "source": sections_doc.get("source"),
            },
        }
        doc["counts"]["rows"] = len(doc["rows"])
        try:
            validate_schema(doc)
        except Exception as exc:
            return fail(EXIT_SCHEMA, f"corridors.json schema: {exc}")

        atomic_write_json(a.out, doc)
        emit({
            "status": "ok",
            "sections": len(sections),
            "rows": len(doc["rows"]),
            "findings": len(findings),
            "c1_missing": doc["counts"]["c1_missing"],
            "c3_flags": doc["counts"]["c3_flags"],
            "out": str(a.out),
        })
        return EXIT_OK

    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
