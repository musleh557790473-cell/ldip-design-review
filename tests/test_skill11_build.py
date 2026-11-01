"""Skill 11 0.1.0 — synthetic T11-1…T11-6 (SPC-0006 Rev Q §4.19 phase A).

No real project data.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin/ldip-design-review"
SK = PLUGIN / "skills/reviewing-wet-utilities/scripts"
REF = PLUGIN / "skills/reviewing-wet-utilities/references"
TPL = PLUGIN / "skills/reviewing-wet-utilities/templates"
PY = sys.executable
FT = 0.3048


def run(script, *args):
    p = subprocess.run([PY, str(SK / script), *map(str, args)], capture_output=True, text=True)
    line = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else "{}"
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        msg = {"raw": p.stdout, "stderr": p.stderr}
    return p.returncode, msg


def write(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1), encoding="utf-8")
    return path


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def make_sqlite(path, *, alts=((1, "Base"), (2, "Alt")), conduits=None, nodes=None):
    """Synthetic Bentley-like schema in US feet (DQ-3)."""
    path = Path(path)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.executescript(
        """
        CREATE TABLE Alternatives (AlternativeID INTEGER PRIMARY KEY, Label TEXT);
        CREATE TABLE Elements (
          ElementID INTEGER PRIMARY KEY, ElementType TEXT, Label TEXT,
          X_ft REAL, Y_ft REAL, Ground_ft REAL
        );
        CREATE TABLE Links (
          ElementID INTEGER PRIMARY KEY, StartNodeID INTEGER, StopNodeID INTEGER
        );
        CREATE TABLE Physical (
          DomainElementID INTEGER, AlternativeID INTEGER,
          Diameter_ft REAL, Length_ft REAL,
          InvertStart_ft REAL, InvertStop_ft REAL,
          Material TEXT, Roughness REAL, RoughnessType TEXT,
          PRIMARY KEY (DomainElementID, AlternativeID)
        );
        """
    )
    for aid, lab in alts:
        c.execute("INSERT INTO Alternatives VALUES (?, ?)", (aid, lab))
    nodes = nodes or [
        (101, "manhole", "MH-1", 1000.0, 2000.0, 1400.0),
        (102, "manhole", "MH-2", 1100.0, 2000.0, 1398.0),
        (103, "outfall", "OF-1", 1200.0, 2000.0, 1395.0),
    ]
    for row in nodes:
        c.execute("INSERT INTO Elements VALUES (?, ?, ?, ?, ?, ?)", row)
    conduits = conduits or [
        # id, start, stop, D_ft (200 mm = 0.656168 ft), L_ft, inv_s_ft, inv_e_ft, alt
        (1, 101, 102, 200 / 1000 / FT, 50 / FT, 1390.0 / FT, 1389.75 / FT, 1),
        (1, 101, 102, 250 / 1000 / FT, 50 / FT, 1390.0 / FT, 1389.75 / FT, 2),  # alt 2 different D
        (2, 102, 103, 200 / 1000 / FT, 40 / FT, 1389.75 / FT, 1389.55 / FT, 1),
        (2, 102, 103, 200 / 1000 / FT, 40 / FT, 1389.75 / FT, 1389.55 / FT, 2),
    ]
    for eid, s, e, d, L, i0, i1, alt in conduits:
        c.execute("INSERT OR IGNORE INTO Links VALUES (?, ?, ?)", (eid, s, e))
        c.execute(
            "INSERT INTO Physical VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (eid, alt, d, L, i0, i1, "PVC", 0.013, "Manning"),
        )
    conn.commit()
    conn.close()
    return path


def make_dxf(path, texts):
    """Minimal DXF with MTEXT labels on wet layers."""
    import ezdxf
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    for layer, text, x, y in texts:
        if layer not in doc.layers:
            doc.layers.add(layer)
        msp.add_mtext(text, dxfattribs={"layer": layer, "insert": (x, y), "char_height": 0.25})
    path = Path(path)
    doc.saveas(path)
    return path


def check(tmp_path, networks, criteria=None, plan=None, presence=None, **kw):
    net_path = write(tmp_path / "networks.json", networks)
    args = [
        "--networks", net_path,
        "--out-consistency", tmp_path / "consistency.json",
        "--out-candidates", tmp_path / "candidates.json",
        "--site", kw.get("site", "NMAR"),
        "--sub", kw.get("sub", "SYN"),
        "--stage", "30% Concept Design",
    ]
    if criteria is not None:
        args += ["--criteria", write(tmp_path / "criteria.json", criteria)]
    if plan is not None:
        args += ["--plan", write(tmp_path / "plan.json", plan)]
    if presence is not None:
        args += ["--presence", write(tmp_path / "presence.json", presence)]
    rc, msg = run("check_wet.py", *args)
    assert rc == 0, msg
    cons = load(tmp_path / "consistency.json")
    cand = load(tmp_path / "candidates.json")
    Draft202012Validator(load(TPL / "consistency.schema.json")).validate(cons)
    Draft202012Validator(load(TPL / "candidates.schema.json")).validate(cand)
    return cons, cand, msg


# --------------------------------------------------------------------------- T11-1 units
def test_t11_1_reader_units_feet_to_si(tmp_path):
    """T11-1: US feet storage → length m, diameter mm; source sha256."""
    db = make_sqlite(tmp_path / "syn.stsw.sqlite")
    out = tmp_path / "networks.json"
    rc, msg = run("read_bentley_sqlite.py", "--sqlite", db, "--network", "sewer", "--out", out)
    assert rc == 0, msg
    doc = load(out)
    Draft202012Validator(load(TPL / "networks.schema.json")).validate(doc)
    assert doc["units"]["length"] == "m" and doc["units"]["diameter"] == "mm"
    assert doc["source"]["sha256"] and len(doc["source"]["sha256"]) == 64
    assert doc["summary"]["conduit_count"] == 2  # base alt only
    c0 = doc["conduits"][0]
    assert abs(c0["diameter_mm"] - 200) < 0.5
    assert abs(c0["length_m"] - 50.0) < 0.02


# --------------------------------------------------------------------------- T11-2 base alternative
def test_t11_2_base_alternative_lowest_id(tmp_path):
    """T11-2: base alternative = lowest AlternativeID (ignores alt 2 diameters)."""
    db = make_sqlite(tmp_path / "syn.stsw.sqlite")
    out = tmp_path / "networks.json"
    rc, msg = run("read_bentley_sqlite.py", "--sqlite", db, "--out", out)
    assert rc == 0, msg
    assert msg["base_alternative_id"] == 1
    assert msg["alternatives"] == [1, 2]
    doc = load(out)
    assert doc["base_alternative_id"] == 1
    assert all(c["alternative_id"] == 1 for c in doc["conduits"])
    assert all(abs(c["diameter_mm"] - 200) < 0.5 for c in doc["conduits"])


# --------------------------------------------------------------------------- T11-3 label parse
def test_t11_3_dxf_label_parse(tmp_path):
    """T11-3: node MTEXT ID/CL/IL/E/N and pipe L=/S=/Ø labels."""
    dxf = make_dxf(tmp_path / "swn.dxf", [
        ("SWN-TEXT", "MH-1 / CL=412.50 / IL=410.20 / E=1000.00 / N=2000.00", 1000, 2000),
        ("SWN-TEXT", "L=50.00 m S=0.50 % Ø200", 1050, 2000),
        ("-05- Hydrant", "FH-1", 50, 50),
    ])
    out = tmp_path / "plan.json"
    rc, msg = run("read_network_plan.py", "--dxf", dxf, "--site", "NMAR",
                  "--network", "sewer", "--out", out)
    assert rc == 0, msg
    doc = load(out)
    assert doc["counts"]["nodes"] >= 1
    node = doc["nodes"][0]
    assert node["id"] == "MH-1"
    assert abs(node["cl_m"] - 412.50) < 1e-6
    assert abs(node["il_m"] - 410.20) < 1e-6
    assert abs(node["e_m"] - 1000.0) < 1e-6
    pipes = [p for p in doc["pipes"] if p.get("length_m") is not None]
    assert pipes and abs(pipes[0]["length_m"] - 50.0) < 1e-6
    assert abs(pipes[0]["slope_pct"] - 0.50) < 1e-6
    assert abs(pipes[0]["diameter_mm"] - 200) < 1e-6
    assert any(f["kind"] == "hydrant" for f in doc["fixtures"])


# --------------------------------------------------------------------------- T11-4 W3 thresholds ±0.01
def test_t11_4_w3_gradient_threshold(tmp_path):
    """T11-4: 200 mm at 0.49 % flags; 0.50 % passes (± 0.01)."""
    # Build networks directly (metres already)
    def net(slope):
        return {
            "networks_version": "1.0",
            "network": "sewer",
            "source": {"path": "syn", "sha256": "a" * 64, "kind": "synthetic"},
            "conduits": [{
                "id": "C1", "element_id": 1, "source": "model", "network": "sewer",
                "diameter_mm": 200, "length_m": 50.0,
                "invert_start_m": 100.0,
                "invert_stop_m": 100.0 - 50.0 * slope / 100.0,
                "slope_pct": slope, "cover_m": 1.5,
                "start_node_id": 1, "stop_node_id": 2,
            }],
            "nodes": [
                {"element_id": 1, "label": "MH1", "element_type": "manhole"},
                {"element_id": 2, "label": "OF", "element_type": "outfall"},
            ],
            "summary": {"conduit_count": 1, "length_km": 0.05},
            "plan": [],
        }

    cons_ok, _, _ = check(tmp_path / "ok", net(0.50), presence={
        "networks": {
            "sewer": {"plan": True, "model": True},
            "water": {"plan": True, "model": True},
            "irrigation": {"plan": True, "model": True},
            "storm": {"plan": True, "model": True},
        },
        "hydraulic_calculations": True,
    })
    slope_rows = [r for r in cons_ok["comparisons"]
                  if r["check"] == "W3" and str(r["quantity"]).startswith("min_slope")]
    assert slope_rows and slope_rows[0]["result"] == "pass"

    cons_bad, cand, _ = check(tmp_path / "bad", net(0.49), presence={
        "networks": {
            "sewer": {"plan": True, "model": True},
            "water": {"plan": True, "model": True},
            "irrigation": {"plan": True, "model": True},
            "storm": {"plan": True, "model": True},
        },
        "hydraulic_calculations": True,
    })
    flags = [r for r in cons_bad["comparisons"]
             if r["check"] == "W3" and str(r["quantity"]).startswith("min_slope")
             and r["result"] == "flag"]
    assert flags and flags[0]["severity"] == "Major"
    finds = [f for f in cand["findings"] if "gradient" in f["title"].casefold()]
    assert finds and finds[0]["proposed_status"] == "Probable"
    assert finds[0]["agent"] == "WUT"
    assert finds[0]["reference"]["rule_id"] is None
    assert finds[0]["report_only"] is True
    assert finds[0]["owner"]["role"] == "DMR"


# --------------------------------------------------------------------------- T11-5 adverse
def test_t11_5_adverse_grade(tmp_path):
    """T11-5: rising invert toward outfall → W3 adverse Major Probable."""
    networks = {
        "networks_version": "1.0",
        "network": "sewer",
        "source": {"path": "syn", "sha256": "b" * 64, "kind": "synthetic"},
        "conduits": [{
            "id": "ADV", "element_id": 1, "source": "model",
            "diameter_mm": 200, "length_m": 40.0,
            "invert_start_m": 100.0, "invert_stop_m": 100.10,
            "slope_pct": -0.25, "cover_m": 1.5,
            "start_node_id": 1, "stop_node_id": 2,
        }],
        "nodes": [
            {"element_id": 1, "label": "MH1", "element_type": "manhole"},
            {"element_id": 2, "label": "OF", "element_type": "outfall"},
        ],
        "summary": {"conduit_count": 1, "length_km": 0.04},
        "plan": [],
    }
    cons, cand, _ = check(tmp_path, networks, presence={
        "networks": {
            "sewer": {"plan": True, "model": True},
            "water": {"plan": True, "model": True},
            "irrigation": {"plan": True, "model": True},
            "storm": {"plan": True, "model": True},
        },
        "hydraulic_calculations": True,
    })
    adv = [r for r in cons["comparisons"]
           if r["check"] == "W3" and str(r["quantity"]).startswith("adverse")]
    assert adv and adv[0]["result"] == "flag" and adv[0]["severity"] == "Major"
    assert any("adverse" in f["title"].casefold() for f in cand["findings"])


# --------------------------------------------------------------------------- T11-6 consistency tolerance edges
def test_t11_6_w2_tolerance_edges(tmp_path):
    """T11-6: L ± 0.05 m edge — 0.05 pass, 0.06 flag Confirmed."""
    def networks(delta):
        return {
            "networks_version": "1.0",
            "network": "sewer",
            "source": {"path": "syn", "sha256": "c" * 64, "kind": "synthetic"},
            "conduits": [{
                "id": "P1", "element_id": 1, "source": "model",
                "diameter_mm": 200, "length_m": 50.0,
                "invert_start_m": 100.0, "invert_stop_m": 99.75,
                "slope_pct": 0.50, "cover_m": 1.5,
                "start_node_id": 1, "stop_node_id": 2,
            }],
            "nodes": [],
            "summary": {"conduit_count": 1, "length_km": 0.05, "length_m": 50.0},
            "plan": [{
                "id": "P1", "source": "plan", "kind": "pipe_label",
                "raw": "P1 L=%.2f m S=0.50 %% Ø200" % (50.0 + delta),
                "length_m": 50.0 + delta, "slope_pct": 0.50, "diameter_mm": 200,
            }],
        }

    presence = {
        "networks": {
            "sewer": {"plan": True, "model": True},
            "water": {"plan": True, "model": True},
            "irrigation": {"plan": True, "model": True},
            "storm": {"plan": True, "model": True},
        },
        "hydraulic_calculations": True,
    }
    cons_ok, cand_ok, _ = check(tmp_path / "edge_ok", networks(0.05), presence=presence)
    L_rows = [r for r in cons_ok["comparisons"]
              if r["check"] == "W2" and str(r["quantity"]).startswith("element:L")]
    assert L_rows and L_rows[0]["result"] == "pass"
    assert not any("length label" in f["title"] for f in cand_ok["findings"])

    cons_bad, cand_bad, _ = check(tmp_path / "edge_bad", networks(0.06), presence=presence)
    L_bad = [r for r in cons_bad["comparisons"]
             if r["check"] == "W2" and str(r["quantity"]).startswith("element:L")
             and r["result"] == "flag"]
    assert L_bad
    finds = [f for f in cand_bad["findings"] if "length label" in f["title"]]
    assert finds and finds[0]["proposed_status"] == "Confirmed"
    assert finds[0]["severity"] == "Minor"
    stmt = finds[0]["statement"]
    assert "50.06" in stmt or ("50.0" in stmt and "0.06" in stmt)


def test_t11_w6_er_conflict_observations(tmp_path):
    """W6 emits DQ-15 ER storm conflict Observations even without storm geometry."""
    networks = {
        "networks_version": "1.0",
        "network": "sewer",
        "source": {"path": "syn", "sha256": "d" * 64, "kind": "synthetic"},
        "conduits": [{
            "id": "C1", "element_id": 1, "source": "model",
            "diameter_mm": 200, "length_m": 40.0,
            "invert_start_m": 100.0, "invert_stop_m": 99.8,
            "slope_pct": 0.5, "cover_m": 1.5,
            "start_node_id": 1, "stop_node_id": 2,
        }],
        "summary": {"conduit_count": 1, "length_km": 0.04},
        "plan": [],
    }
    cons, cand, _ = check(tmp_path, networks, presence={
        "networks": {
            "sewer": {"plan": True, "model": True},
            "water": {"plan": True, "model": True},
            "irrigation": {"plan": True, "model": True},
            "storm": {"plan": True, "model": True},
        },
        "hydraulic_calculations": True,
    })
    obs = [f for f in cand["findings"]
           if f["issue_type"] == "er_ambiguity" and f["severity"] == "Observation"]
    assert len(obs) >= 4
    assert all(f["reference"]["rule_id"] is None for f in obs)
    assert all(f["agent"] == "WUT" for f in cand["findings"])


def test_t11_shp_co17_cross_check(tmp_path):
    """DQ-3 SHP cross-check for label Co-17 (JSON export)."""
    nodes = [
        (101, "manhole", "Co-17", 1000.0, 2000.0, 1400.0),
        (102, "manhole", "MH-2", 1100.0, 2000.0, 1398.0),
    ]
    conduits = [
        (1, 101, 102, 200 / 1000 / FT, 50 / FT, 1390.0 / FT, 1389.75 / FT, 1),
    ]
    db = make_sqlite(tmp_path / "syn.stsw.sqlite", nodes=nodes, conduits=conduits,
                     alts=((1, "Base"),))
    # Model stores X_ft=1000 → e_m = 1000*FT; JSON export is metres (no second convert)
    shp = write(tmp_path / "co17.json", [
        {"label": "Co-17", "e": 1000.0 * FT, "n": 2000.0 * FT},
    ])
    out = tmp_path / "networks.json"
    rc, msg = run(
        "read_bentley_sqlite.py", "--sqlite", db, "--out", out,
        "--shp", shp, "--label", "Co-17",
    )
    assert rc == 0, msg
    assert msg["shp_cross_check"] == "pass"
    doc = load(out)
    assert doc["shp_cross_check"]["result"] == "pass"


def test_t11_criteria_reader(tmp_path):
    """read_wet_criteria extracts sewer gradient table + totals from skill-2 text."""
    text = {
        "pages": [{
            "page": 12,
            "text": (
                "Sewer network design criteria\n"
                "Minimum diameter 200 mm\n"
                "200 mm 0.50 % 0.75 m/s\n"
                "300 mm 0.35 % 0.75 m/s\n"
                "Total network length 9.70 km with 173 manholes\n"
                "Depth 1.40 – 3.50 m\n"
                "Storm water drainage\n"
                "Return period 10-year design storm\n"
                "300 mm 5.91 km\n"
            ),
        }],
    }
    src = write(tmp_path / "report.pdf.json", text)
    out = tmp_path / "criteria.json"
    rc, msg = run("read_wet_criteria.py", "--text", src, "--out", out)
    assert rc == 0, msg
    doc = load(out)
    Draft202012Validator(load(TPL / "criteria.schema.json")).validate(doc)
    assert doc["criteria"]["sewer"].get("min_diameter_mm", {}).get("value") == 200
    assert len(doc["gradient_tables"]["sewer"]) >= 2
    assert doc["declared_totals"]["total_length_km"]["value"] == 9.70
    assert doc["storm_pipe_table"]


def test_t11_finding_schema_accepts_wut():
    """finding.schema.json agent enum + finding_id pattern include WUT."""
    s = json.loads((PLUGIN / "schemas/finding.schema.json").read_text(encoding="utf-8"))
    assert "WUT" in s["properties"]["agent"]["enum"]
    assert "WUT" in s["properties"]["finding_id"]["pattern"]
    from jsonschema import Draft202012Validator
    finding = {
        "schema_version": "1.0",
        "finding_id": "NMAR-SYN-19700101T0000/WUT-001",
        "stage": "candidate",
        "run_id": "NMAR-SYN-19700101T0000",
        "site": "NMAR",
        "sub_id": "SYN",
        "agent": "WUT",
        "title": "Synthetic WUT finding title ok",
        "statement": "Synthetic statement long enough for schema validation.",
        "issue_type": "design_error",
        "location": {"document_no": "SWN-MOD-00001", "package_level": True},
        "severity": "Major",
        "confidence": "Medium",
        "evidence": [{
            "evidence_id": "E01", "tier": "deterministic",
            "source_type": "script_output", "extract": "test",
        }],
        "reference": {"rule_id": None, "source_doc": "ER", "clause": "W3"},
        "proposed_status": "Probable",
        "owner": {"role": "DMR", "name": None},
        "created_at": "2026-11-01T00:00:00Z",
    }
    Draft202012Validator(s).validate(finding)


def test_t11_w1_missing_storm_model(tmp_path):
    """W1 / DQ-8: missing storm model → missing_information."""
    networks = {
        "networks_version": "1.0",
        "network": "sewer",
        "source": {"path": "syn", "sha256": "e" * 64, "kind": "synthetic"},
        "conduits": [],
        "summary": {"conduit_count": 0, "length_km": 0},
        "plan": [],
    }
    cons, cand, _ = check(tmp_path, networks, presence={
        "networks": {
            "sewer": {"plan": True, "model": True},
            "water": {"plan": True, "model": True},
            "irrigation": {"plan": True, "model": True},
            "storm": {"plan": True, "model": False},
        },
        "hydraulic_calculations": False,
    })
    storm = [f for f in cand["findings"] if "storm" in f["title"].casefold() and "model" in f["title"].casefold()]
    assert storm and storm[0]["issue_type"] == "missing_information"
    assert storm[0]["severity"] == "Minor"
    calc = [f for f in cand["findings"] if "hydraulic calculation" in f["title"].casefold()]
    assert calc


def test_skill_md_under_500_and_frontmatter():
    text = (SK.parent / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert len(text.splitlines()) < 500
    assert 'name: reviewing-wet-utilities' in text
    assert 'version: "0.1.0"' in text
    assert "WUT" in text
