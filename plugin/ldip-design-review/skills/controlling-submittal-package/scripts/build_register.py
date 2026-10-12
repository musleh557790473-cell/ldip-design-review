#!/usr/bin/env python3
"""Build the sheet register of a submittal: package.json (SPC-0006 §3.2).

Usage: python build_register.py --files <inventory.json | files.json> --site QRWN|NRJS|NMAR [--sub <id>]
           [--list <drawing list .csv/.xlsx> | --list-not-received] [--ingestion <x.dwg.json> ...] [--run <run-id>]
           [--previous-sub <id>] --out <package.json>
One register row per document: files are grouped by the document number in their name (for a number
whose field count conforms to SPC-0004 §5.1 the last field is the revision and the rest is the base;
otherwise the whole token is the base and no revision is read). Each row lists the carriers of its
number and revision — file names, DWG and PDF title blocks (from skill 1 / 2 ingestion files, matched by
SHA-256), drawing-list entry — the formats received, the DWG <-> PDF pairing of drawings and the DWG
origin (brief §1.4: civil3d_native from AECC objects, saved_in_civil3d from AECC classes only; a
record, never a finding — SPC-0004 §6.3). Files whose name carries no number are listed apart.
With --list, the drawing list (or transmittal list) is read by header labels (document / drawing
number, revision, title); listed-but-not-received and received-but-not-listed numbers are recorded.
In a review run the ingestion files come from files.json (merge_batches.py) and need not be passed.
Exit: 0 written · 2 input error. No network, no shell calls.
"""
__version__ = "0.3.0"

import argparse
import csv
import re
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pkg_common import (register_base, register_revision,  # noqa: E402
                        EXIT_DEP, EXIT_INPUT, EXIT_OK, atomic_write_json, col_letter, emit, fail,  # noqa: E402
                        number_from_name, parse_number, path_ok, read_json, sha256_file)

NUM_HDR = re.compile(r"^(doc(ument)?|drawing|dwg|sheet)\.?\s*(no\.?|number|ref(erence)?)$|^document\s+number$", re.I)
REV_HDR = re.compile(r"^rev(\.|ision)?(\s*(code|no\.?))?$", re.I)
TITLE_HDR = re.compile(r"^(document\s+|drawing\s+)?(title|description)$", re.I)


def read_list(path):
    """Drawing / transmittal list -> entries [{number, revision, title, cell}]. Header found by labels."""
    p = Path(path)
    rows, sheet = [], None
    if p.suffix.lower() == ".csv":
        with open(p, newline="", encoding="utf-8-sig") as fh:
            rows = [list(r) for r in csv.reader(fh)]
        sheet = p.stem
    else:
        try:
            import openpyxl
        except ImportError:
            raise RuntimeError("dependency")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
            for ws in wb.worksheets:
                data = [list(r) for r in ws.iter_rows(values_only=True)]
                if any(any(NUM_HDR.match(str(v or "").strip()) for v in r) for r in data[:40]):
                    rows, sheet = data, ws.title
                    break
            wb.close()
    for i, r in enumerate(rows[:40]):
        cols = {}
        for j, v in enumerate(r):
            t = str(v or "").strip()
            if NUM_HDR.match(t) and "number" not in cols:
                cols["number"] = j
            elif REV_HDR.match(t) and "revision" not in cols:
                cols["revision"] = j
            elif TITLE_HDR.match(t) and "title" not in cols:
                cols["title"] = j
        if "number" in cols:
            out = []
            for k in range(i + 1, len(rows)):
                r2 = rows[k]
                num = str(r2[cols["number"]] or "").strip() if cols["number"] < len(r2) else ""
                if not num:
                    continue
                rev = r2[cols["revision"]] if "revision" in cols and cols["revision"] < len(r2) else None
                if isinstance(rev, float) and rev.is_integer():
                    rev = f"{int(rev):02d}"
                out.append({"number": num.upper(), "revision": (str(rev).strip() or None) if rev is not None else None,
                            "title": str(r2[cols["title"]] or "").strip() if "title" in cols and cols["title"] < len(r2) else None,
                            "cell": f"{col_letter(cols['number'])}{k + 1}", "sheet": sheet})
            return {"path": p.as_posix(), "sha256": sha256_file(p), "number": number_from_name(p.name), "sheet": sheet,
                    "header_row": i + 1, "columns": {k: col_letter(v) for k, v in cols.items()}, "entries": out}
    raise ValueError(f"{p.name}: no column labelled document / drawing number in the first 40 rows")


