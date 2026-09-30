"""Core of skill 8 `applying-ldip-rulebook` (SPC-0006 Rev B §4.5; SPC-0004 Rev C §3–§4).

One implementation, used by the skill's command-line scripts and by the plugin's
format_check.py (FC5), so the rule-state cap is never coded twice (owner, 30 Sep 2026).

The module never edits a rulebook or a value (SPC-0004 §4.4). No network, no shell calls.
"""
__version__ = "0.1.0"

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
def rules_for_agent(book, agent, states=("active", "advisory"), skill=None):
    """Rules an agent may run, each with its cap; inactive rules returned separately for rules_skipped."""
    run, skipped = [], []
    for r in sorted(book["rules"], key=lambda x: x["rule_id"]):
        if r["agent"] != agent or (skill and r["skill"] != skill):
            continue
        if r["state"] == "inactive":
            skipped.append({"rule_id": r["rule_id"], "reason": f"inactive: {r['state_reason']}"})
            continue
        if r["state"] not in states:
            continue
        gaps = sorted(n for n, p in r["parameters"].items() if p["tag"] == "GAP")
        run.append({
            "rule_id": r["rule_id"], "title": r["title"], "skill": r["skill"], "state": r["state"],
            "cap": CAP[r["state"]], "statement": r["statement"], "method": r["method"], "inputs": r["inputs"],
            "parameters": r["parameters"], "skip_subchecks_for": gaps,
            "code_lists": {c: book["code_lists"][c]["values"] for c in r.get("code_lists", [])},
            "severity_default": r["severity_default"], "issue_type": r["issue_type"],
            "default_status": r["default_status"], "exclusions": r.get("exclusions", []),
            "owner_role": r["owner_role"],
        })
    return {"rulebook_version": book["rulebook_version"], "agent": agent, "rules": run, "rules_skipped": skipped}


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
            return {"rule_id": rule_id, "parameter": name, **r["parameters"][name], "rule_state": r["state"]}
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
        if st and cap is not None and STATUS_RANK.get(st, 0) > cap:
            errs.append({"index": i, "path": f"/{i}/{spath}", "message": f"status '{st}' exceeds the cap of {state} rule {rid}"})
    return errs
