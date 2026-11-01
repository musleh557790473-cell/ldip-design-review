#!/usr/bin/env python3
"""W1 / W2 / W3 / W5-geometry / W6-plan checks (skill 11 phase A, SPC-0006 Rev Q §4.19).

DQ-11: report only — findings carry issue_type but **no rule_id** (rule IDs in text).
DQ-12 severity; DQ-9 W2 tolerances; DQ-15 ER storm Observations; status Confirmed/Probable.
OUT of phase A: W4, W5 hydraulics, W7, W8, wntr, binary .out.

Usage:
  python check_wet.py --networks <networks.json> --out-consistency <consistency.json>
      --out-candidates <candidates.json>
      [--criteria <criteria.json>] [--plan <wet_plan.json>]
      [--presence <presence.json>] [--params <params.json>]
      [--site NMAR] [--sub <sub-id>] [--run <run-id>]
      [--stage "30% Concept Design"]

Exit: 0 done · 2 input · 4 schema. No network, no shell.
"""
__version__ = "0.1.0"

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wet_common import (  # noqa: E402
    EXIT_OK, EXIT_SCHEMA, FINDING_SCHEMA, PARAMS_PATH, SKILL_ROOT, atomic_write_json,
    comparison_row, emit, evid, fail, finding_stub, load_params, main_guard, read_json,
    stage_pct,
)

CONS_SCHEMA = SKILL_ROOT / "templates" / "consistency.schema.json"
CAND_SCHEMA = SKILL_ROOT / "templates" / "candidates.schema.json"


def _params_er(params):
    raw = params.get("raw") or {}
    return raw


def run_w1(presence, networks, stage, params, rows, findings, meta, status_cap):
    """W1 — model and drawing presence per discipline (DQ-8 storm model required)."""
    pct = stage_pct(stage) or 30
    sev = "Minor" if pct <= 30 else "Major"
    required = (params.get("raw") or {}).get("w1_required") or {
        "sewer": {"plan": True, "model": True},
        "water": {"plan": True, "model": True},
        "irrigation": {"plan": True, "model": True},
        "storm": {"plan": True, "model": True},
    }
    # presence.json optional override; else infer from networks / plan
    present = presence or {}
    net_name = (networks or {}).get("network")
    has_model = bool((networks or {}).get("conduits"))
    has_plan = bool((networks or {}).get("plan") or (networks or {}).get("plan_nodes"))
    if present.get("networks"):
        # structured presence
        for disc, req in required.items():
            info = (present.get("networks") or {}).get(disc) or {}
            for kind, needed in req.items():
                if not needed:
                    continue
                ok = bool(info.get(kind))
                key = f"{disc}:{kind}"
                if ok:
                    rows.append(comparison_row(
                        "W1", f"presence:{key}", ["presence"],
                        {"discipline": disc, "kind": kind, "present": True},
                        None, None, None, "pass",
                        note="WUT-001…009 presence (report-only)",
                    ))
                else:
                    st = "Probable" if status_cap == "Probable" else "Missing information"
                    rows.append(comparison_row(
                        "W1", f"presence:{key}", ["presence"],
                        {"discipline": disc, "kind": kind, "present": False},
                        None, None, None, "flag", severity=sev, status=st,
                        note="W1 missing deliverable",
                    ))
                    clause = "ER §6.3.5 / §6.3.8"
                    if disc == "storm" and kind == "model":
                        clause = "ER §6.3.5 item 1, §6.3.8 item 1 (DQ-8)"
                    findings.append(finding_stub(
                        "W1", f"Missing {disc} {kind}",
                        f"Discipline '{disc}' {kind} is required at this stage but was not found "
                        f"in the submittal (WUT-001…009 presence; {clause}). Route CRS.",
                        "missing_information", sev, st,
                        [evid("E01", "script_output", f"missing {key}")],
                        meta["site"], meta["sub"], meta["run"],
                        location={"document_no": meta.get("doc") or "MIDP", "package_level": True},
                        status_cap=status_cap, source_doc="Employer's Requirements §6.3.5 / §6.3.8",
                    ))
        # Missing hydraulic calculations (DQ-4)
        if not present.get("hydraulic_calculations"):
            st = "Probable" if status_cap == "Probable" else "Missing information"
            findings.append(finding_stub(
                "W1", "Missing hydraulic calculation results",
                "Parsons submitted no hydraulic calculation results (FlexTable / report tables). "
                "ER §6.3.5 items 1 / 4 and §6.3.8 item 1 require calculations and surge analysis "
                "(DQ-4). Route CRS.",
                "missing_information", sev, st,
                [evid("E01", "script_output", "hydraulic_calculations=absent")],
                meta["site"], meta["sub"], meta["run"],
                location={"document_no": meta.get("doc") or "DESIGN-REPORT", "package_level": True},
                status_cap=status_cap,
                source_doc="Employer's Requirements §6.3.5 / §6.3.8",
            ))
            rows.append(comparison_row(
                "W1", "presence:hydraulic_calculations", ["presence"],
                {"present": False}, None, None, None, "flag", severity=sev, status=st,
            ))
        return
    # Infer single-network presence
    disc = net_name or "sewer"
    for kind, ok in (("model", has_model), ("plan", has_plan)):
        if ok:
            rows.append(comparison_row(
                "W1", f"presence:{disc}:{kind}", ["networks"],
                {"discipline": disc, "kind": kind, "present": True},
                None, None, None, "pass",
            ))
        else:
            st = "Probable" if status_cap == "Probable" else "Missing information"
            rows.append(comparison_row(
                "W1", f"presence:{disc}:{kind}", ["networks"],
                {"discipline": disc, "kind": kind, "present": False},
                None, None, None, "flag", severity=sev, status=st,
            ))
            findings.append(finding_stub(
                "W1", f"Missing {disc} {kind}",
                f"Discipline '{disc}' {kind} not present in networks.json for this run "
                f"(WUT-001…009). ER §6.3.5 / §6.3.8. Route CRS.",
                "missing_information", sev, st,
                [evid("E01", "script_output", f"networks missing {kind}")],
                meta["site"], meta["sub"], meta["run"],
                status_cap=status_cap,
            ))


