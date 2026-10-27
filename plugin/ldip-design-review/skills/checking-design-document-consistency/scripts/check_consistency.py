#!/usr/bin/env python3
"""C1–C6 document consistency checks (skill 16, SPC-0006 Rev N §4.16).

Compares BOQ / report / skill-13 profile / drawing table / skill-10 linework.
DN-3: C4 and C6 read skill 13 / 10 outputs — never recompute geometry.
DN-4: report only — findings carry issue_type but **no rule_id**.
DN-5: tolerances from references/params.json.
DN-6: compare BOQ **base** before factor; record factor as stated.
DN-7: C7 (specs vs BOQ) not built.
C6: Information at 30 %; finding from 60 % (DN-5).

Usage:
  python check_consistency.py --boq <boq.json> --out <consistency.json>
      [--report <report_claims.json>] [--profile <earthworks_profile.json>]
      [--drawing-table <table.json>] [--road-register <register.json>]
      [--stage "30% Concept Design"] [--params <params.json>]
      [--site QRWN] [--sub <sub-id>] [--run <run-id>]

Exit: 0 done · 2 input · 4 schema. No network, no shell.
"""
__version__ = "0.1.0"

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cons_common import (  # noqa: E402
    EXIT_OK, EXIT_SCHEMA, atomic_write_json, emit, exceeds_quantity_tol, fail,
    load_params, main_guard, read_json, stage_pct,
)

SCHEMA_HINT = "templates/consistency.schema.json"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _boq_earth(boq, key):
    sm = (boq.get("summary") or {}).get("earthworks") or {}
    return sm.get(key)


def _boq_road(boq, key):
    sm = (boq.get("summary") or {}).get("roads") or {}
    return sm.get(key)


def _base_of(rec):
    if not rec:
        return None
    if rec.get("base") is not None:
        return float(rec["base"])
    if rec.get("quantity") is not None:
        return float(rec["quantity"])
    return None


def _unit_of(rec, default="m3"):
    return (rec or {}).get("unit") or default


def _profile_cut_fill(profile):
    cf = (profile or {}).get("cut_fill") or {}
    return cf.get("cut_volume_m3"), cf.get("fill_volume_m3")


def _profile_pavement(profile):
    """DN-3: use skill-13 road_top_minus_grading — do not recompute."""
    return (profile or {}).get("road_top_minus_grading") or (profile or {}).get("pavement_depth") or {}


def _drawing_cut_fill(table):
    if not table:
        return None, None
    # Accept {cut, fill} or {cut_m3, fill_m3} or nested quantities
    cut = table.get("cut", table.get("cut_m3", table.get("cut_volume_m3")))
    fill = table.get("fill", table.get("fill_m3", table.get("fill_volume_m3")))
    return cut, fill


def _road_model(register):
    """Skill 10 / 13 road quantities when present — never recompute (DN-3)."""
    if not register:
        return None, None
    roads = register.get("roads") or register.get("summary") or register
    area = roads.get("road_area_m2") or roads.get("road_surface_area_m2") or roads.get("area_m2")
    kerb = roads.get("kerb_length_m") or roads.get("curb_length_m") or roads.get("kerb_m")
    # linework totals
    lw = register.get("linework") or {}
    if kerb is None:
        kerb = lw.get("curb_length_m") or lw.get("kerb_length_m")
    if area is None:
        area = lw.get("road_area_m2")
    return area, kerb


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
        "result": result,  # pass | flag | observation | information | not_run
        "severity": severity,
        "proposed_status": status,
        "evidence": evidence or [],
        "note": note,
    }


def finding_stub(check, title, statement, issue_type, severity, status, evidence,
                 site, sub, run_id, location=None):
    """SPC-0003-shaped candidate — report only; reference.rule_id is always null (DN-4)."""
    agent = "RDE"
    fid_run = run_id or f"{site or 'QRWN'}-SYN-19700101T0000"
    n = abs(hash(f"{check}:{title}")) % 1000
    return {
        "schema_version": "1.0",
        "finding_id": f"{fid_run}/{agent}-{n:03d}",
        "stage": "candidate",
        "run_id": fid_run,
        "site": site or "QRWN",
        "sub_id": sub or "SYN",
        "agent": agent,
        "agent_versions": {"checking-design-document-consistency": __version__},
        "title": title[:120],
        "statement": statement[:1500],
        "issue_type": issue_type,
        "location": location or {"document_no": "BOQ", "package_level": True},
        "severity": severity,
        "confidence": "Medium",
        "evidence": evidence,
        "reference": {
            "rule_id": None,
            "source_doc": "SPC-0006 Rev N §4.16",
            "clause": check,
            "rulebook_version": None,
        },
        "proposed_status": status,
        "owner": {"role": "DMR", "name": None},
        "created_at": _now(),
        "report_only": True,
        "note": "DN-4: report only until a consistency rule group exists in rules_v9.",
    }


