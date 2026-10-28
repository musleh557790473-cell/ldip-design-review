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


def _stated_of(rec):
    """Stated quantity (after factor) for same-basis arithmetic (R12)."""
    if not rec:
        return None
    if rec.get("quantity") is not None:
        return float(rec["quantity"])
    if rec.get("base") is not None:
        return float(rec["base"]) * float(rec.get("factor") or 1.0)
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
    """Skill 10 / 13 road quantities when present — never recompute (DN-3).

    C6: refuse BOQ-vs-itself — register must be an independent model / linework source.
    """
    if not register:
        return None, None, "absent"
    src = str(register.get("source") or register.get("origin") or "").strip().lower()
    # Explicit BOQ / self sources are rejected
    if src in ("boq", "read_boq", "boq.json", "self", "same"):
        return None, None, "self"
    roads = register.get("roads") or register.get("summary") or {}
    if not isinstance(roads, dict):
        roads = {}
    # Flat register without a roads wrapper still ok when source is independent
    if not roads and any(k in register for k in (
            "road_area_m2", "road_surface_area_m2", "kerb_length_m", "curb_length_m")):
        roads = register
    area = roads.get("road_area_m2") or roads.get("road_surface_area_m2") or roads.get("area_m2")
    kerb = roads.get("kerb_length_m") or roads.get("curb_length_m") or roads.get("kerb_m")
    lw = register.get("linework") or {}
    if kerb is None:
        kerb = lw.get("curb_length_m") or lw.get("kerb_length_m")
    if area is None:
        area = lw.get("road_area_m2")
    # Without an explicit independent source and without linework, treat as self-risk
    independent = src in ("skill10", "skill_10", "linework", "model", "road_register",
                          "register", "earthworks_profile", "skill13") or bool(lw)
    if (area is not None or kerb is not None) and not independent:
        # Still allow when caller set independent:true
        if not register.get("independent"):
            return area, kerb, "unproven"
    return area, kerb, "ok"


