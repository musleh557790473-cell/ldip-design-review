"""Tests for skill 8 applying-ldip-rulebook (SPC-0006 §4.5.5). Synthetic rulebooks only.
T1 on the real rules_v0.json runs only when LDIP_REAL_RULEBOOK points to a local copy (never in Git)."""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SK = REPO / "plugin/ldip-design-review/skills/applying-ldip-rulebook"
PY = sys.executable


def rule(rid, state, params=None, code_lists=None, agent=None, default=None, reason="Synthetic reason text."):
    grp = rid.split("-")[0]
    return {"rule_id": rid, "title": f"Synthetic {rid}", "group": grp, "agent": agent or ("INT" if grp == "PKG" else "GIS"),
            "skill": "controlling-submittal-package" if grp == "PKG" else "checking-cadastral-geometry",
            "owner_role": "SBM" if grp == "PKG" else "Lead GIS", "statement": "X shall be Y.", "inputs": ["dlus"],
            "method": "compare", "parameters": params or {}, "code_lists": code_lists or [],
            "severity_default": "Minor", "issue_type": "inconsistency",
            "default_status": default or {"xlsx_cell": "Confirmed", "pdf_ocr": "Probable"},
            "state": state, "state_reason": reason, "tag": "AGREED"}


def book(status="approved"):
    return {
        "rulebook_schema_version": "1.0", "rulebook_version": "v9", "spec": "AICC-BIM-SPC-0004", "status": status,
        "issued": "2026-09-30", "approved_by": "synthetic",
        "sources": [{"source_id": "SRC-01", "title": "Approved source", "status": "approved"},
                    {"source_id": "SRC-02", "title": "Unknown source", "status": "unknown"}],
        "code_lists": {"section": {"source_id": "SRC-01", "clause": "§1", "values": ["AA", "BB"], "tag": "AGREED"},
                       "draft_list": {"source_id": "SRC-02", "clause": "§2", "values": ["ZZ"], "tag": "PROPOSED"}},
        "rules": [
            rule("PKG-001", "active", {"n": {"value": 10, "tag": "AGREED", "source_id": "SRC-01", "clause": "§1", "blocking": False}},
                 code_lists=["section"]),
            rule("GIS-001", "active", {"soft": {"value": None, "tag": "GAP", "blocking": False}}),
            rule("GIS-008", "advisory", {"buf": {"value": 25, "tag": "PROPOSED", "source_id": "SRC-02", "clause": "§2", "blocking": False}}),
            rule("GIS-004", "inactive", {"map": {"value": None, "tag": "GAP", "blocking": True}}, reason="Needs the layer mapping."),
        ],
    }


def write(tmp_path, obj, name="rules.json"):
    p = tmp_path / name
    p.write_text(json.dumps(obj))
    return p


