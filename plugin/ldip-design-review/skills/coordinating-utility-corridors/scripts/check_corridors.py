#!/usr/bin/env python3
"""C1–C6 utility-corridor checks (skill 14, SPC-0006 Rev O §4.17).

DO-4: report only — findings carry issue_type but **no rule_id**.
DO-5: C2 unsigned band–curb distance vs ER §7.10(e) positions (R-O5 / DO-5a…d).
DO-6: C4 at PD = Not auto-verifiable (+ spacing table).
DO-7: C5 tolerance = band half-width + 0.5 m.

Usage:
  python check_corridors.py --sections <sections.json> --out <corridors.json>
      [--plan <plan_linework.json>] [--plan-dwg <UTL-10000.dwg>]
      [--params <params.json>]
      [--site QRWN] [--sub <sub-id>] [--run <run-id>] [--stage "30% Concept Design"]

Exit: 0 done · 2 input · 4 schema. No network, no shell.
"""
__version__ = "0.1.1"

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corridor_common import (  # noqa: E402
    EXIT_OK, EXIT_SCHEMA, PARAMS_PATH, SCHEMA_PATH, atomic_write_json, emit, fail,
    c1_presence, iter_section_bands, load_params, main_guard, parse_section_label,
    path_ok, read_json, side_of_cl,
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


ER_C2_QUOTE = (
    "Utilities allocated per MOMRA cross-section typical: Water main 1.5 m from curb, "
    "sewer 3.0 m, storm 2.5 m, dry utilities opposite side."
)


def finding_stub(check, title, statement, issue_type, severity, status, evidence,
                 site, sub, run_id, location=None, status_cap=None, owner=None,
                 note=None, source_doc=None):
    """SPC-0003-shaped candidate — report only; reference.rule_id is always null (DO-4)."""
    agent = "COO"
    fid_run = run_id or f"{site or 'QRWN'}-SYN-19700101T0000"
    n = abs(hash(f"{check}:{title}:{site}:{sub}")) % 1000
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
            "source_doc": source_doc or "SPC-0006 Rev O §4.17",
            "clause": check,
            "rulebook_version": None,
        },
        "proposed_status": status,
        "owner": owner or {"role": "IM / CM", "name": None},
        "created_at": _now(),
        "report_only": True,
        "note": note or (
            "DO-4: report only until COR-001 / 002 / 101 states change in rules_v9."
        ),
    }