def _tol_flag(a, b, abs_tol):
    if a is None or b is None:
        return None, None
    diff = abs(float(a) - float(b))
    return diff, diff > abs_tol + 1e-12


def run_w2(networks, criteria, params, rows, findings, meta, status_cap):
    """W2 — report ↔ model ↔ drawing consistency (DQ-9)."""
    tol = params
    conduits = (networks or {}).get("conduits") or []
    plan = (networks or {}).get("plan") or []
    summary = (networks or {}).get("summary") or {}
    totals = (criteria or {}).get("declared_totals") or {}

    # Report totals vs model
    model_km = summary.get("length_km")
    if model_km is None and conduits:
        model_km = sum(c.get("length_m") or 0.0 for c in conduits) / 1000.0
    rep_len = totals.get("total_length_km")
    if rep_len and model_km is not None:
        rv = float(rep_len["value"])
        pct = abs(rv - model_km) / max(abs(rv), abs(model_km), 1e-12)
        flag = pct > tol["report_pct"] + 1e-15
        rows.append(comparison_row(
            "W2", "report_vs_model:length_km", ["report", "model"],
            {"report_km": rv, "model_km": model_km, "pct": round(pct, 6)},
            "km", abs(rv - model_km), pct,
            "flag" if flag else "pass",
            severity="Minor" if flag else None,
            status="Probable" if flag else None,
            note="DQ-9 report totals ± 2 %",
        ))
        if flag:
            findings.append(finding_stub(
                "W2", "Report length disagrees with model",
                f"Design report states network length {rv} km (p.{rep_len.get('page')}); "
                f"model sums to {model_km:.3f} km (|Δ|={pct*100:.2f}% > {tol['report_pct']*100:.0f}%). "
                f"Both values quoted (skill 16 pattern). WUT-1xx consistency.",
                "inconsistency", "Minor", "Probable",
                [evid("E01", "script_output",
                      f"report_km={rv} model_km={model_km} pct={pct}")],
                meta["site"], meta["sub"], meta["run"],
                status_cap=status_cap,
            ))

    depth = totals.get("depth_range_m")
    if depth and conduits:
        invs = []
        grounds = []
        for c in conduits:
            if c.get("invert_start_m") is not None and c.get("cover_m") is not None:
                # depth ≈ cover + diameter
                d_m = (c.get("diameter_mm") or 0) / 1000.0
                grounds.append((c.get("cover_m") or 0) + d_m + (c.get("invert_start_m") or 0))
            if c.get("invert_start_m") is not None:
                invs.append(c["invert_start_m"])
        # Compare stated depth range to model invert/cover span when available
        model_depths = []
        for c in conduits:
            if c.get("cover_m") is not None and c.get("diameter_mm") is not None:
                model_depths.append(c["cover_m"] + c["diameter_mm"] / 1000.0)
        if model_depths:
            mn, mx = min(model_depths), max(model_depths)
            rmin, rmax = float(depth["min_m"]), float(depth["max_m"])
            # flag if model span outside report ± 2% of mid
            mid = 0.5 * (rmin + rmax)
            ok = (mn >= rmin * (1 - tol["report_pct"]) and mx <= rmax * (1 + tol["report_pct"]))
            rows.append(comparison_row(
                "W2", "report_vs_model:depth_range", ["report", "model"],
                {"report_min": rmin, "report_max": rmax, "model_min": mn, "model_max": mx},
                "m", None, None, "pass" if ok else "flag",
                severity="Minor" if not ok else None,
                status="Probable" if not ok else None,
            ))
            if not ok:
                findings.append(finding_stub(
                    "W2", "Report depth range disagrees with model",
                    f"Report depth range {rmin} – {rmax} m (p.{depth.get('page')}); "
                    f"model cover+D span {mn:.2f} – {mx:.2f} m. WUT-1xx.",
                    "inconsistency", "Minor", "Probable",
                    [evid("E01", "script_output",
                          f"report={rmin}-{rmax} model={mn}-{mx}")],
                    meta["site"], meta["sub"], meta["run"],
                    status_cap=status_cap,
                ))

    # Element-level plan vs model (L, S, levels, E/N)
    # Match by nearest plan pipe label with same diameter, or by order when ids align
    plan_labels = [p for p in plan if p.get("kind") == "pipe_label" or p.get("length_m") is not None]
    matched = 0
    for c in conduits:
        cid = str(c.get("id") or "")
        candidates = [p for p in plan_labels if cid and cid.casefold() in str(p.get("raw") or "").casefold()]
        if not candidates and c.get("diameter_mm") is not None:
            candidates = [p for p in plan_labels
                          if p.get("diameter_mm") is not None
                          and abs(p["diameter_mm"] - c["diameter_mm"]) < 1.0
                          and p.get("_used") is not True]
        if not candidates:
            continue
        p = candidates[0]
        p["_used"] = True
        matched += 1
        # Length
        diff, flag = _tol_flag(c.get("length_m"), p.get("length_m"), tol["length_m"])
        if p.get("length_m") is not None and c.get("length_m") is not None:
            st = "Confirmed" if not status_cap else "Probable"
            rows.append(comparison_row(
                "W2", f"element:L:{cid}", ["model", "plan"],
                {"model": c.get("length_m"), "plan": p.get("length_m"), "id": cid},
                "m", diff, None, "flag" if flag else "pass",
                severity="Minor" if flag else None,
                status=st if flag else None,
                note="DQ-9 L ± 0.05 m; DWG label vs model → Confirmed",
            ))
            if flag:
                findings.append(finding_stub(
                    "W2", f"Pipe {cid} length label ≠ model",
                    f"Plan label L={p.get('length_m')} m; model length {c.get('length_m')} m "
                    f"(|Δ|={diff:.3f} m > {tol['length_m']} m). WUT-1xx.",
                    "inconsistency", "Minor", st,
                    [evid("E01", "script_output",
                          f"id={cid} model_L={c.get('length_m')} plan_L={p.get('length_m')}")],
                    meta["site"], meta["sub"], meta["run"],
                    status_cap=status_cap,
                ))
        # Slope
        diff, flag = _tol_flag(c.get("slope_pct"), p.get("slope_pct"), tol["slope_pct"])
        if p.get("slope_pct") is not None and c.get("slope_pct") is not None:
            st = "Confirmed" if not status_cap else "Probable"
            rows.append(comparison_row(
                "W2", f"element:S:{cid}", ["model", "plan"],
                {"model": c.get("slope_pct"), "plan": p.get("slope_pct"), "id": cid},
                "%", diff, None, "flag" if flag else "pass",
                severity="Minor" if flag else None,
                status=st if flag else None,
                note="DQ-9 slope ± 0.01 %",
            ))
            if flag:
                findings.append(finding_stub(
                    "W2", f"Pipe {cid} slope label ≠ model",
                    f"Plan label S={p.get('slope_pct')} %; model slope {c.get('slope_pct')} % "
                    f"(|Δ|={diff:.4f} > {tol['slope_pct']}). WUT-1xx.",
                    "inconsistency", "Minor", st,
                    [evid("E01", "script_output",
                          f"id={cid} model_S={c.get('slope_pct')} plan_S={p.get('slope_pct')}")],
                    meta["site"], meta["sub"], meta["run"],
                    status_cap=status_cap,
                ))
    rows.append(comparison_row(
        "W2", "element_match_count", ["model", "plan"],
        {"matched": matched, "model_conduits": len(conduits), "plan_labels": len(plan_labels)},
        None, None, None, "information",
    ))