def evid(eid, source_type, extract, tier="deterministic", **extra):
    e = {
        "evidence_id": eid,
        "tier": tier,
        "source_type": source_type,
        "extract": extract,
    }
    e.update(extra)
    return e


def run_c1(boq, profile, drawing, params, rows, findings, meta):
    cut_b = _boq_earth(boq, "cut")
    fill_b = _boq_earth(boq, "fill")
    model_cut, model_fill = _profile_cut_fill(profile)
    draw_cut, draw_fill = _drawing_cut_fill(drawing)

    pairs = [
        ("cut", _base_of(cut_b), model_cut, draw_cut, _unit_of(cut_b, "m3"), cut_b),
        ("fill", _base_of(fill_b), model_fill, draw_fill, _unit_of(fill_b, "m3"), fill_b),
    ]
    for name, boq_v, model_v, draw_v, unit, rec in pairs:
        sources = ["boq"]
        values = {"boq_base": boq_v}
        if rec and rec.get("factor") is not None:
            values["boq_factor"] = rec["factor"]
            values["boq_stated"] = rec.get("quantity")
        if model_v is not None:
            sources.append("model")
            values["model"] = model_v
        if draw_v is not None:
            sources.append("drawing_table")
            values["drawing_table"] = draw_v

        if boq_v is None and model_v is None and draw_v is None:
            rows.append(comparison_row(
                "C1", name, sources, values, unit, None, None, "not_run",
                note="no cut/fill values present"))
            continue

        flagged = False
        # Compare BOQ base vs model (primary); also vs drawing when both present
        for other_name, other_v in (("model", model_v), ("drawing_table", draw_v)):
            if boq_v is None or other_v is None:
                continue
            flag, diff, pd = exceeds_quantity_tol(boq_v, other_v, unit, params)
            result = "flag" if flag else "pass"
            rows.append(comparison_row(
                "C1", f"{name}_boq_vs_{other_name}",
                ["boq", other_name], {"boq_base": boq_v, other_name: other_v,
                                      "boq_factor": (rec or {}).get("factor")},
                unit, diff, pd, result,
                severity="Minor" if flag else None,
                status="Probable" if flag else None,
                evidence=[
                    evid("E01", "xlsx_cell", f"BOQ {name} base={boq_v}",
                         measured_value=boq_v, unit=unit,
                         artifact_path=(rec or {}).get("address")),
                    evid("E02", "script_output", f"{other_name}={other_v}",
                         measured_value=other_v, unit=unit),
                ],
                note="DN-6: BOQ base before factor",
            ))
            if flag:
                flagged = True
                findings.append(finding_stub(
                    "C1",
                    f"BOQ {name} disagrees with {other_name}",
                    f"BOQ {name} base {boq_v:g} {unit} differs from {other_name} "
                    f"{other_v:g} {unit} beyond the starting tolerance "
                    f"(>{params['qty_pct']*100:.0f}% and >{params['qty_abs_m3']:g} m³).",
                    "inconsistency", "Minor", "Probable",
                    [
                        evid("E01", "xlsx_cell", f"base={boq_v}", measured_value=boq_v, unit=unit),
                        evid("E02", "script_output", f"{other_name}={other_v}",
                             measured_value=other_v, unit=unit),
                    ],
                    meta["site"], meta["sub"], meta["run"],
                    location={"document_no": "BOQ",
                              "table": {"sheet": (rec or {}).get("sheet") or "BOQ",
                                        "cells": [((rec or {}).get("cell") or "A1")]}},
                ))
        if model_v is not None and draw_v is not None and boq_v is None:
            flag, diff, pd = exceeds_quantity_tol(model_v, draw_v, unit, params)
            rows.append(comparison_row(
                "C1", f"{name}_model_vs_drawing", ["model", "drawing_table"],
                {"model": model_v, "drawing_table": draw_v}, unit, diff, pd,
                "flag" if flag else "pass",
                severity="Minor" if flag else None, status="Probable" if flag else None,
            ))
        if not flagged and boq_v is not None and model_v is None and draw_v is None:
            rows.append(comparison_row(
                "C1", name, ["boq"], {"boq_base": boq_v}, unit, None, None, "information",
                note="BOQ only — model / drawing table absent",
            ))