def run_c1(sections, params, rows, findings, meta, status_cap):
    """COR-101 presence — each required utility in each section (aliases / tree c1_as)."""
    required = params["required"]
    for sec in sections:
        label = sec.get("label") or "?"
        present = c1_presence(iter_section_bands(sec))
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
    """COR-001 offsets vs ER §7.10(e) (DO-5a…d / R-O5).

    Unsigned band-centre distance to curb vs ``c2_er_positions_m`` (water 1.5 /
    storm 2.5 / sewer 3.0). Finding when |distance − value| > ``c2_tolerance_m``
    (0.5). One finding per section × utility; storm_cb not compared. Report-only
    (DO-4): rule_id null. Route CRS to Parsons.
    """
    er_pos = params.get("c2_er_positions_m") or {}
    tol = float(params.get("c2_tolerance_m", 0.5))
    for sec in sections:
        label = sec.get("label") or "?"
        curb_ok = sec.get("curb_found")
        if curb_ok is None:
            curb_ok = sec.get("curb_left") is not None and sec.get("curb_right") is not None
        if not curb_ok:
            rows.append(comparison_row(
                "C2", f"offset:{label}", ["section"],
                {"section": label, "curb_left": sec.get("curb_left"),
                 "curb_right": sec.get("curb_right")},
                "m", None, None, "not_run",
                note="R2: curb not found — C2 not_run (offset_to_curb null)",
            ))
            continue

        # Per-band rows (keep signed offset for probe samples); storm_cb skipped for ER.
        by_util = defaultdict(list)
        for rec in iter_section_bands(sec):
            util = rec.get("utility")
            off = rec.get("offset_to_curb")
            unsigned = abs(float(off)) if off is not None else None
            er = er_pos.get(util) if util in er_pos else None
            if util == "storm_cb" or er is None:
                rows.append(comparison_row(
                    "C2", f"offset:{label}:{util}:{rec.get('side')}:{rec.get('index', 0)}",
                    ["section"],
                    {
                        "section": label, "utility": util,
                        "side": rec.get("side"), "index": rec.get("index", 0),
                        "offset_to_curb_m": off,
                        "unsigned_distance_m": unsigned,
                        "centre": rec.get("centre"),
                        "curb_left": sec.get("curb_left"),
                        "curb_right": sec.get("curb_right"),
                    },
                    "m", None, None, "information",
                    note=("storm_cb not compared to ER positions (DO-5)"
                          if util == "storm_cb"
                          else "no ER position for this utility (C2 compares water/storm/sewer)"),
                ))
                continue
            deviation = abs(unsigned - er) if unsigned is not None else None
            flag = deviation is not None and deviation > tol + 1e-9
            rows.append(comparison_row(
                "C2", f"offset:{label}:{util}:{rec.get('side')}:{rec.get('index', 0)}",
                ["section"],
                {
                    "section": label, "utility": util,
                    "side": rec.get("side"), "index": rec.get("index", 0),
                    "offset_to_curb_m": off,
                    "unsigned_distance_m": round(unsigned, 6) if unsigned is not None else None,
                    "er_m": er, "deviation_m": round(deviation, 6) if deviation is not None else None,
                    "tolerance_m": tol,
                    "centre": rec.get("centre"),
                    "curb_left": sec.get("curb_left"), "curb_right": sec.get("curb_right"),
                },
                "m", round(deviation, 6) if deviation is not None else None, None,
                "flag" if flag else ("pass" if deviation is not None else "information"),
                severity="Major" if flag else None,
                status="Probable" if flag else None,
                note="DO-5a: |unsigned offset − ER| vs c2_tolerance_m (R-O5)",
            ))
            if unsigned is not None:
                by_util[util].append({
                    "side": rec.get("side"), "index": rec.get("index", 0),
                    "offset_to_curb_m": off, "unsigned_m": unsigned,
                    "deviation_m": deviation, "er_m": er,
                })

        # One finding per section × ER utility (DO-5c); larger deviation wins.
        for util, samples in sorted(by_util.items()):
            er = er_pos[util]
            best = max(samples, key=lambda s: s["deviation_m"])
            if best["deviation_m"] <= tol + 1e-9:
                continue
            sides = sorted({s["side"] for s in samples if s.get("side")})
            side_bits = "; ".join(
                f"{s['side']} unsigned={s['unsigned_m']:.3f} m "
                f"(signed={s['offset_to_curb_m']}, Δ={s['deviation_m']:.3f} m)"
                for s in samples
            )
            statement = (
                f"Section {label}: '{util}' band centre is {best['unsigned_m']:.3f} m "
                f"(unsigned) from the curb vs ER allocation {er:.1f} m "
                f"(|Δ|={best['deviation_m']:.3f} m > {tol:.1f} m). "
                f"ER §7.10(e): \"{ER_C2_QUOTE}\" "
                f"Direction from the curb is not stated; comparison uses unsigned distance "
                f"(DO-5d / R-O5). Sides: {side_bits}. Route CRS to Parsons."
            )
            evidence = [
                evid("E01", "script_output",
                     f"section={label} utility={util} er={er} tol={tol} "
                     f"max_unsigned={best['unsigned_m']:.3f} max_dev={best['deviation_m']:.3f} "
                     f"sides={sides}"),
                evid("E02", "standard",
                     f"ER §7.10(e) p.57: {ER_C2_QUOTE} "
                     f"(R-O5; docs/reviews/S33-R-O5-ER-7.10e.md)"),
            ]
            if len(samples) > 1:
                evidence.append(evid(
                    "E03", "script_output",
                    f"both sides: {side_bits}",
                ))
            findings.append(finding_stub(
                "C2",
                f"{util} offset vs ER in section {label}",
                statement,
                "design_error", "Major", "Probable",
                evidence,
                meta["site"], meta["sub"], meta["run"],
                location={
                    "document_no": meta.get("doc") or "UTL-30000",
                    "section": label, "utility": util,
                    "route": "CRS", "recipient": "Parsons",
                },
                status_cap=status_cap,
                owner={"role": "Parsons", "name": None},
                source_doc="ER §7.10(e) / SPC-0006 Rev O §4.17",
                note=("DO-4 report-only (rule_id null); DO-5a…d / R-O5: unsigned ER "
                      "positions; CRS to Parsons."),
            ))