def load_ingestion(paths):
    by_sha = {}
    for ip in paths:
        try:
            d = read_json(ip)
        except (OSError, ValueError):
            continue
        sha = (d.get("source") or {}).get("sha256")
        if sha:
            by_sha[sha] = (Path(ip).as_posix(), d)
    return by_sha


TB_REASONS = ("TITLE_BLOCK_ATTRIBS_UNDECODED", "TITLE_BLOCK_FIELD_EMPTY", "TITLE_BLOCK_NOT_FOUND")


def tb(d):
    t = d.get("title_block") or {}
    if not t or not (t.get("document_no") or t.get("revision")):
        return None
    return {"document_no": (t.get("document_no") or "").strip().upper() or None, "revision": t.get("revision"),
            "title": t.get("title"), "sheet": t.get("sheet"), "date": t.get("date"), "source_ref": t.get("source_ref")}


def tb_reason(d):
    """Why the ingestion file has no title block (first matching warning code), K5-2."""
    codes = [w.get("code") for w in d.get("warnings", [])]
    return next((c for c in TB_REASONS if c in codes), "TITLE_BLOCK_NOT_FOUND")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", required=True)
    ap.add_argument("--site", required=True, choices=["QRWN", "NRJS", "NMAR"])
    ap.add_argument("--sub")
    ap.add_argument("--list")
    ap.add_argument("--list-not-received", action="store_true",
                    help="no transmittal or drawing list came with the submission (PKG-007 reports it as missing)")
    ap.add_argument("--ingestion", nargs="*", default=[])
    ap.add_argument("--run")
    ap.add_argument("--previous-sub")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if not path_ok(a.out):
        return fail(EXIT_INPUT, "output path longer than 250 characters")
    try:
        inv = read_json(a.files)
    except (OSError, ValueError) as exc:
        return fail(EXIT_INPUT, f"cannot read --files: {exc}")
    ing_paths = list(a.ingestion) + [f["extraction"]["ingestion"] for f in inv["files"]
                                     if (f.get("extraction") or {}).get("ingestion")]
    ing = load_ingestion(ing_paths)
    lst = None
    if a.list:
        try:
            lst = read_list(a.list)
        except RuntimeError:
            return fail(EXIT_DEP, "openpyxl is not installed")
        except (OSError, ValueError) as exc:
            return fail(EXIT_INPUT, f"cannot read --list: {exc}")
    groups, no_number, origins, attached = {}, [], [], []
    for f in inv["files"]:
        if not f.get("number"):
            if f.get("number_inherited"):      # P-S6-7: part of a numbered deliverable
                attached.append({"path": f["path"], "format": f.get("format"), "sha256": f["sha256"],
                                 **f["number_inherited"]})
            else:
                no_number.append({"path": f["path"], "format": f.get("format"), "sha256": f["sha256"]})
            continue
        pn = parse_number(f["number"])
        rb = register_base(pn)
        g = groups.setdefault(rb, {"base": rb, "parse": {k: pn.get(k) for k in
                                           ("fields", "field_count", "expected_fields", "conforming_count", "type", "parts",
                                            "layout", "midp")},
                                           "files": [], "carriers": {"file_name": []}, "formats": set()})
        entry = {"path": f["path"], "sha256": f["sha256"], "format": f.get("format"), "bytes": f.get("bytes"),
                 "number": pn["number"], "revision": register_revision(pn)}
        g["files"].append(entry)
        g["carriers"]["file_name"].append({"path": f["path"], "number": pn["number"], "revision": register_revision(pn)})
        if f.get("format"):
            g["formats"].add(f["format"])
        if f["sha256"] in ing:
            ipath, d = ing[f["sha256"]]
            entry["ingestion"] = ipath
            t = tb(d)
            kind = "dwg_title_block" if f.get("format") == "DWG" else "pdf_title_block" if f.get("format") == "PDF" else None
            if kind:
                g["carriers"].setdefault(kind, []).append(
                    {"path": f["path"], **(t or {"document_no": None, "revision": None, "reason": tb_reason(d)}),
                     "read": t is not None})
            if f.get("format") == "DWG":
                aecc = (d.get("content") or {}).get("aecc") or {}
                c = d.get("content") or {}
                origins.append({"path": f["path"], "base": rb,
                                "origin": "civil3d_native" if aecc.get("civil3d_native") else
                                ("saved_in_civil3d" if aecc.get("saved_in_civil3d") else "autocad_or_exported"),
                                "confidence": "Confirmed" if aecc.get("civil3d_native") else "Probable",
                                "unresolved": {k: [x.get("name") or x.get("filename") or x.get("style")
                                                   for x in c.get(k, []) if x.get("resolved") is False]
                                               for k in ("xrefs", "images", "fonts")},
                                "resolve_checked": any(x.get("resolved") is not None
                                                       for k in ("xrefs", "images", "fonts") for x in c.get(k, []))})
    if lst:
        for e in lst["entries"]:
            pe = parse_number(e["number"])
            if register_base(pe) in groups:
                groups[register_base(pe)]["carriers"].setdefault("drawing_list", []).append(
                    {"number": pe["number"], "revision": e["revision"] or register_revision(pe), "cell": e["cell"], "sheet": e["sheet"]})
    register = []
    for base in sorted(groups):
        g = groups[base]
        fm = sorted(g["formats"])
        pairing = None
        if g["parse"]["type"] == "DWG":
            pairing = "paired" if {"DWG", "PDF"} <= g["formats"] else "dwg_only" if "DWG" in g["formats"] else \
                "pdf_only" if "PDF" in g["formats"] else "neither"
        revs = sorted({x["revision"] for x in g["carriers"]["file_name"] if x["revision"]})
        register.append({"base": base, "numbers": sorted({x["number"] for x in g["files"]}), "revisions": revs,
                         "parse": g["parse"], "formats_received": fm, "pairing": pairing,
                         "files": sorted(g["files"], key=lambda x: x["path"]), "carriers": g["carriers"]})
    if a.list and a.list_not_received:
        return fail(EXIT_INPUT, "--list and --list-not-received exclude each other")
    transmittal, missing, unlisted = ({"received": False} if a.list_not_received else None), [], []
    if lst:
        listed = {}
        for e in lst["entries"]:
            listed.setdefault(register_base(parse_number(e["number"])), e)
        missing = [dict(e, base=b) for b, e in sorted(listed.items()) if b not in groups]
        unlisted = [{"base": b, "paths": [x["path"] for x in groups[b]["files"]]} for b in sorted(groups) if b not in listed]
        transmittal = {k: lst[k] for k in ("path", "sha256", "number", "sheet", "header_row", "columns")}
        transmittal["entries"] = len(lst["entries"])
    doc = {"tool": {"name": "build_register.py", "version": __version__}, "run_id": a.run or inv.get("run_id"),
           "site": a.site, "sub_id": a.sub or inv.get("sub_id"), "root": inv.get("root"), "mode": inv.get("mode"),
           "transmittal": transmittal, "sheet_register": register, "files_without_number": no_number,
           "attached_files": attached,
           "dwg_origin": sorted(origins, key=lambda x: x["path"]), "missing": missing, "unlisted": unlisted,
           "previous_submittal": a.previous_sub,
           "counts": {"documents": len(register), "files": len(inv["files"]), "files_without_number": len(no_number),
                      "attached_files": len(attached),
                      "with_ingestion": sum(1 for r in register for x in r["files"] if x.get("ingestion")),
                      "listed": len(lst["entries"]) if lst else None, "missing": len(missing) if lst else None,
                      "unlisted": len(unlisted) if lst else None}}
    atomic_write_json(a.out, doc)
    emit({"status": "ok", "out": str(a.out), **{k: v for k, v in doc["counts"].items() if v is not None}})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