def _doc_no(meta, kind="boq"):
    """R8: prefer real document_no from inputs; never leave findings as bare labels only."""
    if kind == "boq":
        return meta.get("boq_document_no") or meta.get("document_no") or "BOQ"
    if kind == "report":
        return meta.get("report_document_no") or meta.get("document_no") or "DESIGN-REPORT"
    return meta.get("document_no") or kind


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

        def _pair(left_name, left_v, right_name, right_v, qty_label, note=None):
            nonlocal flagged
            if left_v is None or right_v is None:
                return
            flag, diff, pd = exceeds_quantity_tol(left_v, right_v, unit, params)
            result = "flag" if flag else "pass"
            vals = {left_name: left_v, right_name: right_v}
            if left_name == "boq_base" and rec is not None:
                vals["boq_factor"] = rec.get("factor")
            rows.append(comparison_row(
                "C1", qty_label, [left_name.replace("_base", ""), right_name],
                vals, unit, diff, pd, result,
                severity="Minor" if flag else None,
                status="Probable" if flag else None,
                evidence=[
                    evid("E01", "xlsx_cell" if "boq" in left_name else "script_output",
                         f"{left_name}={left_v}", measured_value=left_v, unit=unit,
                         artifact_path=(rec or {}).get("address") if "boq" in left_name else None),
                    evid("E02", "script_output", f"{right_name}={right_v}",
                         measured_value=right_v, unit=unit),
                ],
                note=note or "DN-6: BOQ base before factor" if "boq" in left_name else "R7 three-way",
            ))
            if flag:
                flagged = True
                findings.append(finding_stub(
                    "C1",
                    f"{name}: {left_name} disagrees with {right_name}",
                    f"{name} {left_name} {left_v:g} {unit} differs from {right_name} "
                    f"{right_v:g} {unit} beyond the starting tolerance "
                    f"(>{params['qty_pct']*100:.0f}% and >{params['qty_abs_m3']:g} m³).",
                    "inconsistency", "Minor", "Probable",
                    [
                        evid("E01", "xlsx_cell" if "boq" in left_name else "script_output",
                             f"{left_name}={left_v}", measured_value=left_v, unit=unit),
                        evid("E02", "script_output", f"{right_name}={right_v}",
                             measured_value=right_v, unit=unit),
                    ],
                    meta["site"], meta["sub"], meta["run"],
                    location={"document_no": _doc_no(meta, "boq"),
                              "table": {"sheet": (rec or {}).get("sheet") or "BOQ",
                                        "cells": [((rec or {}).get("cell") or "A1")]}},
                ))

        # R7: always emit the three pairs when both sides exist
        _pair("boq_base", boq_v, "model", model_v, f"{name}_boq_vs_model")
        _pair("boq_base", boq_v, "drawing_table", draw_v, f"{name}_boq_vs_drawing")
        _pair("model", model_v, "drawing_table", draw_v, f"{name}_model_vs_drawing",
              note="R7: model vs drawing table")
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
    # R9: balanced only when tagged in the earthworks section (or families_present already filtered)
    bal_claims = [
        c for c in claims
        if c.get("family") == "balanced" and (
            c.get("in_earthworks_section") is True or c.get("section") == "earthworks")
    ]
    balanced = bool(bal_claims) or ("balanced" in families and not any(
        c.get("family") == "balanced" and c.get("in_earthworks_section") is False for c in claims))

    if not balanced and disposal is None and imp is None:
        rows.append(comparison_row(
            "C3", "report_vs_quantities", ["report", "boq"], {}, None, None, None, "not_run",
            note="no earthworks balance/import/export claim and no disposal/import quantities"))
        return

    values = {"balanced_claim": balanced, "disposal_base": disposal, "import_base": imp,
              "balanced_section": "earthworks" if bal_claims else None}
    conflict = balanced and (
        (disposal is not None and abs(disposal) > 1.0) or
        (imp is not None and abs(imp) > 1.0)
    )
    if conflict:
        page = (bal_claims[0].get("page") if bal_claims else None) or next(
            (c.get("page") for c in claims if c.get("family") == "balanced"), None)
        rows.append(comparison_row(
            "C3", "balanced_vs_disposal_import", ["report", "boq"], values, "m3",
            None, None, "observation", severity="Observation",
            evidence=[
                evid("E01", "pdf_vector_text", "balanced", tier="extracted_text_crop",
                     artifact_path=f"page:{page}" if page else "page:1"),
                evid("E02", "xlsx_cell", f"disposal={disposal}; import={imp}"),
            ],
            note="report claims balanced (earthworks) but BOQ has disposal/import",
        ))
        findings.append(finding_stub(
            "C3", "Report claims balanced earthworks but BOQ shows disposal/import",
            f"The earthworks section states quantities are balanced, yet the BOQ records "
            f"disposal base={disposal} and import base={imp}.",
            "inconsistency", "Observation", "Probable",
            [
                evid("E01", "pdf_vector_text", "balanced", tier="extracted_text_crop",
                     artifact_path=f"page:{page}" if page else "page:1"),
                evid("E02", "xlsx_cell", f"disposal={disposal}; import={imp}"),
            ],
            meta["site"], meta["sub"], meta["run"],
            location={"document_no": _doc_no(meta, "report"), "pdf": {"page": int(page or 1)}},
        ))
    else:
        rows.append(comparison_row(
            "C3", "report_vs_quantities", ["report", "boq"], values, "m3", None, None,
            "pass" if balanced else "information",
        ))