def run_c2(boq, report, rows, findings, meta):
    ratios = (boq.get("summary") or {}).get("ratios_recorded") or []
    formulas = (boq.get("summary") or {}).get("base_factor_formulas") or []
    families = set((report or {}).get("families_present") or [])
    claims = (report or {}).get("claims") or []
    basis_stated = "basis_ratio" in families or any(c.get("family") == "basis_ratio" for c in claims)

    if not ratios and not formulas:
        rows.append(comparison_row(
            "C2", "quantity_basis", ["boq"], {}, None, None, None, "not_run",
            note="no base×factor or split ratios in BOQ"))
        return

    values = {"ratios": ratios, "base_factor_count": len(formulas), "basis_stated_in_report": basis_stated}
    if formulas:
        values["example_factor"] = formulas[0].get("factor")
        values["example_base"] = formulas[0].get("base")
    if ratios and not basis_stated:
        rows.append(comparison_row(
            "C2", "split_ratios", ["boq", "report"], values, None, None, None, "observation",
            severity="Observation", status=None,
            note="basis not stated",
            evidence=[evid("E01", "xlsx_cell", f"ratios={ratios}")],
        ))
        findings.append(finding_stub(
            "C2", "BOQ quantity basis not stated in the report",
            f"BOQ records constant split ratios {ratios} (and/or base×factor formulas) "
            f"that are not stated in the design / concept report.",
            "inconsistency", "Observation", "Probable",
            [evid("E01", "xlsx_cell", f"ratios={ratios}")],
            meta["site"], meta["sub"], meta["run"],
        ))
    elif formulas and not basis_stated:
        rows.append(comparison_row(
            "C2", "base_factor", ["boq", "report"], values, None, None, None, "observation",
            severity="Observation", note="basis not stated (factor present, not explained)",
        ))
        findings.append(finding_stub(
            "C2", "BOQ base×factor basis not stated in the report",
            f"BOQ uses base×factor (example factor {values.get('example_factor')}) "
            f"without a matching statement in the report.",
            "inconsistency", "Observation", "Probable",
            [evid("E01", "xlsx_cell", f"factor={values.get('example_factor')}")],
            meta["site"], meta["sub"], meta["run"],
        ))
    else:
        rows.append(comparison_row(
            "C2", "quantity_basis", ["boq", "report"], values, None, None, None, "pass",
            note="ratios/factor recorded; basis present in report" if basis_stated else "recorded",
        ))


def run_c3(boq, report, rows, findings, meta):
    families = set((report or {}).get("families_present") or [])
    claims = (report or {}).get("claims") or []
    disposal = _base_of(_boq_earth(boq, "disposal"))
    imp = _base_of(_boq_earth(boq, "import"))
    balanced = "balanced" in families or any(c.get("family") == "balanced" for c in claims)

    if not balanced and disposal is None and imp is None:
        rows.append(comparison_row(
            "C3", "report_vs_quantities", ["report", "boq"], {}, None, None, None, "not_run",
            note="no balance/import/export claim and no disposal/import quantities"))
        return

    values = {"balanced_claim": balanced, "disposal_base": disposal, "import_base": imp}
    # "balanced" claim conflicts with non-trivial disposal or import
    conflict = balanced and (
        (disposal is not None and abs(disposal) > 1.0) or
        (imp is not None and abs(imp) > 1.0)
    )
    if conflict:
        page = next((c.get("page") for c in claims if c.get("family") == "balanced"), None)
        rows.append(comparison_row(
            "C3", "balanced_vs_disposal_import", ["report", "boq"], values, "m3",
            None, None, "observation", severity="Observation",
            evidence=[
                evid("E01", "pdf_vector_text", "balanced", tier="extracted_text_crop",
                     artifact_path=f"page:{page}" if page else None),
                evid("E02", "xlsx_cell", f"disposal={disposal}; import={imp}"),
            ],
            note="report claims balanced but BOQ has disposal/import",
        ))
        findings.append(finding_stub(
            "C3", "Report claims balanced earthworks but BOQ shows disposal/import",
            f"The report states earthworks are balanced, yet the BOQ records disposal "
            f"base={disposal} and import base={imp}.",
            "inconsistency", "Observation", "Probable",
            [
                evid("E01", "pdf_vector_text", "balanced", tier="extracted_text_crop",
                     artifact_path=f"page:{page}" if page else "page:1"),
                evid("E02", "xlsx_cell", f"disposal={disposal}; import={imp}"),
            ],
            meta["site"], meta["sub"], meta["run"],
            location={"document_no": "DESIGN-REPORT", "pdf": {"page": int(page or 1)}},
        ))
    else:
        rows.append(comparison_row(
            "C3", "report_vs_quantities", ["report", "boq"], values, "m3", None, None,
            "pass" if balanced else "information",
        ))


