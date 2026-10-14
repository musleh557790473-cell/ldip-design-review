"""Core of skill 8 `applying-ldip-rulebook` (SPC-0006 Rev B §4.5; SPC-0004 Rev E §3–§4).

One implementation, used by the skill's command-line scripts and by the plugin's
format_check.py (FC5), so the rule-state cap is never coded twice (owner, 30 Sep 2026).

The module never edits a rulebook or a value (SPC-0004 §4.4). No network, no shell calls.
"""
__version__ = "0.2.0"
# 0.2.0 (SPC-0004 Rev E, I11): schema 1.1 (ROD / EWK / COR rules, applicability, keyed and advisory parameters,
# target state, tolerances, required inputs, stage map); lint L5-L12; advisory parameters capped at Probable
# (Rev E §4.7) in get_rules output and in FC5 when a finding names its parameter (SPC-0003 Rev E §4.8).

import json
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCHEMA_PATH = SKILL_DIR.parent.parent / "schemas" / "rules.schema.json"

# SPC-0006 §2.3.3
EXIT_OK, EXIT_INPUT, EXIT_DEPENDENCY, EXIT_SCHEMA, EXIT_REFUSED = 0, 2, 3, 4, 5

CAP = {"active": "Confirmed", "advisory": "Probable"}           # SPC-0004 §4.1
CAP_RANK = {"active": 3, "advisory": 2}
# Missing information ranks with Confirmed (brief §5.1); agreed by the owner, 30 Sep 2026.
STATUS_RANK = {"Confirmed": 3, "Missing information": 3, "Probable": 2, "Not auto-verifiable": 1}
APPROVED_SOURCE = ("approved", "internal_agreed")
ROAD_GROUPS = ("ROD", "EWK", "COR")
SKILL_NOT_BUILT = "skill not built"


class RulebookError(Exception):
    def __init__(self, code, detail, errors=None):
        super().__init__(detail)
        self.code, self.detail, self.errors = code, detail, errors or []


# ------------------------------------------------------------------ load + validate
def read_rulebook(path):
    p = Path(path)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RulebookError(EXIT_INPUT, f"rulebook not found: {p}") from exc
    except json.JSONDecodeError as exc:
        raise RulebookError(EXIT_INPUT, f"rulebook is not valid JSON: {exc}") from exc


def schema_errors(book, schema_path=SCHEMA_PATH):
    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError as exc:
        raise RulebookError(EXIT_DEPENDENCY, "jsonschema is not installed") from exc
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    v = Draft202012Validator(schema, format_checker=FormatChecker())
    return [f"schema: {e.json_path}: {e.message[:160]}" for e in v.iter_errors(book)]