def _pick_pavement_depth_claim(claims):
    """R10: prefer pavement-section / pavement+subgrade 550 mm; not stormwater 500."""
    depth_claims = [c for c in claims
                    if c.get("family") == "pavement_depth" and c.get("value_m") is not None]
    if not depth_claims:
        return None
    in_pav = [c for c in depth_claims if c.get("section") == "pavement"]
    pool = in_pav or depth_claims
    both = [c for c in pool if c.get("pavement_subgrade")]
    if both:
        pool = both
    # Prefer 550 mm (wearing → ToS) when several depths remain
    prefer_550 = [c for c in pool if abs(float(c.get("value_mm") or 0) - 550.0) < 1e-6]
    if prefer_550:
        return prefer_550[0]
    return pool[0]


def run_c4(report, profile, params, rows, findings, meta):
    """Pavement depth claim vs skill-13 road_top − ToS stats (DN-3, no recompute)."""
    claims = (report or {}).get("claims") or []
    depth_claim = _pick_pavement_depth_claim(claims)
    depth_claims = [depth_claim] if depth_claim else []
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
            location={"document_no": _doc_no(meta, "report"),
                      "pdf": {"page": int(depth_claims[0].get("page") or 1)}},
        ))


def run_c5(boq, rows, findings, meta):
    """R3b: handful of grouped findings — roads/earthworks + A-List only (DN-1).

    One finding per pattern (e.g. "A-List: 33 principal quantities are #REF!").
    Other sheets → information row with count only.
    """
    groups = list((boq.get("summary") or {}).get("error_groups") or [])
    # Fallback if older boq.json without error_groups
    if not groups:
        errors = list((boq.get("summary") or {}).get("cell_errors") or [])
        alist = [e for e in errors if "list" in (e.get("sheet") or "").lower()
                 or "principal" in (e.get("sheet") or "").lower()
                 or e.get("sheet_kind") == "alist"]
        ew = [e for e in errors if e.get("sheet_kind") == "earthworks"
              or "earthwork" in (e.get("sheet") or "").lower()]
        rd = [e for e in errors if e.get("sheet_kind") == "roads"
              or "road" in (e.get("sheet") or "").lower()]
        other_n = len(errors) - len(alist) - len(ew) - len(rd)
        if alist:
            groups.append({"group": "alist", "label": "A-List", "count": len(alist),
                           "cells": [e.get("address") for e in alist[:50]], "in_scope": True,
                           "pattern": f"A-List: {len(alist)} principal quantities are #REF!"})
        if ew:
            groups.append({"group": "earthworks", "label": "earthworks part", "count": len(ew),
                           "cells": [e.get("address") for e in ew[:50]], "in_scope": True,
                           "pattern": f"earthworks part: {len(ew)} cells are #REF!"})
        if rd:
            groups.append({"group": "roads", "label": "roads part", "count": len(rd),
                           "cells": [e.get("address") for e in rd[:50]], "in_scope": True,
                           "pattern": f"roads part: {len(rd)} cells are #REF!"})
        if other_n > 0:
            groups.append({"group": "other", "label": "other sheets", "count": other_n,
                           "cells": [], "in_scope": False,
                           "pattern": f"other sheets: {other_n} #REF! cells (information only)"})

    in_scope_findings = 0
    for g in groups:
        cells = g.get("cells") or []
        if g.get("in_scope"):
            in_scope_findings += 1
            rows.append(comparison_row(
                "C5", "cell_error_group", ["boq"],
                {"group": g.get("group"), "count": g.get("count"), "pattern": g.get("pattern"),
                 "cells": cells[:20]}, None, None, None, "flag",
                severity="Minor", status="Confirmed",
                evidence=[evid(f"E{i+1:02d}", "xlsx_cell", c) for i, c in enumerate(cells[:10])],
                note="R3b: one finding per pattern; roads/earthworks + A-List only (DN-1)",
            ))
            findings.append(finding_stub(
                "C5", (g.get("pattern") or f"{g.get('label')}: #REF!")[:120],
                f"{g.get('pattern')} Cells (evidence): {', '.join(cells[:15])}"
                + ("…" if len(cells) > 15 else "") + ".",
                "inconsistency", "Minor", "Confirmed",
                [evid(f"E{i+1:02d}", "xlsx_cell", c) for i, c in enumerate(cells[:10])]
                or [evid("E01", "xlsx_cell", g.get("pattern") or "#REF!")],
                meta["site"], meta["sub"], meta["run"],
                location={"document_no": _doc_no(meta, "boq"),
                          "table": {"sheet": g.get("label") or "BOQ",
                                    "cells": [c.split("!")[-1] if c and "!" in c else (c or "A1")
                                              for c in cells[:5]] or ["A1"]}},
            ))
        else:
            rows.append(comparison_row(
                "C5", "cell_error_other", ["boq"],
                {"group": g.get("group"), "count": g.get("count"), "pattern": g.get("pattern")},
                None, None, None, "information",
                note="R3b: other-discipline #REF! counted only (DN-1 scope)",
            ))

    # R3: principal-quantity SUMIF/SUM vs part-item totals (grouped, not per cell)
    flagged_pc = [pc for pc in (boq.get("summary") or {}).get("principal_checks") or []
                  if pc.get("result") == "flag"]
    if flagged_pc:
        cells = [pc.get("address") for pc in flagged_pc]
        rows.append(comparison_row(
            "C5", "principal_vs_parts", ["boq"],
            {"count": len(flagged_pc), "cells": cells}, "m3", None, None, "flag",
            severity="Minor", status="Probable",
            note="R3: principal quantity link ≠ sum of part items",
        ))
        findings.append(finding_stub(
            "C5", f"Principal quantities disagree with part items ({len(flagged_pc)})",
            f"{len(flagged_pc)} principal SUMIF/SUM link(s) disagree with earthworks/roads "
            f"part totals. Cells: {', '.join(c for c in cells if c)}.",
            "inconsistency", "Minor", "Probable",
            [evid(f"E{i+1:02d}", "xlsx_cell", c) for i, c in enumerate(cells[:10])],
            meta["site"], meta["sub"], meta["run"],
            location={"document_no": _doc_no(meta, "boq"), "package_level": True},
        ))
        in_scope_findings += 1
    else:
        for pc in (boq.get("summary") or {}).get("principal_checks") or []:
            if pc.get("result") == "pass":
                rows.append(comparison_row(
                    "C5", "principal_vs_parts", ["boq"],
                    {"address": pc.get("address"), "cached": pc.get("cached_value"),
                     "part_sum": pc.get("part_sum")}, "m3", pc.get("difference"),
                    pc.get("pct_difference"), "pass",
                    note="R3: principal quantity matches part items",
                ))

    # R12: suitable fill F23 = excavation stated − disposal stated (same basis)
    # Not total fill (I23 / F23+F25) vs cut base − disposal.
    cut_st = _stated_of(_boq_earth(boq, "cut"))
    disposal_st = _stated_of(_boq_earth(boq, "disposal"))
    suitable = _boq_earth(boq, "suitable_fill") or _boq_earth(boq, "fill")
    suitable_st = _stated_of(suitable)
    if cut_st is not None and suitable_st is not None and disposal_st is not None:
        expected = cut_st - disposal_st
        diff = abs(expected - suitable_st)
        pd = (diff / abs(cut_st)) if cut_st else None
        result = "flag" if pd is not None and pd > 0.10 else "pass"
        rows.append(comparison_row(
            "C5", "part_arithmetic_fill", ["boq"],
            {"excavation_stated": cut_st, "disposal_stated": disposal_st,
             "suitable_fill_stated": suitable_st, "expected_suitable_fill": expected,
             "address": (suitable or {}).get("address")},
            "m3", diff, pd, result,
            severity="Minor" if result == "flag" else None,
            status="Probable" if result == "flag" else None,
            note="R12: suitable fill F23 = excavation − disposal (stated)",
        ))
        if result == "flag":
            findings.append(finding_stub(
                "C5", "BOQ part arithmetic: suitable fill ≠ excavation − disposal",
                f"Suitable fill (F23) stated {suitable_st:g} differs from excavation "
                f"{cut_st:g} − disposal {disposal_st:g} = {expected:g}.",
                "inconsistency", "Minor", "Probable",
                [evid("E01", "xlsx_cell",
                      f"cut={cut_st}; disposal={disposal_st}; suitable_fill={suitable_st}")],
                meta["site"], meta["sub"], meta["run"],
                location={"document_no": _doc_no(meta, "boq"), "package_level": True},
            ))
            in_scope_findings += 1
    elif in_scope_findings == 0 and not groups:
        rows.append(comparison_row(
            "C5", "internal_integrity", ["boq"], {"cell_errors": 0}, None, None, None, "pass",
            note="no #REF! / error cells",
        ))



