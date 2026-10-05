"""Rule-level recomputation for the GIS schedule rules (P-S3-24, owner approved 5 Oct 2026).

The Verifier re-reads every operand cell with its own reader (recheck.py) and recomputes the rule
formula with the tolerance from the approved rulebook. A sample whose recomputation shows no breach
makes the finding `disagrees`. Operands are named by role in the evidence extract ('role CELL=value',
'role=value', or a DLUS aggregate 'dlus_sum DLUS sum(D4:D878 if C=<Level 2>)=…').

  GIS-013  area, far, gfa        |gfa − area × far| > tol
           gfa, units (villa)    |gfa − units × fixed_villa_gfa| > tol
           gfa, res|com|cf       |gfa − (res + com + cf)| > tol
  GIS-014  residents|parking_req, units, rate, per    |field − units × rate / per| > tol_persons | tol_spaces
  GIS-012  area + dlus_sum       |area − Σ| > area_tol      plots + dlus_count   plots ≠ count
           dlus_sum / dlus_count only (Level 2 missing from the Summary)   Σ > area_tol or count > 0
           A labelled reconciliation line is not re-evaluated (stated in the method).
Other rules and patterns: values are re-read only (no formula to recompute). No network, no shell calls."""
__version__ = "0.1.0"

import re

ROLE = re.compile(r"(?:^|;\s*)(?P<role>[a-z_]+)\s+(?:(?P<prefix>Overall Summary|Summary|DLUS|PA|Planning Assumptions|"
                  r"Community Facilities)\s+)?(?P<cell>[A-Z]{1,3}[0-9]{1,7})=(?P<val>[^;|()]+)")
CONST = re.compile(r"(?:^|;\s*)(?P<role>[a-z_]+)=(?P<val>[-+0-9.,eE]+)\s*(?=;|$)")
AGG = re.compile(r"(?P<role>dlus_sum|dlus_count) DLUS (?P<fn>sum|count)\((?P<col>[A-Z]{1,3})(?P<r0>[0-9]+):[A-Z]{1,3}(?P<r1>[0-9]+)"
                 r" (?:if (?P<kcol>[A-Z]{1,3})=|= )(?P<key>[^)]+)\)=(?P<val>[^;|]+)")
PREFIX = {"summary": "Overall Summary", "overall summary": "Overall Summary", "dlus": "DLUS", "pa": "Planning Assumptions",
          "planning assumptions": "Planning Assumptions", "community facilities": "Community Facilities"}


def parse_sample(seg, default_sheet):
    ops, consts, aggs = {}, {}, []
    for m in ROLE.finditer(seg):
        ops[m.group("role")] = {"sheet": PREFIX.get((m.group("prefix") or "").lower(), default_sheet), "cell": m.group("cell")}
    for m in CONST.finditer(seg):
        consts[m.group("role")] = m.group("val")
    for m in AGG.finditer(seg):
        kcol = m.group("kcol") or m.group("col")
        aggs.append({"role": m.group("role"), "fn": m.group("fn"), "col": m.group("col"), "kcol": kcol,
                     "r0": int(m.group("r0")), "r1": int(m.group("r1")), "key": m.group("key").strip()})
    return ops, consts, aggs


def needed_cells(samples):
    """{sheet: set(cells)} to re-read for every sample."""
    out = {}
    for ops, _, aggs in samples:
        for o in ops.values():
            out.setdefault(o["sheet"], set()).add(o["cell"])
        for a in aggs:
            for r in range(a["r0"], a["r1"] + 1):
                out.setdefault("DLUS", set()).update({f"{a['col']}{r}", f"{a['kcol']}{r}"})
    return out


def _p(rules, rid, name):
    r = rules.get(rid) or {}
    p = (r.get("parameters") or {}).get(name) or {}
    return None if p.get("tag") == "GAP" else p.get("value")


def recompute_sample(rule_id, ops, consts, aggs, val, num, rules):
    """(breach True/False, detail) or None when this sample has no recomputable formula."""
    def v(role):
        if role in ops:
            return num(val(ops[role]["sheet"], ops[role]["cell"]))
        if role in consts:
            return num(consts[role])
        return None

    def agg(role):
        a = next((x for x in aggs if x["role"] == role), None)
        if a is None:
            return None
        key = " ".join(a["key"].split()).casefold()
        rows = [r for r in range(a["r0"], a["r1"] + 1)
                if " ".join(str(val("DLUS", f"{a['kcol']}{r}") or "").split()).casefold() == key]
        if a["fn"] == "count":
            return float(len(rows))
        return sum(num(val("DLUS", f"{a['col']}{r}")) or 0.0 for r in rows)

    if rule_id == "GIS-013":
        tol = _p(rules, "GIS-013", "tol")
        gfa = v("gfa")
        if tol is None or gfa is None:
            return None
        if {"area", "far"} <= set(ops) | set(consts):
            area, far = v("area"), v("far")
            if area is None or far is None:
                return None
            exp = area * far
            return abs(gfa - exp) > tol, f"gfa {gfa:g} vs area × far {exp:g} (tol {tol:g})"
        parts = [r for r in ("res", "com", "cf") if r in ops or r in consts]
        if parts:
            exp = sum(v(r) or 0.0 for r in parts)
            return abs(gfa - exp) > tol, f"gfa {gfa:g} vs breakdown {exp:g} (tol {tol:g})"
        vg = _p(rules, "GIS-013", "fixed_villa_gfa")
        units = v("units")
        if vg is not None and units is not None:
            exp = units * vg
            return abs(gfa - exp) > tol, f"gfa {gfa:g} vs units × {vg:g} = {exp:g} (tol {tol:g})"
        return None
    if rule_id == "GIS-014":
        field = next((f for f in ("residents", "parking_req") if f in ops or f in consts), None)
        tol = _p(rules, "GIS-014", "tol_persons" if field == "residents" else "tol_spaces")
        if field is None or tol is None:
            return None
        fv, units, rate, per = v(field), v("units"), v("rate"), v("per")
        if None in (fv, units, rate, per) or not per:
            return None
        exp = units * rate / per
        return abs(fv - exp) > tol, f"{field} {fv:g} vs units × rate / per = {exp:g} (tol {tol:g})"
    if rule_id == "GIS-012":
        tol = _p(rules, "GIS-012", "area_tol")
        s, c = agg("dlus_sum"), agg("dlus_count")
        if "area" in ops and s is not None and tol is not None:
            a = v("area")
            return (a is not None and abs(a - s) > tol), f"summary area {a} vs DLUS Σ {s:,.2f} (tol {tol:g})"
        if "plots" in ops and c is not None:
            p = v("plots")
            return (p is not None and int(round(p)) != int(c)), f"summary plots {p} vs DLUS count {int(c)}"
        if s is not None and tol is not None and not ({"area", "plots"} & set(ops)):
            return (s > tol or (c or 0) > 0), f"Level 2 absent from the Summary; DLUS Σ {s:,.2f}, count {int(c or 0)}"
        return None
    return None