def run(script, *args):
    p = subprocess.run([PY, str(SK / "scripts" / script), *map(str, args)], capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def test_load_valid(tmp_path):
    rc, out = run("load_rules.py", write(tmp_path, book()))
    assert rc == 0 and out["states"] == {"active": 2, "advisory": 1, "inactive": 1}


def test_t4_draft_refused(tmp_path):
    p = write(tmp_path, book("draft"))
    assert run("load_rules.py", p)[0] == 5
    assert run("get_rules.py", "--rulebook", p, "--agent", "GIS")[0] == 5
    assert run("load_rules.py", p, "--allow-draft")[0] == 0


def m_blocking_gap_active(b):
    b["rules"][3]["state"] = "active"


def m_proposed_in_active(b):
    b["rules"][0]["parameters"]["n"]["tag"] = "PROPOSED"


def m_unapproved_codelist_active(b):
    b["rules"][0]["code_lists"] = ["draft_list"]


def m_nongap_without_value(b):
    b["rules"][0]["parameters"]["n"]["value"] = None


def m_colour_confirmed(b):
    b["rules"][0]["default_status"]["pdf_colour"] = "Confirmed"


def m_duplicate_id(b):
    b["rules"].append(copy.deepcopy(b["rules"][0]))


def m_bad_id_pattern(b):
    b["rules"][0]["rule_id"] = "PKG-1"


def m_gap_with_value(b):
    b["rules"][1]["parameters"]["soft"]["value"] = 3


def m_group_mismatch(b):
    b["rules"][0]["rule_id"] = "GIS-099"


def m_unknown_source(b):
    b["rules"][0]["parameters"]["n"]["source_id"] = "SRC-77"


def m_unknown_code_list(b):
    b["rules"][0]["code_lists"] = ["nope"]


MUTATIONS = [  # (mutation, expected fragment) — first eight = SPC-0004 §8.3
    (m_blocking_gap_active, "L1 GIS-004"), (m_proposed_in_active, "L2 PKG-001.n: PROPOSED"),
    (m_unapproved_codelist_active, "code list draft_list not approved"), (m_nongap_without_value, "schema"),
    (m_colour_confirmed, "schema"), (m_duplicate_id, "duplicate rule_id"), (m_bad_id_pattern, "schema"),
    (m_gap_with_value, "schema"), (m_group_mismatch, "does not match group"), (m_unknown_source, "unknown source SRC-77"),
    (m_unknown_code_list, "unknown code list nope"),
]


@pytest.mark.parametrize("mut,frag", MUTATIONS, ids=[m.__name__ for m, _ in MUTATIONS])
def test_t2_mutations_refused_for_intended_reason(tmp_path, mut, frag):
    b = book()
    mut(b)
    rc, out = run("load_rules.py", write(tmp_path, b))
    assert rc == 5 and any(frag in e for e in out["errors"]), out["errors"]


def test_get_rules_caps_and_skips(tmp_path):
    p = write(tmp_path, book())
    rc, out = run("get_rules.py", "--rulebook", p, "--agent", "GIS")
    assert rc == 0
    caps = {r["rule_id"]: r["cap"] for r in out["rules"]}
    assert caps == {"GIS-001": "Confirmed", "GIS-008": "Probable"}
    assert out["rules_skipped"] == [{"rule_id": "GIS-004", "reason": "inactive: Needs the layer mapping."}]
    assert [r for r in out["rules"] if r["rule_id"] == "GIS-001"][0]["skip_subchecks_for"] == ["soft"]
    rc, out = run("get_rules.py", "--rulebook", p, "--agent", "GIS", "--states", "active")
    assert [r["rule_id"] for r in out["rules"]] == ["GIS-001"]
    assert run("get_rules.py", "--rulebook", p, "--agent", "GIS", "--states", "inactive")[0] == 2
    o = tmp_path / "evidence" / "int" / "out.json"          # parent folder created by the script
    rc, res = run("get_rules.py", "--rulebook", p, "--agent", "INT", "--out", o)
    assert rc == 0 and json.loads(o.read_text())["rules"][0]["code_lists"] == {"section": ["AA", "BB"]}


def test_lookup(tmp_path):
    p = write(tmp_path, book())
    assert run("lookup_code.py", "--rulebook", p, "--list", "section", "--value", "AA")[1]["found"] is True
    assert run("lookup_code.py", "--rulebook", p, "--list", "section", "--value", "QQ")[1]["found"] is False
    assert run("lookup_code.py", "--rulebook", p, "--param", "PKG-001.n")[1]["value"] == 10
    assert run("lookup_code.py", "--rulebook", p, "--list", "nope", "--value", "AA")[0] == 2
    assert run("lookup_code.py", "--rulebook", p, "--param", "PKG-999.n")[0] == 2


def finding(rule_id, status, stage="candidate"):
    f = {"stage": stage, "reference": {"rule_id": rule_id, "rulebook_version": "v9"}}
    if stage == "candidate":
        f["proposed_status"] = status
    else:
        f["verification"] = {"status": status}
    return f


@pytest.mark.parametrize("fs,ok", [
    ([finding("GIS-001", "Confirmed")], True),
    ([finding("GIS-008", "Probable")], True),
    ([finding("GIS-008", "Confirmed")], False),                        # T3
    ([finding("GIS-008", "Missing information", "verified")], False),
    ([finding("GIS-004", "Probable")], False),
    ([finding("GIS-555", "Probable")], False),
    ([finding(None, "Confirmed", "verified")], False),
    ([dict(finding("GIS-001", "Confirmed"), reference={"rule_id": "GIS-001", "rulebook_version": "v1"})], False),
])
def test_check_cap(tmp_path, fs, ok):
    rc, out = run("check_cap.py", "--findings", write(tmp_path, fs, "f.json"), "--rulebook", write(tmp_path, book()))
    assert rc == 0 and (out["result"] == "pass") == ok


@pytest.mark.skipif(not os.environ.get("LDIP_REAL_RULEBOOK"), reason="real rulebook is local-only (brief §10.2)")
def test_t1_real_rules_v0():
    rc, out = run("load_rules.py", os.environ["LDIP_REAL_RULEBOOK"])
    assert rc == 0 and out["states"] == {"active": 15, "advisory": 5, "inactive": 9}


def test_skill_frontmatter_best_practice():
    import yaml
    text = (SK / "SKILL.md").read_text()
    fm = yaml.safe_load(text.split("---")[1])
    assert fm["name"] == "applying-ldip-rulebook" and len(fm["name"]) <= 64
    assert fm["description"].startswith("LDIP design review (AICC JV):") and len(fm["description"]) <= 1024
    assert "claude" not in fm["name"] and "anthropic" not in fm["name"]
    assert len(text.splitlines()) < 500