def lint_errors(book):
    """SPC-0004 §4.5 lint (what JSON Schema cannot express). Robust to structurally bad input."""
    errs = []
    sources = {s.get("source_id"): s for s in book.get("sources", []) if isinstance(s, dict)}
    code_lists = book.get("code_lists", {}) if isinstance(book.get("code_lists"), dict) else {}
    tolerances = book.get("tolerances", {}) if isinstance(book.get("tolerances"), dict) else {}
    phases_used = set()
    for name, t in tolerances.items():
        if isinstance(t, dict) and t.get("source_id") and t["source_id"] not in sources:
            errs.append(f"L4 tolerances.{name}: unknown source {t['source_id']}")
    seen = set()
    for r in book.get("rules", []):
        if not isinstance(r, dict):
            continue
        rid = r.get("rule_id", "?")
        params = r.get("parameters", {}) if isinstance(r.get("parameters"), dict) else {}
        # L3 unique id, id matches group
        if rid in seen:
            errs.append(f"L3 {rid}: duplicate rule_id")
        seen.add(rid)
        if isinstance(rid, str) and r.get("group") and not rid.startswith(str(r["group"]) + "-"):
            errs.append(f"L3 {rid}: rule_id does not match group {r.get('group')}")
        # L4 referenced sources / code lists exist
        for name, p in params.items():
            sid = p.get("source_id") if isinstance(p, dict) else None
            if sid and sid not in sources:
                errs.append(f"L4 {rid}.{name}: unknown source {sid}")
        for ri in r.get("required_inputs", []) or []:
            if isinstance(ri, dict) and ri.get("source_id") and ri["source_id"] not in sources:
                errs.append(f"L4 {rid}.required_inputs[{ri.get('item')}]: unknown source {ri['source_id']}")
        for c in r.get("code_lists", []) or []:
            if c not in code_lists:
                errs.append(f"L4 {rid}: unknown code list {c}")
            elif code_lists[c].get("source_id") not in sources:
                errs.append(f"L4 code list {c}: unknown source {code_lists[c].get('source_id')}")
        # L1 blocking GAP -> inactive
        if any(isinstance(p, dict) and p.get("tag") == "GAP" and p.get("blocking") for p in params.values()) \
                and r.get("state") != "inactive":
            errs.append(f"L1 {rid}: blocking GAP parameter but state={r.get('state')}")
        # L2 active rules: no PROPOSED value, approved sources and code lists only
        if r.get("state") == "active":
            for name, p in params.items():
                if not isinstance(p, dict):
                    continue
                if p.get("tag") == "PROPOSED":
                    errs.append(f"L2 {rid}.{name}: PROPOSED value in an active rule")
                if p.get("tag") != "GAP":
                    st = sources.get(p.get("source_id"), {}).get("status")
                    if st not in APPROVED_SOURCE:
                        errs.append(f"L2 {rid}.{name}: source {p.get('source_id')} not approved ({st}) in an active rule")
            for c in r.get("code_lists", []) or []:
                cl = code_lists.get(c)
                if cl and (cl.get("tag") != "AGREED" or sources.get(cl.get("source_id"), {}).get("status") not in APPROVED_SOURCE):
                    errs.append(f"L2 {rid}: code list {c} not approved in an active rule")
        # state_reason required for non-active rules
        if r.get("state") != "active" and len(str(r.get("state_reason", ""))) < 10:
            errs.append(f"L1 {rid}: missing state_reason for a {r.get('state')} rule")
        errs += _lint_rev_e(r, rid, params, sources, tolerances, phases_used)
    errs += _lint_stage_map(book.get("stage_map"), phases_used)
    return errs


def _lint_rev_e(r, rid, params, sources, tolerances, phases_used):
    """SPC-0004 Rev E §4.9: L5-L11 for one rule; collects the phases used for L12."""
    errs, state, road = [], r.get("state"), r.get("group") in ROAD_GROUPS
    pdicts = {n: p for n, p in params.items() if isinstance(p, dict)}
    for name, p in pdicts.items():                                   # L5 advisory parameters AGREED only
        if p.get("enforcement") == "advisory" and p.get("tag") != "AGREED":
            errs.append(f"L5 {rid}.{name}: advisory parameter must be AGREED (is {p.get('tag')})")
    if state == "inactive" and str(r.get("state_reason", "")).lower().startswith(SKILL_NOT_BUILT) \
            and not r.get("target_state"):                           # L6
        errs.append(f"L6 {rid}: inactive ('skill not built') without target_state")
    if road:                                                          # L7
        for name, p in pdicts.items():
            if p.get("tag") != "GAP" and not p.get("clause"):
                errs.append(f"L7 {rid}.{name}: no clause")
    for name, p in pdicts.items():                                   # L8
        ref = p.get("tolerance_ref")
        if not ref:
            continue
        t = tolerances.get(ref)
        if t is None:
            errs.append(f"L8 {rid}.{name}: unknown tolerance '{ref}'")
        elif t.get("tag") in ("PROPOSED", "GAP") and state == "active":
            errs.append(f"L8 {rid}.{name}: tolerance '{ref}' is {t.get('tag')} in an active rule")
    appl = r.get("applicability") or {}
    for name, p in pdicts.items():                                   # L9
        if not p.get("keyed_by") or p.get("tag") == "GAP":
            continue
        val = p.get("value")
        if not isinstance(val, dict):
            errs.append(f"L9 {rid}.{name}: keyed_by set but value is not an object")
        elif len(p["keyed_by"]) == 1:
            want = appl.get(p["keyed_by"][0])
            if want and "all" not in want:
                missing = [w for w in want if str(w) not in {str(k) for k in val}]
                if missing:
                    errs.append(f"L9 {rid}.{name}: no value for {p['keyed_by'][0]} {missing}")
    if road and not r.get("required_inputs"):                        # L10
        errs.append(f"L10 {rid}: no required_inputs")
    if r.get("check_type") == "presence":                            # L11
        cited = [p for p in pdicts.values() if p.get("tag") != "GAP"]
        tiers = {(sources.get(p.get("source_id")) or {}).get("tier") for p in cited}
        if not tiers & {"1", "2"}:
            errs.append(f"L11 {rid}: presence rule cites no tier-1 or tier-2 source")
        elif "1" not in tiers and any(p.get("enforcement", "binding") != "advisory" for p in cited):
            errs.append(f"L11 {rid}: presence rule cites tier 2 only but is not advisory")
    for x in [r] + list(pdicts.values()) + list(r.get("required_inputs") or []):
        if isinstance(x, dict) and x.get("required_from_phase"):
            phases_used.add(x["required_from_phase"])
    return errs