def run_c4(report, profile, params, rows, findings, meta):
    """Pavement depth claim vs skill-13 road_top − ToS stats (DN-3, no recompute)."""
    claims = (report or {}).get("claims") or []
    depth_claims = [c for c in claims if c.get("family") == "pavement_depth" and c.get("value_m") is not None]
    stats = _profile_pavement(profile)
    median = stats.get("median")
    p5 = stats.get("p5") or stats.get("dz_p5")
    p95 = stats.get("p95") or stats.get("dz_p95")

    if not depth_claims and median is None:
        rows.append(comparison_row(
            "C4", "pavement_depth", ["report", "model"], {}, "m", None, None, "not_run",
            note="no pavement-depth claim and no skill-13 road_top_minus_grading"))
        return

    stated_m = depth_claims[0]["value_m"] if depth_claims else None
    values = {
        "stated_m": stated_m,
        "stated_mm": depth_claims[0].get("value_mm") if depth_claims else None,
        "model_median_m": median,
        "model_p5_m": p5,
        "model_p95_m": p95,
        "n": stats.get("n"),
    }
    if stated_m is None or median is None:
        rows.append(comparison_row(
            "C4", "pavement_depth", ["report", "model"], values, "m", None, None, "information",
            note="claim or model depth missing",
        ))
        return

    diff = abs(float(median) - float(stated_m))
    flag = diff > params["c4_abs_m"]
    rows.append(comparison_row(
        "C4", "pavement_depth", ["report", "model"], values, "m", diff, None,
        "flag" if flag else "pass",
        severity="Minor" if flag else None,
        status="Probable" if flag else None,
        evidence=[
            evid("E01", "pdf_vector_text", depth_claims[0].get("extract") or f"{stated_m} m",
                 tier="extracted_text_crop", artifact_path=f"page:{depth_claims[0].get('page')}",
                 measured_value=stated_m, unit="m"),
            evid("E02", "script_output", f"road_top_minus_grading.median={median}",
                 measured_value=median, unit="m"),
        ],
        note="DN-3: median from skill 13 profile; not recomputed",
    ))
    if flag:
        findings.append(finding_stub(
            "C4", "Stated pavement depth disagrees with model",
            f"Report pavement depth {stated_m:g} m vs model median "
            f"road-top−ToS {median:g} m (Δ={diff:g} m > {params['c4_abs_m']} m).",
            "inconsistency", "Minor", "Probable",
            [
                evid("E01", "pdf_vector_text", f"stated={stated_m} m",
                     tier="extracted_text_crop",
                     artifact_path=f"page:{depth_claims[0].get('page')}", measured_value=stated_m, unit="m"),
                evid("E02", "script_output", f"median={median}", measured_value=median, unit="m"),
            ],
            meta["site"], meta["sub"], meta["run"],
            location={"document_no": "DESIGN-REPORT",
                      "pdf": {"page": int(depth_claims[0].get("page") or 1)}},
        ))