def _min_slope_for_d(d_mm, er_table, declared_table):
    """Prefer ER table; fall back to declared gradient table."""
    table = er_table or declared_table or []
    if not table or d_mm is None:
        return None, None
    # exact then nearest lower
    rows = sorted(table, key=lambda r: float(r["diameter_mm"]))
    exact = [r for r in rows if abs(float(r["diameter_mm"]) - d_mm) < 0.6]
    if exact:
        return float(exact[0]["min_slope_pct"]), exact[0]
    below = [r for r in rows if float(r["diameter_mm"]) <= d_mm + 0.6]
    if below:
        r = below[-1]
        return float(r["min_slope_pct"]), r
    return float(rows[0]["min_slope_pct"]), rows[0]


def run_w3(networks, criteria, params, rows, findings, meta, status_cap):
    """W3 — gravity sewer geometry (min D, min gradient, MH spacing, cover, adverse)."""
    raw = params.get("raw") or {}
    er = raw.get("er") or {}
    sewer = er.get("sewer") or {}
    declared_vals = (raw.get("declared") or {}).get("sewer") or {}
    min_d = float(sewer.get("min_diameter_mm", 200))
    max_mh = sewer.get("mh_spacing_m") or {"min": 30, "max": 80}
    max_spacing = float(max_mh.get("max", 80))
    # DQ-7: sewer cover is declared (ER silent) — soffit < 1.0 m → protection §6.3.3
    cover_min = float(declared_vals.get("cover_m", 1.0))
    cover_source = declared_vals.get("source") or "declared"
    cover_clause = declared_vals.get("clause") or "§6.3.3"
    max_depth = sewer.get("max_depth_m")
    er_grad = sewer.get("min_slope_by_d_mm") or []
    declared = ((criteria or {}).get("gradient_tables") or {}).get("sewer") or []
    thr = params["w3_threshold_tol"]

    conduits = [c for c in ((networks or {}).get("conduits") or [])
                if (networks or {}).get("network") in (None, "sewer", "force_main")
                or c.get("network") in (None, "sewer")]
    if (networks or {}).get("network") not in (None, "sewer", "force_main") and not conduits:
        rows.append(comparison_row(
            "W3", "geometry", ["model"], {}, None, None, None, "not_run",
            note="W3 applies to gravity sewer",
        ))
        return

    # topology for adverse grade
    by_id = {c["element_id"]: c for c in conduits if c.get("element_id") is not None}
    # outfalls = nodes that are never a start
    starts = {c.get("start_node_id") for c in conduits}
    stops = {c.get("stop_node_id") for c in conduits}
    outfalls = (stops - starts) - {None}

    for c in conduits:
        cid = c.get("id") or c.get("element_id")
        d = c.get("diameter_mm")
        # min D
        if d is not None and d + thr < min_d:
            rows.append(comparison_row(
                "W3", f"min_D:{cid}", ["model"],
                {"diameter_mm": d, "min_mm": min_d}, "mm", min_d - d, None, "flag",
                severity="Minor", status="Probable",
                note="ER §7.5.2 min D 200 mm",
            ))
            findings.append(finding_stub(
                "W3", f"Sewer {cid} below min diameter",
                f"Model diameter {d} mm < ER minimum {min_d} mm (§7.5.2 p.32). WUT-010.",
                "design_error", "Minor", "Probable",
                [evid("E01", "script_output", f"id={cid} D={d} min={min_d}")],
                meta["site"], meta["sub"], meta["run"],
                status_cap=status_cap, required_value=min_d, unit="mm",
                source_doc="ER §7.5.2 p.32",
            ))
        else:
            rows.append(comparison_row(
                "W3", f"min_D:{cid}", ["model"],
                {"diameter_mm": d, "min_mm": min_d}, "mm", None, None,
                "pass" if d is not None else "not_run",
            ))

        # min gradient
        need, src = _min_slope_for_d(d, er_grad, declared)
        s = c.get("slope_pct")
        if need is not None and s is not None:
            # Strict below-minimum (T11-4: 0.49 flags, 0.50 passes). thr is for
            # float noise only (1e-9 band), not a 0.01 grace below the ER limit.
            if s + 1e-9 < need:
                rows.append(comparison_row(
                    "W3", f"min_slope:{cid}", ["model", "ER"],
                    {"slope_pct": s, "min_pct": need, "diameter_mm": d},
                    "%", need - s, None, "flag",
                    severity="Major", status="Probable",
                    note="ER §7.5.2 min gradient per D",
                ))
                findings.append(finding_stub(
                    "W3", f"Sewer {cid} gradient below minimum",
                    f"Model slope {s:.3f} % < minimum {need:.3f} % for D≈{d} mm "
                    f"(ER §7.5.2 / declared table). WUT-011.",
                    "design_error", "Major", "Probable",
                    [evid("E01", "script_output",
                          f"id={cid} S={s} min={need} D={d} src={src}")],
                    meta["site"], meta["sub"], meta["run"],
                    status_cap=status_cap, required_value=need, unit="%",
                    source_doc="ER §7.5.2 p.32",
                ))
            else:
                rows.append(comparison_row(
                    "W3", f"min_slope:{cid}", ["model", "ER"],
                    {"slope_pct": s, "min_pct": need, "diameter_mm": d},
                    "%", None, None, "pass",
                ))

        # MH spacing = conduit length vs max
        L = c.get("length_m")
        if L is not None and L - thr > max_spacing:
            rows.append(comparison_row(
                "W3", f"mh_spacing:{cid}", ["model"],
                {"length_m": L, "max_m": max_spacing}, "m", L - max_spacing, None, "flag",
                severity="Major", status="Probable",
            ))
            findings.append(finding_stub(
                "W3", f"Sewer {cid} exceeds MH spacing",
                f"Segment length {L:.2f} m > ER MH spacing maximum {max_spacing:.0f} m "
                f"(§7.5.2 30–80 m). WUT-012.",
                "design_error", "Major", "Probable",
                [evid("E01", "script_output", f"id={cid} L={L} max={max_spacing}")],
                meta["site"], meta["sub"], meta["run"],
                status_cap=status_cap, required_value=max_spacing, unit="m",
                source_doc="ER §7.5.2 p.32",
            ))
        elif L is not None:
            rows.append(comparison_row(
                "W3", f"mh_spacing:{cid}", ["model"],
                {"length_m": L, "max_m": max_spacing}, "m", None, None, "pass",
            ))

        # cover (declared §6.3.3 — soffit < 1.0 m → special protection)
        cover = c.get("cover_m")
        if cover is not None and cover + thr < cover_min:
            rows.append(comparison_row(
                "W3", f"cover:{cid}", ["model", cover_source],
                {"cover_m": cover, "min_m": cover_min, "source": cover_source,
                 "clause": cover_clause},
                "m", cover_min - cover, None, "flag",
                severity="Minor", status="Probable",
                note=f"declared cover {cover_clause} (ER silent)",
            ))
            findings.append(finding_stub(
                "W3", f"Sewer {cid} soffit cover below declared",
                f"Soffit cover {cover:.3f} m < {cover_min} m → special protection "
                f"(declared {cover_clause}; source={cover_source}; ER silent on numeric cover). "
                f"WUT-013.",
                "design_error", "Minor", "Probable",
                [evid("E01", "script_output",
                      f"id={cid} cover={cover} min={cover_min} source={cover_source}")],
                meta["site"], meta["sub"], meta["run"],
                status_cap=status_cap, source_doc=f"declared {cover_clause}",
            ))
        elif cover is not None:
            rows.append(comparison_row(
                "W3", f"cover:{cid}", ["model", cover_source],
                {"cover_m": cover, "min_m": cover_min, "source": cover_source},
                "m", None, None, "pass",
                note=f"declared cover {cover_clause}",
            ))

        # adverse grade: invert rises in flow direction (start → stop toward outfall)
        inv_s, inv_e = c.get("invert_start_m"), c.get("invert_stop_m")
        if inv_s is not None and inv_e is not None and inv_e > inv_s + thr / 100.0:
            # Only flag when stop is closer to outfall set (topology walk heuristic)
            toward_outfall = c.get("stop_node_id") in outfalls or c.get("stop_node_id") in stops
            if toward_outfall or outfalls:
                rows.append(comparison_row(
                    "W3", f"adverse:{cid}", ["model"],
                    {"invert_start_m": inv_s, "invert_stop_m": inv_e},
                    "m", inv_e - inv_s, None, "flag",
                    severity="Major", status="Probable",
                    note="adverse grade by flow direction",
                ))
                findings.append(finding_stub(
                    "W3", f"Sewer {cid} adverse grade",
                    f"Invert rises along flow {inv_s:.3f} → {inv_e:.3f} m (adverse). WUT-014.",
                    "design_error", "Major", "Probable",
                    [evid("E01", "script_output",
                          f"id={cid} IL_start={inv_s} IL_stop={inv_e}")],
                    meta["site"], meta["sub"], meta["run"],
                    status_cap=status_cap,
                ))


