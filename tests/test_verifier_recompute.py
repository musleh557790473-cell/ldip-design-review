"""P-S3-43 (value match by type) and P-S3-24 (rule recomputation) in skill 18 recheck.py.
Synthetic workbook and rulebook only (owner approved 5 Oct 2026)."""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_cadastral_skill as sk9          # noqa: E402
import test_rulebook_skill as sk8           # noqa: E402
import test_verifier_skill as sk18          # noqa: E402

sys.path.insert(0, str(sk18.SC))
import verify_common as vc                   # noqa: E402


@pytest.mark.parametrize("quoted,cell,ok", [
    ("1", "1", True),
    ("1/1", "1", False),                        # composite / ratio is text, never the number 1 (P-S3-43)
    ("1,032,067.36 m2", "1032067.356", True),   # unit allowed, printed precision
    ("1,032,067.36 m2", "1032067.37", False),
    ("12a", "12", False),
    ("5 kg", "5", False),                       # unknown unit → text
    ("G+2", "g+2", True),
    ("G+2", "2", False),
    ("-", None, True), ("-", "0", True), ("-", "3", False),
    ("12.5%", "0.125", True), ("12.5%", "0.126", False),
    ("3.57095e+06", "3570946.73", True),
    ("Villa  - Duplex", "Villa - Duplex", True),
])
def test_value_match_by_type(quoted, cell, ok):
    assert vc.values_agree(quoted, cell) is ok


def full_rulebook(tmp_path, tol=1.0):
    b = sk8.book()

    def p(value, unit=None):                  # parameter citing the synthetic book's approved source
        return dict(sk9.param(value, unit), source_id="SRC-01")
    params = {"GIS-012": {"area_tol": p(tol, "m2")}, "GIS-013": {"tol": p(tol, "m2"), "fixed_villa_gfa": p(360.0, "m2")},
              "GIS-014": {"tol_persons": p(0.05), "tol_spaces": p(0.01)}, "GIS-015": {"tol_rel": p(0.5, "%")}}
    rules = []
    for rid in ["GIS-001", "GIS-002", "GIS-003", "GIS-012", "GIS-013", "GIS-014", "GIS-015", "GIS-008", "GIS-004", "PKG-001"]:
        r = sk8.rule(rid, "active", params.get(rid, {}), default={"xlsx_cell": "Confirmed", "csv_cell": "Confirmed",
                                                                   "script_output": "Confirmed"})
        rules.append(r)
    b["rules"] = rules
    b["code_lists"] = {}
    return sk8.write(tmp_path, b, f"rules_tol{int(tol)}.json")


def recheck(tmp_path, cand, rb):
    out = tmp_path / "rc.json"
    rc, s = sk18.run("recheck.py", "--candidates", cand, "--root", tmp_path, "--out", out, "--rulebook", rb)
    assert rc == 0, s
    return json.loads(out.read_text())["results"]


def by_rule(cand_file):
    return {f["finding_id"]: f["reference"]["rule_id"] for f in json.loads(Path(cand_file).read_text())}


def test_recompute_confirms_breaches(tmp_path):
    wb = sk9.build_workbook(tmp_path / "s.xlsx")
    cand, _ = sk18.candidates(tmp_path, wb)
    res = recheck(tmp_path, cand, full_rulebook(tmp_path))
    rules = by_rule(cand)
    assert {r["recheck_result"] for r in res.values()} == {"agrees"}
    recomputed = {rules[k] for k, r in res.items() if "recomputed on" in r["method"]}
    assert {"GIS-013", "GIS-014"} <= recomputed
    for k, r in res.items():
        if rules[k] in ("GIS-013", "GIS-014"):
            rec = [c for c in r["compared"] if "recompute" in c][0]["recompute"]
            assert rec and all(x["breach"] for x in rec)


def test_recompute_without_breach_disagrees(tmp_path):
    """Same cells, but the approved tolerance does not support the breach → disagrees (Probable)."""
    wb = sk9.build_workbook(tmp_path / "s.xlsx")
    cand, _ = sk18.candidates(tmp_path, wb)
    res = recheck(tmp_path, cand, full_rulebook(tmp_path, tol=100000.0))
    rules = by_rule(cand)
    gis013 = [r for k, r in res.items() if rules[k] == "GIS-013" and "recomputed" in r["method"]]
    assert gis013 and all(r["recheck_result"] == "disagrees" and "show no breach" in r["method"] for r in gis013)
    # rules with no formula keep the value re-read only
    assert all(r["recheck_result"] == "agrees" for k, r in res.items() if rules[k] in ("GIS-001", "GIS-003"))


def test_old_composite_quote_disagrees(tmp_path):
    """The session 7 case: a quoted 'PA M4=1/1' against a cell holding 1 no longer agrees (P-S3-43)."""
    wb = sk9.build_workbook(tmp_path / "s.xlsx")
    cand, f = sk18.candidates(tmp_path, wb)
    g14 = [x for x in f if x["reference"]["rule_id"] == "GIS-014"]
    assert g14, "synthetic workbook has a GIS-014 finding"
    ext = g14[0]["evidence"][0]["extract"]
    assert "rate PA " in ext and "per PA " in ext and "/1" not in ext       # finder quotes single cells now
    old = copy.deepcopy(g14[0])
    import re
    old["evidence"][0]["extract"] = re.sub(r"rate PA ([A-Z]+[0-9]+)=([0-9.]+); per PA [A-Z]+[0-9]+=([0-9.]+)",
                                           r"PA \1=\2/\3", ext)
    p = tmp_path / "old.json"
    p.write_text(json.dumps([old]))
    res = recheck(tmp_path, p, full_rulebook(tmp_path))
    r = list(res.values())[0]
    assert r["recheck_result"] == "disagrees"
    assert any(c.get("kind") == "text" and not c["ok"] for c in r["compared"] if "cell" in c)


def test_without_rulebook_no_recompute(tmp_path):
    wb = sk9.build_workbook(tmp_path / "s.xlsx")
    cand, _ = sk18.candidates(tmp_path, wb)
    out = tmp_path / "rc.json"
    assert sk18.run("recheck.py", "--candidates", cand, "--root", tmp_path, "--out", out)[0] == 0
    res = json.loads(out.read_text())["results"]
    assert all("not recomputed" in r["method"] for r in res.values())