def run_c5(boq, rows, findings, meta):
    errors = (boq.get("summary") or {}).get("cell_errors") or []
    # Also collect item-level errors
    for sh in boq.get("sheets") or []:
        for it in sh.get("items") or []:
            if it.get("error"):
                errors.append({
                    "sheet": it.get("sheet"), "cell": it.get("cell"),
                    "address": it.get("address"), "value": it["error"], "kind": "item_error",
                })
    # de-dupe by address
    seen = set()
    uniq = []
    for e in errors:
        key = e.get("address") or f"{e.get('sheet')}!{e.get('cell')}"
        if key in seen:
            continue
        seen.add(key)
        uniq.append(e)

    if not uniq:
        # Part arithmetic: suitable fill ≈ excavation − disposal when all three present
        cut = _base_of(_boq_earth(boq, "cut"))
        fill = _base_of(_boq_earth(boq, "fill"))
        disposal = _base_of(_boq_earth(boq, "disposal"))
        if cut is not None and fill is not None and disposal is not None:
            expected = cut - disposal
            diff = abs(expected - fill)
            # soft check — observation band only when far off (>10% of cut)
            pd = (diff / abs(cut)) if cut else None
            result = "flag" if pd is not None and pd > 0.10 else "pass"
            rows.append(comparison_row(
                "C5", "part_arithmetic_fill", ["boq"],
                {"cut": cut, "disposal": disposal, "fill": fill, "expected_fill": expected},
                "m3", diff, pd, result,
                severity="Minor" if result == "flag" else None,
                status="Probable" if result == "flag" else None,
                note="suitable fill = excavation − disposal",
            ))
            if result == "flag":
                findings.append(finding_stub(
                    "C5", "BOQ part arithmetic: fill ≠ excavation − disposal",
                    f"Fill base {fill:g} differs from excavation {cut:g} − disposal "
                    f"{disposal:g} = {expected:g}.",
                    "inconsistency", "Minor", "Probable",
                    [evid("E01", "xlsx_cell", f"cut={cut}; disposal={disposal}; fill={fill}")],
                    meta["site"], meta["sub"], meta["run"],
                ))
        else:
            rows.append(comparison_row(
                "C5", "internal_integrity", ["boq"], {"cell_errors": 0}, None, None, None, "pass",
                note="no #REF! / error cells",
            ))
        return

    for e in uniq:
        addr = e.get("address") or f"{e.get('sheet')}!{e.get('cell')}"
        rows.append(comparison_row(
            "C5", "cell_error", ["boq"],
            {"address": addr, "value": e.get("value")}, None, None, None, "flag",
            severity="Minor", status="Confirmed",
            evidence=[evid("E01", "xlsx_cell", f"{addr}={e.get('value')}")],
            note="Confirmed allowed for xlsx_cell errors (SPC-0003 evidence tier)",
        ))
        findings.append(finding_stub(
            "C5", f"BOQ cell error at {addr}",
            f"BOQ cell {addr} contains {e.get('value')} (principal-quantities / item error).",
            "inconsistency", "Minor", "Confirmed",
            [evid("E01", "xlsx_cell", f"{addr}={e.get('value')}")],
            meta["site"], meta["sub"], meta["run"],
            location={"document_no": "BOQ",
                      "table": {"sheet": e.get("sheet") or "BOQ",
                                "cells": [e.get("cell") or "A1"]}},
        ))


def run_c6(boq, register, params, stage, rows, findings, meta):
    road = _boq_road(boq, "road_area")
    kerb = _boq_road(boq, "kerb")
    model_area, model_kerb = _road_model(register)
    pct = stage_pct(stage)
    finding_ok = pct is not None and pct >= params["c6_finding_from_pct"]

    pairs = [
        ("road_area", _base_of(road), model_area, _unit_of(road, "m2"), road),
        ("kerb_length", _base_of(kerb), model_kerb, _unit_of(kerb, "m"), kerb),
    ]
    any_data = False
    for name, boq_v, model_v, unit, rec in pairs:
        if boq_v is None and model_v is None:
            continue
        any_data = True
        values = {"boq_base": boq_v, "model": model_v, "boq_factor": (rec or {}).get("factor")}
        if boq_v is None or model_v is None:
            rows.append(comparison_row(
                "C6", name, ["boq", "model"], values, unit, None, None, "information",
                note="one side missing",
            ))
            continue
        flag, diff, pd = exceeds_quantity_tol(boq_v, model_v, unit, params)
        if not finding_ok:
            rows.append(comparison_row(
                "C6", name, ["boq", "model"], values, unit, diff, pd, "information",
                note=f"Information at {pct or 30}% — finding from {params['c6_finding_from_pct']}%",
            ))
        else:
            rows.append(comparison_row(
                "C6", name, ["boq", "model"], values, unit, diff, pd,
                "flag" if flag else "pass",
                severity="Minor" if flag else None,
                status="Probable" if flag else None,
            ))
            if flag:
                findings.append(finding_stub(
                    "C6", f"BOQ {name} disagrees with model / linework",
                    f"BOQ {name} base {boq_v:g} {unit} vs model {model_v:g} {unit} "
                    f"beyond tolerance at stage {stage}.",
                    "inconsistency", "Minor", "Probable",
                    [
                        evid("E01", "xlsx_cell", f"base={boq_v}", measured_value=boq_v, unit=unit),
                        evid("E02", "script_output", f"model={model_v}", measured_value=model_v, unit=unit),
                    ],
                    meta["site"], meta["sub"], meta["run"],
                ))
    if not any_data:
        rows.append(comparison_row(
            "C6", "road_boq_vs_model", ["boq", "model"], {}, None, None, None, "not_run",
            note="no road area / kerb quantities",
        ))


