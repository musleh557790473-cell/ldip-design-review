#!/usr/bin/env python3
"""Local test harness for skill 1 extracting-dwg-data (SPC-0006 §4.1.8) — owner runs it on a
Windows machine that has the converters. Not part of the plugin; never run by an agent.

For each DWG in --in: convert.py → extract_dxf.py → validate_ingestion.py, then print one
summary line per file and write summary.json in --out. Nothing leaves the computer; paste the
summary back into the build session (no Parsons data goes to Git).

Usage (from the unzipped folder):
  python tools/skill1-local-test/run_skill1.py --in C:/LDIP_test/in --out C:/LDIP_test/out
         --engine oda [--exe "C:/Program Files/ODA/ODAFileConverter 27.1.0/ODAFileConverter.exe"]
"""
__version__ = "0.1.2"

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SK = ROOT / "plugin" / "ldip-design-review" / "skills" / "extracting-dwg-data" / "scripts"
DEFAULT_EXE = {"oda": r"C:\Program Files\ODA\ODAFileConverter 27.1.0\ODAFileConverter.exe",
               "accoreconsole": r"C:\Program Files\Autodesk\AutoCAD 2025\accoreconsole.exe"}


def run(script, *args):
    p = subprocess.run([sys.executable, str(SK / script), *map(str, args)], capture_output=True, text=True)
    lines = p.stdout.strip().splitlines()
    try:
        out = json.loads(lines[-1]) if lines else {}
    except json.JSONDecodeError:
        out = {}
    if p.returncode != 0 and not out.get("detail"):
        out = {**out, "status": "error", "detail": (p.stderr or p.stdout or "no output")[-1500:]}
    return p.returncode, out


def polys_by_layer(polys, top=12):
    agg = {}
    for p in polys:
        a = agg.setdefault(p["layer"], {"count": 0, "areas": []})
        a["count"] += 1
        a["areas"].append(p["area"])
    rows = sorted(agg.items(), key=lambda kv: -kv[1]["count"])[:top]
    return {k: {"count": v["count"], "min_area": min(v["areas"]), "max_area": max(v["areas"]),
                "sum_area": round(sum(v["areas"]), 2)} for k, v in rows}


def texts_by_layer(texts, top=10, samples=6):
    agg = {}
    for x in texts:
        a = agg.setdefault(x["layer"], [])
        a.append(x["text"].replace("\n", " ")[:40])
    rows = sorted(agg.items(), key=lambda kv: -len(kv[1]))[:top]
    return {k: {"count": len(v), "samples": v[:samples]} for k, v in rows}


def attrs_by_block(blocks, top=8):
    agg = {}
    for b in blocks:
        if b["attributes"]:
            a = agg.setdefault(b["name"], {"count": 0, "tags": sorted(b["attributes"])[:12], "sample": b["attributes"]})
            a["count"] += 1
    return dict(sorted(agg.items(), key=lambda kv: -kv[1]["count"])[:top])


def summarise(js):
    d = json.loads(js.read_text(encoding="utf-8"))
    c = d["content"]
    return {
        "acadver": c["header"]["acadver_source"], "insunits": c["header"]["insunits"], "crs": d["crs"]["declared"],
        "extmin": c["header"]["extmin"][:2], "extmax": c["header"]["extmax"][:2],
        "layers": len(c["layers"]), "inserts": len(c["blocks"]), "texts": len(c["texts"]),
        "dimensions": len(c["dimensions"]), "dim_overrides": sum(1 for x in c["dimensions"] if x["override"]),
        "closed_polylines": len(c["polylines"]), "open_polylines": sum(c["open_polylines_by_layer"].values()),
        "xrefs": [(x["name"], x["resolved"]) for x in c["xrefs"]][:10], "images": len(c["images"]),
        "aecc": {k: c["aecc"][k] for k in ("civil3d_native", "saved_in_civil3d", "aecc_class_count",
                                            "aecc_proxy_entity_count", "proxy_count")},
        "proxy_entities_by_class": dict(sorted(c["aecc"]["proxy_entities_by_class"].items(), key=lambda kv: -kv[1])[:15]),
        "proxy_objects_top": dict(sorted(c["aecc"]["proxy_objects_by_class"].items(), key=lambda kv: -kv[1])[:8]),
        "aecc_entities_by_type": c["aecc"]["aecc_entities_by_type"],
        "closed_polylines_by_layer": polys_by_layer(c["polylines"]),
        "texts_by_layer": texts_by_layer(c["texts"]),
        "block_attributes_by_block": attrs_by_block(c["blocks"]),
        "dimension_overrides": [{k: x.get(k) for k in ("text", "measurement", "text_differs", "layer", "handle")}
                                for x in c["dimensions"] if x["override"]][:10],
        "origin_signals": {k: v for k, v in c["origin_signals"].items() if k not in ("note",)},
        "layouts": [lo["name"] for lo in c["layouts"]][:10],
        "title_block": d["title_block"], "warnings": [w["code"] for w in d["warnings"]],
        "top_layers": [ly["name"] for ly in sorted(c["layers"], key=lambda x: -x["entity_count"])[:15]],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--engine", default="oda", choices=["oda", "accoreconsole", "ezdwg"])
    ap.add_argument("--exe")
    a = ap.parse_args()
    inp, out = Path(a.inp), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    exe = a.exe or DEFAULT_EXE.get(a.engine)
    results = {"harness": __version__, "engine": a.engine, "exe": exe, "files": {}}
    for dwg in sorted(inp.glob("*.dwg")):
        stem, rec = dwg.stem, {}
        args = ["--engine", a.engine, "--dwg", dwg, "--out", out / f"{stem}.dxf", "--log", out / f"{stem}.convert.json"]
        if exe and a.engine != "ezdwg":
            args += ["--exe", exe]
        rc, s = run("convert.py", *args)
        rec["convert"] = {"rc": rc, **{k: s.get(k) for k in ("status", "detail", "engine_version", "acadver", "warnings")}}
        if rc == 0:
            js = out / f"{dwg.name}.json"
            rc, s = run("extract_dxf.py", "--dxf", out / f"{stem}.dxf", "--dwg", dwg, "--convert-log",
                        out / f"{stem}.convert.json", "--out", js, "--source-path", dwg.name, "--resolve-base", inp)
            rec["extract"] = {"rc": rc, "status": s.get("status"), "detail": s.get("detail")}
            if rc == 0:
                rc, s = run("validate_ingestion.py", "--files", js)
                rec["validate"] = {"rc": rc, "status": s.get("status"), "errors": s.get("errors")}
                rec["summary"] = summarise(js)
        results["files"][dwg.name] = rec
        print(json.dumps({dwg.name: {k: (v.get("rc") if isinstance(v, dict) and "rc" in v else None)
                                     for k, v in rec.items() if k != "summary"}}))
    (out / "summary.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"summary written: {out / 'summary.json'}")


if __name__ == "__main__":
    main()