def _lint_stage_map(stage_map, phases_used):
    """L12: a stage map covering every phase used; DD entries carry their MIDP milestone (Rev E §5.14A)."""
    if not phases_used:
        return []
    if not isinstance(stage_map, dict) or not stage_map:
        return [f"L12 rules use phases {sorted(phases_used)} but stage_map is missing"]
    errs = []
    mapped = {v.get("phase") for v in stage_map.values() if isinstance(v, dict)}
    errs += [f"L12 phase {ph} used but no stage_map entry maps to it" for ph in sorted(phases_used - mapped)]
    if "DD" in phases_used and any(isinstance(v, dict) and v.get("phase") == "DD" and v.get("milestone") is None
                                   for v in stage_map.values()):
        errs.append("L12 a DD stage_map entry has no milestone (Rev E §5.14A)")
    return errs


def load_rulebook(path, require_approved=True):
    """Validate (schema + lint) and return the rulebook. Refuses (exit 5) if invalid or not approved."""
    book = read_rulebook(path)
    errs = schema_errors(book) + lint_errors(book)
    if errs:
        raise RulebookError(EXIT_REFUSED, f"rulebook failed validation ({len(errs)} errors)", errs)
    if require_approved and book.get("status") != "approved":
        raise RulebookError(EXIT_REFUSED, f"rulebook status is '{book.get('status')}', not 'approved' (SPC-0005 §4.12)")
    return book


def state_counts(book):
    out = {"active": 0, "advisory": 0, "inactive": 0}
    for r in book["rules"]:
        out[r["state"]] = out.get(r["state"], 0) + 1
    return out


# ------------------------------------------------------------------ queries
def param_cap(rule_state, p):
    """Cap of one parameter's sub-check: the rule cap, lowered to Probable for an advisory parameter (Rev E §4.7)."""
    cap = CAP[rule_state]
    if isinstance(p, dict) and p.get("enforcement") == "advisory" and CAP_RANK.get("advisory", 0) < CAP_RANK[rule_state]:
        cap = CAP["advisory"]
    return cap


def _resolved_params(book, r):
    out = {}
    tol = book.get("tolerances") or {}
    for name, p in r["parameters"].items():
        q = dict(p)
        if r["state"] in CAP:
            q["cap"] = param_cap(r["state"], p)
        if p.get("tolerance_ref") in tol:
            t = tol[p["tolerance_ref"]]
            q["tolerance"] = {"value": t.get("value"), "unit": t.get("unit"), "tag": t.get("tag")}
        out[name] = q
    return out


def rules_for_agent(book, agent, states=("active", "advisory"), skill=None):
    """Rules an agent may run, each with its cap; inactive rules returned separately for rules_skipped."""
    run, skipped = [], []
    for r in sorted(book["rules"], key=lambda x: x["rule_id"]):
        if r["agent"] != agent or (skill and r["skill"] != skill):
            continue
        if r["state"] == "inactive":
            s = {"rule_id": r["rule_id"], "reason": f"inactive: {r['state_reason']}"}
            if r.get("target_state"):
                s["target_state"] = r["target_state"]
            skipped.append(s)
            continue
        if r["state"] not in states:
            continue
        gaps = sorted(n for n, p in r["parameters"].items() if p["tag"] == "GAP")
        item = {
            "rule_id": r["rule_id"], "title": r["title"], "skill": r["skill"], "state": r["state"],
            "cap": CAP[r["state"]], "statement": r["statement"], "method": r["method"], "inputs": r["inputs"],
            "parameters": _resolved_params(book, r), "skip_subchecks_for": gaps,
            "advisory_parameters": sorted(n for n, p in r["parameters"].items() if p.get("enforcement") == "advisory"),
            "code_lists": {c: book["code_lists"][c]["values"] for c in r.get("code_lists", [])},
            "severity_default": r["severity_default"], "issue_type": r["issue_type"],
            "default_status": r["default_status"], "exclusions": r.get("exclusions", []),
            "owner_role": r["owner_role"],
        }
        for k in ("check_type", "applicability", "required_inputs", "required_from_phase", "decision_ref"):
            if k in r:
                item[k] = r[k]
        run.append(item)
    res = {"rulebook_version": book["rulebook_version"], "agent": agent, "rules": run, "rules_skipped": skipped}
    if book.get("stage_map"):
        res["stage_map"] = book["stage_map"]
    return res