def validate_minimal(doc):
    required = ("consistency_version", "comparisons", "findings", "counts", "meta")
    for k in required:
        if k not in doc:
            return f"missing {k}"
    for row in doc["comparisons"]:
        if "check" not in row or "result" not in row:
            return "comparison row missing check/result"
    for f in doc["findings"]:
        if f.get("reference", {}).get("rule_id") is not None:
            return "DN-4 violated: finding has rule_id"
        if f.get("issue_type") not in ("inconsistency", "missing_information", "package_control",
                                       "design_error", "er_ambiguity", "schedule_blocking"):
            return f"bad issue_type {f.get('issue_type')}"
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--boq", required=True, help="boq.json from read_boq.py")
    ap.add_argument("--report", default=None, help="report_claims.json from read_report.py")
    ap.add_argument("--profile", default=None, help="skill 13 earthworks_profile.json")
    ap.add_argument("--drawing-table", default=None, dest="drawing_table")
    ap.add_argument("--road-register", default=None, dest="road_register",
                    help="skill 10 register / linework quantities JSON")
    ap.add_argument("--stage", default="30% Concept Design")
    ap.add_argument("--params", default=None)
    ap.add_argument("--site", default="QRWN")
    ap.add_argument("--sub", default="SYN")
    ap.add_argument("--run", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        params = load_params(a.params)
        boq = read_json(a.boq)
        report = read_json(a.report) if a.report else None
        profile = read_json(a.profile) if a.profile else None
        drawing = read_json(a.drawing_table) if a.drawing_table else None
        register = read_json(a.road_register) if a.road_register else None
        meta = {
            "site": a.site,
            "sub": a.sub,
            "run": a.run or f"{a.site}-{a.sub}-19700101T0000",
            "stage": a.stage,
            "stage_pct": stage_pct(a.stage),
            "params": {
                "qty_pct": params["qty_pct"],
                "qty_abs_m3": params["qty_abs_m3"],
                "c4_abs_m": params["c4_abs_m"],
                "c6_finding_from_pct": params["c6_finding_from_pct"],
            },
            "tool": {"script": "check_consistency.py", "version": __version__},
            "decisions": ["DN-3", "DN-4", "DN-5", "DN-6", "DN-7"],
            "marking": (
                "Report only (DN-4) — no consistency rule_id until rules_v9; "
                "issue_type inconsistency / Observation per SPC-0003."
            ),
        }
        rows, findings = [], []
        run_c1(boq, profile, drawing, params, rows, findings, meta)
        run_c2(boq, report, rows, findings, meta)
        run_c3(boq, report, rows, findings, meta)
        run_c4(report, profile, params, rows, findings, meta)
        run_c5(boq, rows, findings, meta)
        run_c6(boq, register, params, a.stage, rows, findings, meta)

        counts = {
            "comparisons": len(rows),
            "findings": len(findings),
            "flag": sum(1 for r in rows if r["result"] == "flag"),
            "observation": sum(1 for r in rows if r["result"] == "observation"),
            "information": sum(1 for r in rows if r["result"] == "information"),
            "pass": sum(1 for r in rows if r["result"] == "pass"),
            "not_run": sum(1 for r in rows if r["result"] == "not_run"),
            "by_check": {},
        }
        for r in rows:
            counts["by_check"].setdefault(r["check"], 0)
            counts["by_check"][r["check"]] += 1

        doc = {
            "consistency_version": "1.0",
            "comparisons": rows,
            "findings": findings,
            "counts": counts,
            "meta": meta,
            "c7": {"built": False, "note": "DN-7 — specifications vs BOQ not built"},
        }
        err = validate_minimal(doc)
        if err:
            return fail(EXIT_SCHEMA, err)
        atomic_write_json(a.out, doc)
        emit({
            "status": "ok",
            "out": a.out,
            "comparisons": counts["comparisons"],
            "findings": counts["findings"],
            "flag": counts["flag"],
            "observation": counts["observation"],
            "information": counts["information"],
            "report_only": True,
        })
        return EXIT_OK

    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