def _fixture_spacing(fixtures, kind, max_m):
    pts = [(f.get("e_m"), f.get("n_m"), f) for f in fixtures
           if f.get("kind") == kind and f.get("e_m") is not None and f.get("n_m") is not None]
    flags = []
    for i, (e1, n1, f1) in enumerate(pts):
        nearest = None
        for j, (e2, n2, f2) in enumerate(pts):
            if i == j:
                continue
            d = ((e1 - e2) ** 2 + (n1 - n2) ** 2) ** 0.5
            if nearest is None or d < nearest:
                nearest = d
        if nearest is not None and nearest > max_m + 1e-9:
            flags.append((f1, nearest, max_m))
    return flags


def run_w5_geom(networks, plan_doc, params, rows, findings, meta, status_cap):
    """W5 geometry only — min D, hydrant spacing, valves / washouts / air-valves, cover."""
    raw = params.get("raw") or {}
    er = (raw.get("er") or {}).get("water") or {}
    declared_w = (raw.get("declared") or {}).get("water") or {}
    # DQ-7: water min D is declared (§5.3.7 110/160 mm); ER silent
    min_d = float(declared_w.get("min_diameter_mm", 110))
    min_d_source = declared_w.get("source") or "declared"
    min_d_clause = declared_w.get("clause") or "§5.3.7"
    # ER ~1.2 m in roads — keep as ER, advisory
    cover_min = float(er.get("cover_m", 1.2))
    cover_advisory = bool(er.get("cover_advisory", True))
    hyd_res = float((er.get("hydrant_spacing_m") or {}).get("residential", 150))
    hyd_com = float((er.get("hydrant_spacing_m") or {}).get("commercial", 90))
    # use residential default unless plan says commercial
    hyd_max = hyd_res
    valve_max = float(er.get("valve_spacing_m", 500))

    net = (networks or {}).get("network")
    conduits = (networks or {}).get("conduits") or []
    if net not in (None, "water", "irrigation") and not any(
            c.get("network") in ("water", "irrigation") for c in conduits):
        # still allow when fixtures present on plan
        pass
    fixtures = list((networks or {}).get("fixtures") or [])
    if plan_doc:
        fixtures.extend(plan_doc.get("fixtures") or [])

    for c in conduits:
        if net not in (None, "water", "irrigation") and c.get("network") not in ("water", "irrigation", None):
            continue
        cid = c.get("id") or c.get("element_id")
        d = c.get("diameter_mm")
        if d is not None and d + params["w3_threshold_tol"] < min_d:
            findings.append(finding_stub(
                "W5", f"Pressure pipe {cid} below min D",
                f"Diameter {d} mm < declared minimum {min_d} mm "
                f"({min_d_clause}; source={min_d_source}; ER silent on min D). "
                f"WUT-020 (geometry).",
                "design_error", "Minor", "Probable",
                [evid("E01", "script_output",
                      f"id={cid} D={d} min={min_d} source={min_d_source}")],
                meta["site"], meta["sub"], meta["run"],
                status_cap=status_cap, source_doc=f"declared {min_d_clause}",
            ))
            rows.append(comparison_row(
                "W5", f"min_D:{cid}", ["model", min_d_source],
                {"diameter_mm": d, "min_mm": min_d, "source": min_d_source,
                 "clause": min_d_clause},
                "mm", None, None, "flag", severity="Minor", status="Probable",
                note=f"declared min D {min_d_clause}",
            ))
        cover = c.get("cover_m")
        if cover is not None and cover + params["w3_threshold_tol"] < cover_min:
            findings.append(finding_stub(
                "W5", f"Pressure pipe {cid} cover low",
                f"Cover {cover:.3f} m < ~{cover_min} m in roads "
                f"(ER §7.5.1 advisory). WUT-020.",
                "design_error", "Minor", "Probable",
                [evid("E01", "script_output",
                      f"id={cid} cover={cover} advisory={cover_advisory}")],
                meta["site"], meta["sub"], meta["run"],
                status_cap=status_cap, source_doc="ER §7.5.1 (advisory cover)",
            ))

    # Hydrant spacing
    for f, nearest, mx in _fixture_spacing(fixtures, "hydrant", hyd_max):
        findings.append(finding_stub(
            "W5", "Hydrant spacing exceeds limit",
            f"Hydrant '{f.get('label')}' nearest neighbour {nearest:.1f} m > "
            f"{mx:.0f} m (FLS / ER §7.5.13; residential {hyd_res:.0f} / commercial {hyd_com:.0f}). "
            f"WUT-021.",
            "design_error", "Major", "Probable",
            [evid("E01", "script_output",
                  f"label={f.get('label')} nearest={nearest} max={mx}")],
            meta["site"], meta["sub"], meta["run"],
            status_cap=status_cap, source_doc="ER / FLS p.48–49",
        ))
        rows.append(comparison_row(
            "W5", "hydrant_spacing", ["plan"],
            {"label": f.get("label"), "nearest_m": nearest, "max_m": mx},
            "m", nearest - mx, None, "flag", severity="Major", status="Probable",
        ))
    if any(f.get("kind") == "hydrant" for f in fixtures):
        if not any(r.get("quantity") == "hydrant_spacing" and r.get("result") == "flag" for r in rows):
            rows.append(comparison_row(
                "W5", "hydrant_spacing", ["plan"], {"max_m": hyd_max},
                "m", None, None, "pass",
            ))

    # Valves presence / spacing (geometry)
    valves = [f for f in fixtures if f.get("kind") in ("valve", "washout", "air_valve")]
    if valves:
        for f, nearest, mx in _fixture_spacing(valves, "valve", valve_max):
            findings.append(finding_stub(
                "W5", "Valve spacing exceeds limit",
                f"Valve '{f.get('label')}' nearest {nearest:.1f} m > ER ≤ {valve_max:.0f} m "
                f"(§7.5.1). WUT-022.",
                "design_error", "Minor", "Probable",
                [evid("E01", "script_output", f"nearest={nearest} max={valve_max}")],
                meta["site"], meta["sub"], meta["run"],
                status_cap=status_cap,
            ))
        rows.append(comparison_row(
            "W5", "valves_present", ["plan"],
            {"count": len(valves)}, None, None, None, "pass",
            note="washouts at dead ends / air-valves checked for presence only (phase A)",
        ))
    else:
        rows.append(comparison_row(
            "W5", "valves_present", ["plan"], {"count": 0},
            None, None, None, "information",
            note="no valve/washout/air-valve fixtures on plan",
        ))