def run_c3(sections, params, rows, findings, meta, status_cap):
    """COR-001 side rule — dry not on the same side of CL as wet (excludes tree bands)."""
    wet_set, dry_set = params["wet"], params["dry"]
    for sec in sections:
        label = sec.get("label") or "?"
        cl = sec.get("cl_x") or 0.0
        wet_sides, dry_sides = set(), set()
        wet_on, dry_on = defaultdict(list), defaultdict(list)
        for rec in iter_section_bands(sec):
            if rec.get("exclude_c3_c4"):
                continue
            util = rec.get("utility")
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
    cc = abs(float(a["centre"]) - float(b["centre"]))
    if a["x_max"] < b["x_min"]:
        near = b["x_min"] - a["x_max"]
        far = b["x_max"] - a["x_min"]
    elif b["x_max"] < a["x_min"]:
        near = a["x_min"] - b["x_max"]
        far = a["x_max"] - b["x_min"]
    else:
        near = 0.0
        far = max(a["x_max"], b["x_max"]) - min(a["x_min"], b["x_min"])
    return cc, near, far


def run_c4(sections, params, rows, findings, meta, status_cap):
    """COR-002 horizontal — per-band pairs; tree bands excluded (R4)."""
    power = params["power"]
    min_m = params["c4_min_m"]
    for sec in sections:
        label = sec.get("label") or "?"
        bands = [b for b in iter_section_bands(sec) if not b.get("exclude_c3_c4")]
        power_bands = [b for b in bands if b.get("utility") in power or b.get("power")]
        if not power_bands:
            rows.append(comparison_row(
                "C4", f"spacing:{label}", ["section"],
                {"section": label}, None, None, None, "not_run",
                note="no power (MV/LV/HV) band in section",
            ))
            continue
        for prec in power_bands:
            pu = prec.get("utility")
            for orec in bands:
                if orec is prec:
                    continue
                ou = orec.get("utility")
                if ou == pu and orec.get("side") == prec.get("side") and \
                        orec.get("index") == prec.get("index"):
                    continue
                cc, near, far = _edge_spacing(prec, orec)
                nav = cc < min_m
                rows.append(comparison_row(
                    "C4",
                    f"spacing:{label}:{pu}:{prec.get('side')}:{prec.get('index', 0)}"
                    f":{ou}:{orec.get('side')}:{orec.get('index', 0)}",
                    ["section"],
                    {
                        "section": label, "power": pu, "other": ou,
                        "power_side": prec.get("side"), "other_side": orec.get("side"),
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
    samples = list(plan.get("samples") or [])
    if not samples and plan.get("networks"):
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
        bands = [b for b in iter_section_bands(sec) if b.get("utility") == util]
        band = bands[0] if bands else None
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


def read_plan_cut_labels_raw(dwg_path):
    """M3: section cut labels from raw TEXT / MTEXT / ATTRIB (not DXF)."""
    from ezdwg.raw import (
        decode_text_entities, decode_mtext_entities, decode_attrib_entities,
    )
    path = str(Path(dwg_path).resolve())
    labels = []
    counts = defaultdict(int)
    for row in decode_text_entities(path):
        lab = parse_section_label(row[1], strict=True)
        if lab:
            labels.append(lab)
            counts[lab] += 1
    for row in decode_mtext_entities(path):
        lab = parse_section_label(row[1], strict=True)
        if lab:
            labels.append(lab)
            counts[lab] += 1
    for row in decode_attrib_entities(path):
        lab = parse_section_label(row[1], strict=True)
        if lab:
            labels.append(lab)
            counts[lab] += 1
    return {"section_cuts": sorted(set(labels)), "cut_counts": dict(counts),
            "source": "raw_text_mtext_attrib", "path": path}


def run_c6(sections, plan, params, rows, findings, meta, status_cap):
    """Section vs plan — every section type cut on plan; every road covered; duplicates."""
    sec_labels = [s.get("label") for s in sections if s.get("label")]
    seen = {}
    dupes = []
    for lab in sec_labels:
        seen[lab] = seen.get(lab, 0) + 1
    for lab, n in seen.items():
        if n > 1:
            dupes.append(lab)

    plan_cuts = list((plan or {}).get("section_cuts") or (plan or {}).get("cuts") or [])
    plan_roads = list((plan or {}).get("roads") or [])
    cut_counts = (plan or {}).get("cut_counts") or {}
    covered = set(plan_cuts)
    missing_on_plan = [lab for lab in seen if lab not in covered] if plan_cuts else []
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
         "cut_counts": cut_counts,
         "missing_on_plan": missing_on_plan, "uncovered_roads": uncovered_roads,
         "duplicates": dupes,
         "plan_source": (plan or {}).get("source")},
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
    """One corridors.json row per section × band (R4)."""
    out = []
    for sec in sections:
        label = sec.get("label")
        for rec in iter_section_bands(sec):
            out.append({
                "section": label,
                "utility": rec.get("utility"),
                "side": rec.get("side"),
                "index": rec.get("index", 0),
                "x_min": rec.get("x_min"),
                "x_max": rec.get("x_max"),
                "centre": rec.get("centre"),
                "width": rec.get("width"),
                "offset_to_curb": rec.get("offset_to_curb"),
                "class": rec.get("class"),
                "power": rec.get("power"),
                "layer": rec.get("layer"),
                "handle": rec.get("handle"),
                "page": rec.get("page"),
                "cl_x": sec.get("cl_x"),
                "curb_left": sec.get("curb_left"),
                "curb_right": sec.get("curb_right"),
                "exclude_c3_c4": bool(rec.get("exclude_c3_c4")),
            })
    return out


def build_spacing_table(sections, params):
    power = params["power"]
    table = []
    for sec in sections:
        bands = [b for b in iter_section_bands(sec) if not b.get("exclude_c3_c4")]
        for prec in bands:
            if prec.get("utility") not in power and not prec.get("power"):
                continue
            for orec in bands:
                if orec is prec:
                    continue
                cc, near, far = _edge_spacing(prec, orec)
                table.append({
                    "section": sec.get("label"),
                    "power": prec.get("utility"), "other": orec.get("utility"),
                    "power_side": prec.get("side"), "other_side": orec.get("side"),
                    "centre_to_centre_m": round(cc, 6),
                    "near_edge_m": round(near, 6),
                    "max_edge_m": round(far, 6),
                })
    return table


def validate_schema(doc):
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        return
    schema = read_json(SCHEMA_PATH)
    Draft202012Validator(schema).validate(doc)


def main(argv=None):
    ap = argparse.ArgumentParser(description="C1–C6 corridor checks (skill 14)")
    ap.add_argument("--sections", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--plan", default=None, help="Plan JSON (section_cuts / samples)")
    ap.add_argument("--plan-dwg", default=None,
                    help="Corridor plan DWG — C6 cuts from raw TEXT/MTEXT/ATTRIB (M3)")
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
        if a.plan_dwg:
            if not Path(a.plan_dwg).is_file():
                raise ValueError(f"plan DWG not found: {a.plan_dwg}")
            raw_cuts = read_plan_cut_labels_raw(a.plan_dwg)
            if plan is None:
                plan = {}
            # Merge: raw cuts win for section_cuts when provided
            plan = {
                **plan,
                "section_cuts": raw_cuts["section_cuts"],
                "cut_counts": raw_cuts["cut_counts"],
                "source": raw_cuts["source"],
                "plan_dwg": raw_cuts["path"],
            }
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
                "rows": 0,
                "comparisons": len(rows),
                "findings": len(findings),
                "c2_flags": sum(1 for r in rows if r["check"] == "C2" and r["result"] == "flag"),
                "c3_flags": sum(1 for r in rows if r["check"] == "C3" and r["result"] == "flag"),
                "c1_missing": sum(1 for r in rows if r["check"] == "C1" and r["result"] == "flag"),
                "c2_findings": sum(
                    1 for f in findings if (f.get("reference") or {}).get("clause") == "C2"
                ),
            },
            "meta": {
                "marking": "report only (DO-4); C2 ER unsigned offsets (DO-5 / R-O5); "
                           "C4 Not auto-verifiable at PD (DO-6)",
                "site": a.site, "sub": a.sub, "run": meta["run"], "stage": a.stage,
                "route": sections_doc.get("route"),
                "status_cap": status_cap,
                "generated_at": _now(),
                "checker": f"check_corridors/{__version__}",
                "source": sections_doc.get("source"),
                "c2_er_positions_m": params.get("c2_er_positions_m"),
                "c2_tolerance_m": params.get("c2_tolerance_m"),
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
