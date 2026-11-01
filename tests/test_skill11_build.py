"""Skill 11 0.1.0 — synthetic T11-1…T11-6 (SPC-0006 Rev Q §4.19 phase A).

No real project data. Synthetic fixture uses the real OpenFlows table/column names
(HMIModelingElement / Conduit / …), not invented Elements/Physical (R1).
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
FT_MM = 304.8


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


def make_sqlite(path, *, alts=((1, "Base"), (2, "Alt")), conduits=None, nodes=None,
                network="sewer"):
    """Synthetic OpenFlows schema in US feet (DQ-3 / R1 real table names)."""
    path = Path(path)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.executescript(
        """
        CREATE TABLE Alternatives (AlternativeID INTEGER PRIMARY KEY, Label TEXT);
        CREATE TABLE HMIModelingElement (
          ElementID INTEGER PRIMARY KEY, Label TEXT, IsDeleted INTEGER DEFAULT 0
        );
        CREATE TABLE Conduit (DomainElementID INTEGER PRIMARY KEY);
        CREATE TABLE Conduit_Physical_Data (
          DomainElementID INTEGER, AlternativeID INTEGER,
          ConduitDiameter REAL,
          PRIMARY KEY (DomainElementID, AlternativeID)
        );
        CREATE TABLE PhysicalLink_Physical_Data (
          DomainElementID INTEGER, AlternativeID INTEGER,
          Physical_UpstreamInvert REAL, Physical_DownstreamInvert REAL,
          PRIMARY KEY (DomainElementID, AlternativeID)
        );
        CREATE TABLE BaseLink_HMIDataSetTopology_Data (
          DomainElementID INTEGER PRIMARY KEY,
          HMITopologyStartNodeID INTEGER, HMITopologyStopNodeID INTEGER
        );
        CREATE TABLE BaseLink_HmiDataSetGeometry_Data (
          DomainElementID INTEGER, AlternativeID INTEGER,
          HMIGeometryScaledLength REAL,
          PRIMARY KEY (DomainElementID, AlternativeID)
        );
        CREATE TABLE GravityLinkBase_Physical_Data (
          DomainElementID INTEGER, AlternativeID INTEGER,
          Physical_Material TEXT, Physical_ManningsN REAL,
          PRIMARY KEY (DomainElementID, AlternativeID)
        );
        CREATE TABLE BaseNode_Physical_Data (
          DomainElementID INTEGER, AlternativeID INTEGER,
          Physical_GroundElevation REAL,
          PRIMARY KEY (DomainElementID, AlternativeID)
        );
        CREATE TABLE BaseDirectedNode_Physical_Data (
          DomainElementID INTEGER, AlternativeID INTEGER,
          Physical_GroundElevation REAL,
          PRIMARY KEY (DomainElementID, AlternativeID)
        );
        CREATE TABLE IdahoPipe (DomainElementID INTEGER PRIMARY KEY);
        CREATE TABLE IdahoPipe_Physical_Data (
          DomainElementID INTEGER, AlternativeID INTEGER,
          Physical_PipeMaterial TEXT, Physical_PipeDiameter REAL,
          Physical_HazenWilliamsC REAL,
          PRIMARY KEY (DomainElementID, AlternativeID)
        );
        CREATE TABLE BaseIdahoNode_Physical_Data (
          DomainElementID INTEGER, AlternativeID INTEGER,
          Physical_NodeElevation REAL,
          PRIMARY KEY (DomainElementID, AlternativeID)
        );
        """
    )
    for aid, lab in alts:
        c.execute("INSERT INTO Alternatives VALUES (?, ?)", (aid, lab))

    # Default sewer nodes (ground in metres → store as feet)
    nodes = nodes or [
        (101, "MH-1", 1400.0),
        (102, "MH-2", 1398.0),
        (103, "OF-1", 1395.0),
    ]
    for eid, lab, ground_m in nodes:
        c.execute(
            "INSERT INTO HMIModelingElement VALUES (?, ?, 0)", (eid, lab),
        )
        c.execute(
            "INSERT INTO BaseNode_Physical_Data VALUES (?, ?, ?)",
            (eid, 1, ground_m / FT),
        )

    if network in ("water", "irrigation"):
        # Pressure pipes: IdahoPipe family
        conduits = conduits or [
            # eid, start, stop, D_mm, L_m, alt, material, C
            (1, 101, 102, 160.0, 50.0, 1, "DI", 140.0),
            (1, 101, 102, 200.0, 50.0, 2, "DI", 140.0),
            (2, 102, 103, 110.0, 40.0, 1, "PE", 140.0),
            (2, 102, 103, 110.0, 40.0, 2, "PE", 140.0),
        ]
        for eid, s, e, d_mm, L_m, alt, mat, hw in conduits:
            c.execute("INSERT OR IGNORE INTO IdahoPipe VALUES (?)", (eid,))
            c.execute(
                "INSERT OR IGNORE INTO HMIModelingElement VALUES (?, ?, 0)",
                (eid, f"P-{eid}"),
            )
            c.execute(
                "INSERT OR IGNORE INTO BaseLink_HMIDataSetTopology_Data VALUES (?, ?, ?)",
                (eid, s, e),
            )
            c.execute(
                "INSERT INTO IdahoPipe_Physical_Data VALUES (?, ?, ?, ?, ?)",
                (eid, alt, mat, d_mm / FT_MM, hw),
            )
            c.execute(
                "INSERT INTO BaseLink_HmiDataSetGeometry_Data VALUES (?, ?, ?)",
                (eid, alt, L_m / FT),
            )
            c.execute(
                "INSERT OR IGNORE INTO BaseIdahoNode_Physical_Data VALUES (?, ?, ?)",
                (s, 1, 1390.0 / FT),
            )
    else:
        # Default Co-17 validation values (R1 note) plus a second conduit
        # Co-17: D 200 mm, L 7.60 m, inverts 759.413 / 759.365
        conduits = conduits or [
            # eid, label, start, stop, D_mm, L_m, inv_s_m, inv_e_m, alt
            (1, "Co-17", 101, 102, 200.0, 7.60, 759.413, 759.365, 1),
            (1, "Co-17", 101, 102, 250.0, 7.60, 759.413, 759.365, 2),  # alt 2
            (2, "C-2", 102, 103, 200.0, 40.0, 759.365, 759.165, 1),
            (2, "C-2", 102, 103, 200.0, 40.0, 759.365, 759.165, 2),
        ]
        for eid, lab, s, e, d_mm, L_m, i0, i1, alt in conduits:
            c.execute("INSERT OR IGNORE INTO Conduit VALUES (?)", (eid,))
            c.execute(
                "INSERT OR IGNORE INTO HMIModelingElement VALUES (?, ?, 0)",
                (eid, lab),
            )
            c.execute(
                "INSERT OR IGNORE INTO BaseLink_HMIDataSetTopology_Data VALUES (?, ?, ?)",
                (eid, s, e),
            )
            c.execute(
                "INSERT INTO Conduit_Physical_Data VALUES (?, ?, ?)",
                (eid, alt, d_mm / FT_MM),
            )
            c.execute(
                "INSERT INTO PhysicalLink_Physical_Data VALUES (?, ?, ?, ?)",
                (eid, alt, i0 / FT, i1 / FT),
            )
            c.execute(
                "INSERT INTO BaseLink_HmiDataSetGeometry_Data VALUES (?, ?, ?)",
                (eid, alt, L_m / FT),
            )
            c.execute(
                "INSERT INTO GravityLinkBase_Physical_Data VALUES (?, ?, ?, ?)",
                (eid, alt, "PVC", 0.013),
            )
    conn.commit()
    conn.close()
    return path


def make_dxf(path, texts, lines=None):
    """Minimal DXF with MTEXT labels on real wet layers (R3 / R4)."""
    import ezdxf
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    for layer, text, x, y in texts:
        if layer not in doc.layers:
            doc.layers.add(layer)
        msp.add_mtext(text, dxfattribs={"layer": layer, "insert": (x, y), "char_height": 0.25})
    for layer, x0, y0, x1, y1 in (lines or []):
        if layer not in doc.layers:
            doc.layers.add(layer)
        msp.add_line((x0, y0), (x1, y1), dxfattribs={"layer": layer})
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
    """T11-1: US feet storage → length m, diameter mm; source sha256 (no unit guessing)."""
    db = make_sqlite(tmp_path / "syn.stsw.sqlite")
    out = tmp_path / "networks.json"
    rc, msg = run("read_bentley_sqlite.py", "--sqlite", db, "--network", "sewer", "--out", out)
    assert rc == 0, msg
    doc = load(out)
    Draft202012Validator(load(TPL / "networks.schema.json")).validate(doc)
    assert doc["units"]["length"] == "m" and doc["units"]["diameter"] == "mm"
    assert doc["units"]["source_storage"] == "US_feet"
    assert doc["source"]["sha256"] and len(doc["source"]["sha256"]) == 64
    assert doc["source"]["schema"] == "openflows_conduit"
    assert doc["summary"]["conduit_count"] == 2  # base alt only
    # Co-17 expected SI values
    by_id = {c["id"]: c for c in doc["conduits"]}
    c0 = by_id["Co-17"]
    assert abs(c0["diameter_mm"] - 200) < 0.5
    assert abs(c0["length_m"] - 7.60) < 0.02
    assert abs(c0["invert_start_m"] - 759.413) < 0.02
    assert abs(c0["invert_stop_m"] - 759.365) < 0.02
    assert c0["units_source"] == "US_feet"


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
    """T11-3: real node CL:/IL: and pipe [<D>]L=/S= labels (R4); real layers (R3)."""
    node_txt = "MH-1\nCL: 412.50m\nIL: 410.20m\nE: 1000.00\nN: 2000.00"
    pipe_txt = "200L=50.00\nS=0.50%"
    dxf = make_dxf(tmp_path / "swn.dxf", [
        ("CI-Ss_50_35_08_30-P-SS_Annotation Main Sewer", node_txt, 1000, 2000),
        ("CI-Ss_50_35_08_30-P-SS_Pipe Dia 200", pipe_txt, 1050, 2000),
        ("C-PW-FIRE HYDRANT COVER CATCH", "FH-1", 50, 50),
    ], lines=[
        ("CI-Ss_50_35_08_30-P-SS_Pipe Dia 200", 0, 0, 50, 0),
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
    labels = [p for p in doc["pipes"] if p.get("kind") == "pipe_label"]
    assert labels and abs(labels[0]["length_m"] - 50.0) < 1e-6
    assert abs(labels[0]["slope_pct"] - 0.50) < 1e-6
    assert abs(labels[0]["diameter_mm"] - 200) < 1e-6
    assert any(f["kind"] == "hydrant" for f in doc["fixtures"])
    # Diameter from layer pattern
    geom = [p for p in doc["pipes"] if p.get("kind") == "pipe_geometry"]
    assert geom and abs(geom[0]["diameter_mm"] - 200) < 1e-6
    assert geom[0]["utility"] == "sewer"


def test_t11_3b_pipe_label_no_diameter_prefix(tmp_path):
    """Pipe label without diameter prefix still parses L= / S=."""
    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location("rnp", SK / "read_network_plan.py")
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    p = mod.parse_pipe_label("L=63.01\nS=1.50%")
    assert p and abs(p["length_m"] - 63.01) < 1e-9
    assert abs(p["slope_pct"] - 1.50) < 1e-9
    assert p["diameter_mm"] is None


def test_t11_3c_water_layer_pct_c(tmp_path):
    """Water %%c160mm layer yields diameter 160."""
    dxf = make_dxf(tmp_path / "pwt.dxf", [
        ("%%c160mm", "160L=12.00\nS=0.00%", 10, 10),
    ], lines=[("%%c160mm", 0, 0, 12, 0)])
    out = tmp_path / "plan.json"
    rc, msg = run("read_network_plan.py", "--dxf", dxf, "--site", "NMAR",
                  "--network", "water", "--out", out)
    assert rc == 0, msg
    doc = load(out)
    pipes = doc["pipes"]
    assert any(p.get("diameter_mm") == 160 for p in pipes)


# --------------------------------------------------------------------------- T11-4 W3 thresholds ±0.01
def test_t11_4_w3_gradient_threshold(tmp_path):
    """T11-4: 200 mm at 0.49 % flags; 0.50 % passes (± 0.01)."""
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
    """DQ-3 SHP cross-check for Co-17 D/L/inverts (JSON export equals sqlite)."""
    db = make_sqlite(tmp_path / "syn.stsw.sqlite", alts=((1, "Base"),))
    shp = write(tmp_path / "co17.json", [
        {
            "label": "Co-17",
            "diameter_mm": 200,
            "length_m": 7.60,
            "invert_start_m": 759.413,
            "invert_stop_m": 759.365,
            "e": None, "n": None,
        },
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
    checks = doc["shp_cross_check"]["checks"]
    assert checks["diameter_mm"]["result"] == "pass"
    assert checks["length_m"]["result"] == "pass"
    assert checks["invert_start_m"]["result"] == "pass"
    assert checks["invert_stop_m"]["result"] == "pass"


def test_t11_idaho_pipe_water_reader(tmp_path):
    """Water / irrigation uses IdahoPipe tables; Hazen-Williams C preserved."""
    db = make_sqlite(tmp_path / "syn.wtg.sqlite", network="water", alts=((1, "Base"), (2, "Alt")))
    out = tmp_path / "networks.json"
    rc, msg = run("read_bentley_sqlite.py", "--sqlite", db, "--network", "water", "--out", out)
    assert rc == 0, msg
    doc = load(out)
    assert doc["source"]["schema"] == "openflows_idaho_pipe"
    assert doc["summary"]["conduit_count"] == 2
    assert all(c["roughness_type"] == "HazenWilliams" for c in doc["conduits"])
    assert all(abs(c["roughness"] - 140) < 1e-6 for c in doc["conduits"])


def test_t11_criteria_reader(tmp_path):
    """read_wet_criteria extracts sewer gradient table + totals from skill-2 text."""
    text = {
        "pages": [{
            "page": 12,
            "text": (
                "Sewer network design criteria §6.3\n"
                "Table 6.1 Minimum diameter 200 mm\n"
                "Table 6.3\n"
                "200 mm 0.50 % 0.75 m/s\n"
                "300 mm 0.35 % 0.75 m/s\n"
                "Total network length 9.70 km with 173 manholes\n"
                "Depth 1.40 – 3.50 m\n"
                "Potable water §5.3\n"
                "Total network length 11.0 km residual pressure 2.76 / 5.27 bar\n"
                "Storm water drainage §10.2\n"
                "Table 10.9\n"
                "Return period 10-year design storm\n"
                "300 mm 5.91 km\n"
                "P-055 0.18 m\n"
                "P-118 0.63 m\n"
            ),
        }, {
            "page": 13,
            "text": "P-119 1.15 m\nP-092 110.6 m\nP-088 100.7 m\n",
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
    clauses = {t["clause"] for t in doc["declared_tables"]}
    assert "§5.3" in clauses and "§6.3" in clauses and "§10.2" in clauses
    assert "Table 6.1" in clauses and "Table 6.3" in clauses and "Table 10.9" in clauses
    ids = {r["id"]: r["value"] for r in doc["storm_table_109"]}
    assert abs(ids["P-055"] - 0.18) < 1e-9
    assert abs(ids["P-118"] - 0.63) < 1e-9
    assert abs(ids["P-119"] - 1.15) < 1e-9
    assert abs(ids["P-092"] - 110.6) < 1e-9
    assert abs(ids["P-088"] - 100.7) < 1e-9


def test_t11_params_declared_not_er():
    """R6: sewer cover and water min D are declared, not ER; water cover stays ER advisory."""
    p = load(REF / "params.json")
    assert "cover_m" not in (p["er"].get("sewer") or {})
    assert "min_diameter_mm" not in (p["er"].get("water") or {})
    assert p["er"]["water"]["cover_m"] == 1.2
    assert p["er"]["water"].get("cover_advisory") is True
    assert p["declared"]["sewer"]["source"] == "declared"
    assert p["declared"]["sewer"]["cover_m"] == 1.0
    assert "6.3.3" in p["declared"]["sewer"]["clause"]
    assert p["declared"]["water"]["source"] == "declared"
    assert p["declared"]["water"]["min_diameter_mm"] == 110
    assert "5.3.7" in p["declared"]["water"]["clause"]


def test_t11_wet_layers_agreed_real_names():
    """R3: wet_layers.json uses AGREED tag and real NMAR layer names (no invented)."""
    doc = load(REF / "wet_layers.json")
    assert "[AGREED 21:55]" in doc["tag"]
    layers = doc["shared_layers"]
    assert "C-SSWR-MNHL-P" in layers
    assert "CI-Ss_50_35_08_30-P-SS_Annotation Main Sewer" in layers
    assert "C-PW-FIRE HYDRANT COVER CATCH" in layers
    for banned in ("SWN-PIPE", "C-PIPE-200", "-02- Sewerage", "-05- Hydrant"):
        assert banned not in layers
    assert any("SS_Pipe Dia" in p["regex"] for p in doc["layer_patterns"])
    assert any("%%c" in p["regex"] for p in doc["layer_patterns"])


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


def test_t11_w3_declared_cover_and_w5_declared_min_d(tmp_path):
    """R6: W3 cover uses declared 1.0 m; W5 min D uses declared 110 mm."""
    sewer = {
        "networks_version": "1.0",
        "network": "sewer",
        "source": {"path": "syn", "sha256": "f" * 64, "kind": "synthetic"},
        "conduits": [{
            "id": "SHALLOW", "element_id": 1, "source": "model",
            "diameter_mm": 200, "length_m": 40.0,
            "invert_start_m": 100.0, "invert_stop_m": 99.8,
            "slope_pct": 0.5, "cover_m": 0.8,
            "start_node_id": 1, "stop_node_id": 2,
        }],
        "summary": {"conduit_count": 1, "length_km": 0.04},
        "plan": [],
    }
    cons, cand, _ = check(tmp_path / "sewer", sewer, presence={
        "networks": {
            "sewer": {"plan": True, "model": True},
            "water": {"plan": True, "model": True},
            "irrigation": {"plan": True, "model": True},
            "storm": {"plan": True, "model": True},
        },
        "hydraulic_calculations": True,
    })
    cover = [r for r in cons["comparisons"]
             if r["check"] == "W3" and str(r["quantity"]).startswith("cover")
             and r["result"] == "flag"]
    assert cover and cover[0]["values"].get("source") == "declared"
    assert any("6.3.3" in f["statement"] for f in cand["findings"])

    water = {
        "networks_version": "1.0",
        "network": "water",
        "source": {"path": "syn", "sha256": "g" * 64, "kind": "synthetic"},
        "conduits": [{
            "id": "TINY", "element_id": 1, "source": "model", "network": "water",
            "diameter_mm": 90, "length_m": 20.0, "cover_m": 1.5,
        }],
        "summary": {"conduit_count": 1, "length_km": 0.02},
        "plan": [],
    }
    cons_w, cand_w, _ = check(tmp_path / "water", water, presence={
        "networks": {
            "sewer": {"plan": True, "model": True},
            "water": {"plan": True, "model": True},
            "irrigation": {"plan": True, "model": True},
            "storm": {"plan": True, "model": True},
        },
        "hydraulic_calculations": True,
    })
    mind = [r for r in cons_w["comparisons"]
            if r["check"] == "W5" and str(r["quantity"]).startswith("min_D")
            and r["result"] == "flag"]
    assert mind and mind[0]["values"].get("source") == "declared"
    assert any("5.3.7" in f["statement"] for f in cand_w["findings"])


def test_skill_md_under_500_and_frontmatter():
    text = (SK.parent / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert len(text.splitlines()) < 500
    assert 'name: reviewing-wet-utilities' in text
    assert 'version: "0.1.0"' in text
    assert "WUT" in text