def run_c6(boq, register, params, stage, rows, findings, meta):
    road = _boq_road(boq, "road_area")
    kerb = _boq_road(boq, "kerb")
    model_area, model_kerb, provenance = _road_model(register)
    pct = stage_pct(stage)
    finding_ok = pct is not None and pct >= params["c6_finding_from_pct"]

    if provenance in ("self", "unproven") and register is not None:
        # C6: never BOQ vs itself — refuse comparison
        rows.append(comparison_row(
            "C6", "road_boq_vs_model", ["boq", "model"],
            {"boq_road_area": _base_of(road), "boq_kerb": _base_of(kerb),
             "refused_model_area": model_area, "refused_model_kerb": model_kerb,
             "provenance": provenance},
            None, None, None, "not_run",
            note="C6 refused: model/register is not an independent skill-10/linework source "
                 "(never BOQ vs itself)",
        ))
        return

    pairs = [
        ("road_area", _base_of(road), model_area, _unit_of(road, "m2"), road),
        ("kerb_length", _base_of(kerb), model_kerb, _unit_of(kerb, "m"), kerb),
    ]
    any_data = False
    for name, boq_v, model_v, unit, rec in pairs:
        if boq_v is None and model_v is None:
            continue
        # Extra guard: identical numeric twin with no linework → self
        if (boq_v is not None and model_v is not None and abs(float(boq_v) - float(model_v)) < 1e-9
                and provenance != "ok"):
            rows.append(comparison_row(
                "C6", name, ["boq", "model"], {"boq_base": boq_v, "model": model_v},
                unit, None, None, "not_run",
                note="C6 refused: model equals BOQ base without independent provenance",
            ))
            any_data = True
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
                    location={"document_no": _doc_no(meta, "boq"), "package_level": True},
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
    ap.add_argument("--document-no", default=None, dest="document_no",
                    help="Fallback Parsons document number for findings (R8)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        params = load_params(a.params)
        boq = read_json(a.boq)
        report = read_json(a.report) if a.report else None
        profile = read_json(a.profile) if a.profile else None
        drawing = read_json(a.drawing_table) if a.drawing_table else None
        register = read_json(a.road_register) if a.road_register else None
        boq_doc = ((boq.get("source") or {}).get("document_no")
                   or a.document_no or Path(a.boq).stem)
        report_doc = (report or {}).get("document_no") or a.document_no
        meta = {
            "site": a.site,
            "sub": a.sub,
            "run": a.run or f"{a.site}-{a.sub}-19700101T0000",
            "stage": a.stage,
            "stage_pct": stage_pct(a.stage),
            "boq_document_no": boq_doc,
            "report_document_no": report_doc,
            "document_no": a.document_no or boq_doc,
            "params": {
                "qty_pct": params["qty_pct"],
                "qty_abs_m3": params["qty_abs_m3"],
                "c4_abs_m": params["c4_abs_m"],
                "c6_finding_from_pct": params["c6_finding_from_pct"],
                "tag": ((params.get("raw") or {}).get("tolerances") or {}).get("tag")
                       or (params.get("raw") or {}).get("tag"),
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