def run_w6(networks, criteria, plan_doc, params, rows, findings, meta, status_cap):
    """W6 — storm network plan/report table + ER conflict Observations (DQ-13 / DQ-15)."""
    raw = params.get("raw") or {}
    er = (raw.get("er") or {}).get("storm") or {}
    conflicts = (raw.get("er_conflicts") or {}).get("storm") or []

    # DQ-15 Observations for ER internal conflicts
    for conf in conflicts:
        findings.append(finding_stub(
            "W6", conf.get("title") or "ER storm internal conflict",
            conf.get("statement") or (
                f"ER conflict: {conf.get('a')} vs {conf.get('b')}. "
                f"Until answered, W6 applies {conf.get('applied')}. RFI candidate (DQ-13 / DQ-15)."
            ),
            "er_ambiguity", "Observation", "Probable",
            [evid("E01", "script_output", json.dumps(conf, ensure_ascii=False)[:500])],
            meta["site"], meta["sub"], meta["run"],
            status_cap=status_cap,
            source_doc=conf.get("source_doc") or "ER §7.5.3 / §7.5.6–7.5.8",
            note="DQ-15 Observation — ER internal storm conflict; RFI with DQ-13",
        ))
        rows.append(comparison_row(
            "W6", f"er_conflict:{conf.get('id', 'storm')}", ["ER"],
            conf, None, None, None, "observation",
            severity="Observation", status="Probable",
        ))

    net = (networks or {}).get("network")
    conduits = (networks or {}).get("conduits") or []
    plan = (networks or {}).get("plan") or []
    if plan_doc:
        plan = plan + (plan_doc.get("pipes") or [])
    fixtures = list((networks or {}).get("fixtures") or [])
    if plan_doc:
        fixtures.extend(plan_doc.get("fixtures") or [])

    is_storm = net == "storm" or any(c.get("network") == "storm" for c in conduits) or any(
        (p.get("utility") == "storm") for p in plan
    )
    # Always emit conflict observations; geometry checks when storm data present
    if not is_storm and not ((criteria or {}).get("storm_pipe_table")):
        rows.append(comparison_row(
            "W6", "storm_geometry", ["plan"], {}, None, None, None, "not_run",
            note="no storm network in this run",
        ))
        return

    min_d = float(er.get("min_diameter_mm", 300))
    cover_min = float(er.get("cover_m", 1.2))
    mh = er.get("mh_spacing_m") or {"lt_800_mm_max": 100, "gt_800_mm_max": 120}
    storm_pipes = plan if plan else conduits

    for p in storm_pipes:
        if p.get("utility") not in (None, "storm") and p.get("network") not in (None, "storm"):
            if net != "storm":
                continue
        d = p.get("diameter_mm")
        cid = p.get("id") or p.get("handle") or p.get("raw") or "?"
        if d is not None and d + params["w3_threshold_tol"] < min_d:
            findings.append(finding_stub(
                "W6", f"Storm pipe below min D",
                f"Storm diameter {d} mm < ER minimum {min_d} mm. WUT-030.",
                "design_error", "Minor", "Probable",
                [evid("E01", "script_output", f"D={d} min={min_d}")],
                meta["site"], meta["sub"], meta["run"],
                status_cap=status_cap,
            ))
            rows.append(comparison_row(
                "W6", f"min_D:{cid}", ["plan" if p.get("source") == "plan" else "model"],
                {"diameter_mm": d, "min_mm": min_d}, "mm", None, None, "flag",
                severity="Minor", status="Probable",
            ))
        L = p.get("length_m")
        max_sp = float(mh.get("lt_800_mm_max", 100))
        if d is not None and d > 800:
            max_sp = float(mh.get("gt_800_mm_max", 120))
        if L is not None and L > max_sp + params["w3_threshold_tol"]:
            findings.append(finding_stub(
                "W6", "Storm MH spacing exceeded",
                f"Storm segment length {L:.1f} m > Table 7-11 max {max_sp:.0f} m "
                f"for D≈{d} mm (Probable from plan). WUT-031.",
                "design_error", "Major", "Probable",
                [evid("E01", "script_output", f"L={L} max={max_sp} D={d}")],
                meta["site"], meta["sub"], meta["run"],
                status_cap=status_cap, source_doc="ER Table 7-11 p.46",
            ))
            rows.append(comparison_row(
                "W6", f"mh_spacing:{cid}", ["plan"],
                {"length_m": L, "max_m": max_sp}, "m", None, None, "flag",
                severity="Major", status="Probable",
            ))
        cover = p.get("cover_m")
        if cover is not None and cover + params["w3_threshold_tol"] < cover_min:
            findings.append(finding_stub(
                "W6", "Storm cover below minimum",
                f"Cover {cover:.3f} m < {cover_min} m (ER §7.5.8). WUT-032.",
                "design_error", "Minor", "Probable",
                [evid("E01", "script_output", f"cover={cover}")],
                meta["site"], meta["sub"], meta["run"],
                status_cap=status_cap,
            ))

    # Outfalls / ponds drawn
    outfalls = [f for f in fixtures if f.get("kind") == "outfall"]
    ponds = [f for f in fixtures if f.get("kind") == "pond"]
    if is_storm or storm_pipes:
        if not outfalls:
            findings.append(finding_stub(
                "W6", "Storm outfall not drawn",
                "No outfall symbol / label found on the storm plan (W6 plan check). "
                "WUT-033 presence.",
                "missing_information", "Minor",
                "Probable" if status_cap == "Probable" else "Missing information",
                [evid("E01", "script_output", "outfalls=0")],
                meta["site"], meta["sub"], meta["run"],
                status_cap=status_cap,
            ))
            rows.append(comparison_row(
                "W6", "outfall_presence", ["plan"], {"count": 0},
                None, None, None, "flag", severity="Minor",
                status="Probable",
            ))
        else:
            rows.append(comparison_row(
                "W6", "outfall_presence", ["plan"], {"count": len(outfalls)},
                None, None, None, "pass",
            ))
        rows.append(comparison_row(
            "W6", "attenuation_pond_presence", ["plan"],
            {"count": len(ponds)}, None, None, None,
            "pass" if ponds else "information",
            note="attenuation ponds — information when none drawn at PD",
        ))

    # Report storm pipe table vs plan diameters (W6 report table)
    table = (criteria or {}).get("storm_pipe_table") or []
    if table and storm_pipes:
        plan_by_d = defaultdict(float)
        for p in storm_pipes:
            if p.get("diameter_mm") is not None and p.get("length_m") is not None:
                key = int(round(p["diameter_mm"]))
                plan_by_d[key] += float(p["length_m"])
        for row in table:
            d = int(round(row["diameter_mm"]))
            rep = float(row["length_m"])
            pl = plan_by_d.get(d)
            if pl is None:
                continue
            pct = abs(rep - pl) / max(abs(rep), abs(pl), 1e-12)
            flag = pct > params["report_pct"]
            rows.append(comparison_row(
                "W6", f"report_table:D{d}", ["report", "plan"],
                {"report_m": rep, "plan_m": pl, "pct": round(pct, 6)},
                "m", abs(rep - pl), pct, "flag" if flag else "pass",
                severity="Minor" if flag else None,
                status="Probable" if flag else None,
            ))