def lookup_code(book, list_name, value):
    cl = book["code_lists"].get(list_name)
    if cl is None:
        raise RulebookError(EXIT_INPUT, f"unknown code list '{list_name}'")
    return {"list": list_name, "value": value, "found": value in cl["values"],
            "source_id": cl["source_id"], "clause": cl["clause"], "tag": cl["tag"]}


def lookup_param(book, rule_id, name):
    for r in book["rules"]:
        if r["rule_id"] == rule_id:
            if name not in r["parameters"]:
                raise RulebookError(EXIT_INPUT, f"rule {rule_id} has no parameter '{name}'")
            p = r["parameters"][name]
            res = {"rule_id": rule_id, "parameter": name, **p, "rule_state": r["state"]}
            if r["state"] in CAP:
                res["cap"] = param_cap(r["state"], p)
            if p.get("tolerance_ref") in (book.get("tolerances") or {}):
                res["tolerance"] = book["tolerances"][p["tolerance_ref"]]
            return res
    raise RulebookError(EXIT_INPUT, f"unknown rule '{rule_id}'")


# ------------------------------------------------------------------ cap check (FC5)
def status_of(f):
    return f.get("proposed_status") if f.get("stage") == "candidate" else (f.get("verification") or {}).get("status")


def cap_errors(findings, rules_by_id, rulebook_version=None):
    """FC5 (SPC-0005 §6.3, §8): rule exists, is not inactive, status ≤ cap; rulebook version matches.
    Returns a list of {"index", "path", "message"}."""
    errs = []
    for i, f in enumerate(findings):
        if not isinstance(f, dict):
            continue
        ref = f.get("reference") or {}
        rid, st = ref.get("rule_id"), status_of(f)
        spath = "proposed_status" if f.get("stage") == "candidate" else "verification/status"
        if ref.get("rulebook_version") and rulebook_version and ref["rulebook_version"] != rulebook_version:
            errs.append({"index": i, "path": f"/{i}/reference/rulebook_version",
                         "message": f"rulebook_version '{ref['rulebook_version']}' differs from the run's '{rulebook_version}'"})
        if not rid:
            if st == "Confirmed":
                errs.append({"index": i, "path": f"/{i}/reference/rule_id", "message": "Confirmed without a rule_id (SPC-0005 §8)"})
            continue
        rule = rules_by_id.get(rid)
        if rule is None:
            errs.append({"index": i, "path": f"/{i}/reference/rule_id", "message": f"rule {rid} not in the run's rulebook"})
            continue
        state = rule.get("state")
        if state == "inactive":
            errs.append({"index": i, "path": f"/{i}/reference/rule_id",
                         "message": f"rule {rid} is inactive and shall not be run (SPC-0004 §4.1)"})
            continue
        cap = CAP_RANK.get(state)
        label = f"{state} rule {rid}"
        pname = ref.get("parameter")
        if pname:
            p = (rule.get("parameters") or {}).get(pname)
            if p is None:
                errs.append({"index": i, "path": f"/{i}/reference/parameter",
                             "message": f"rule {rid} has no parameter '{pname}'"})
                continue
            if cap is not None and p.get("enforcement") == "advisory":
                cap = min(cap, CAP_RANK["advisory"])
                label = f"advisory parameter {rid}.{pname} (Probable)"
        if st and cap is not None and STATUS_RANK.get(st, 0) > cap:
            errs.append({"index": i, "path": f"/{i}/{spath}", "message": f"status '{st}' exceeds the cap of {label}"})
    return errs