def validate_candidates(findings):
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        return None
    schema = json.loads(FINDING_SCHEMA.read_text(encoding="utf-8"))
    v = Draft202012Validator(schema)
    errors = []
    for i, f in enumerate(findings):
        # report_only extras are allowed via additionalProperties false on finding —
        # strip report_only/note for schema check if needed
        slim = {k: val for k, val in f.items() if k not in ("report_only", "note")}
        # location may have extra keys; keep required ones
        for e in v.iter_errors(slim):
            errors.append(f"[{i}] {e.message}")
    return errors


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--networks", required=True)
    ap.add_argument("--out-consistency", required=True)
    ap.add_argument("--out-candidates", required=True)
    ap.add_argument("--criteria")
    ap.add_argument("--plan")
    ap.add_argument("--presence")
    ap.add_argument("--params")
    ap.add_argument("--site", default="NMAR")
    ap.add_argument("--sub", default="SYN")
    ap.add_argument("--run")
    ap.add_argument("--stage", default="30% Concept Design")
    ap.add_argument("--doc")
    a = ap.parse_args(argv)

    def run():
        params = load_params(a.params or PARAMS_PATH)
        networks = read_json(a.networks)
        criteria = read_json(a.criteria) if a.criteria else None
        plan_doc = read_json(a.plan) if a.plan else None
        presence = read_json(a.presence) if a.presence else None
        # Merge plan into networks for W2 if separate
        if plan_doc and not networks.get("plan"):
            networks = dict(networks)
            networks["plan"] = []
            for p in plan_doc.get("pipes") or []:
                networks["plan"].append({**p, "source": "plan"})
            networks["plan_nodes"] = plan_doc.get("nodes") or []
            networks["fixtures"] = list(networks.get("fixtures") or []) + list(
                plan_doc.get("fixtures") or []
            )
        status_cap = None
        if (networks.get("meta") or {}).get("status_cap") == "Probable":
            status_cap = "Probable"
        if plan_doc and plan_doc.get("status_cap") == "Probable":
            status_cap = "Probable"
        site = a.site.upper()
        run_id = a.run or f"{site}-{a.sub}-19700101T0000"
        meta = {"site": site, "sub": a.sub, "run": run_id, "doc": a.doc}
        rows, findings = [], []
        run_w1(presence, networks, a.stage, params, rows, findings, meta, status_cap)
        run_w2(networks, criteria, params, rows, findings, meta, status_cap)
        if (networks.get("network") in (None, "sewer", "force_main")
                or any(c.get("network") == "sewer" for c in networks.get("conduits") or [])):
            run_w3(networks, criteria, params, rows, findings, meta, status_cap)
        run_w5_geom(networks, plan_doc, params, rows, findings, meta, status_cap)
        run_w6(networks, criteria, plan_doc, params, rows, findings, meta, status_cap)

        cons = {
            "consistency_version": "1.0",
            "comparisons": rows,
            "findings": findings,
            "counts": {
                "comparisons": len(rows),
                "findings": len(findings),
                "by_check": {},
            },
            "meta": {
                "marking": (
                    "report only (DQ-11); phase A = W1/W2/W3/W5-geometry/W6-plan; "
                    "agent WUT; owner DMR"
                ),
                "site": site,
                "sub": a.sub,
                "run": run_id,
                "stage": a.stage,
                "checker": f"check_wet/{__version__}",
                "status_cap": status_cap,
            },
        }
        for r in rows:
            cons["counts"]["by_check"].setdefault(r["check"], 0)
            cons["counts"]["by_check"][r["check"]] += 1

        cand = {
            "candidates_version": "1.0",
            "stage": "candidate",
            "agent": "WUT",
            "findings": findings,
            "counts": {"findings": len(findings)},
            "meta": cons["meta"],
        }

        # Validate findings against finding.schema.json (without report_only extras)
        errs = validate_candidates(findings)
        if errs:
            return fail(EXIT_SCHEMA, "candidates failed finding.schema.json", errors=errs[:30])

        try:
            from jsonschema import Draft202012Validator
            for path, doc in ((CONS_SCHEMA, cons), (CAND_SCHEMA, cand)):
                schema = json.loads(path.read_text(encoding="utf-8"))
                es = list(Draft202012Validator(schema).iter_errors(doc))
                if es:
                    return fail(EXIT_SCHEMA, f"{path.name} failed",
                                errors=[e.message for e in es[:20]])
        except ImportError:
            pass

        atomic_write_json(a.out_consistency, cons)
        atomic_write_json(a.out_candidates, cand)
        emit({
            "status": "ok", "code": EXIT_OK,
            "consistency": str(a.out_consistency),
            "candidates": str(a.out_candidates),
            "findings": len(findings),
            "comparisons": len(rows),
            "by_check": cons["counts"]["by_check"],
        })
        return EXIT_OK

    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
