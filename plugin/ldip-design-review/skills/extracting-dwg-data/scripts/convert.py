#!/usr/bin/env python3
"""DWG → DXF with a switchable engine (SPC-0006 §4.1.3, §4.1.5).

Engines:
  ezdwg          Python package (MIT), reads DWG R2000–R2018; under test (SPC-0006 Appendix A).
  libredwg       GNU LibreDWG `dwg2dxf` (GPL-3.0, separate program; --libredwg-exe or env LDIP_LIBREDWG_EXE).
                 Enabled only after the licence review L-1 (SPC-0006 Rev F §4.1A.2, DF-1).
  chain          (0.2.0, K21-1 / K21-3) ezdwg first; when ezdwg fails on the file (exit 2 or 3) LibreDWG,
                 if it is enabled (LDIP_LIBREDWG_EXE set); when no step reads the file: exit 3 with
                 `next: local_autocad` — the owner runs the local AutoCAD route (P-APS-1 accoreconsole or a
                 local AutoCAD / Civil 3D connector) on his computer. Agents never call a connector.
  oda            ODA File Converter (external program; --exe or env LDIP_ODA_EXE). Licence: K2 [GAP].
  accoreconsole  AutoCAD Core Console, Windows only (--exe or env LDIP_ACCORE_EXE). Script DXFOUT.

Usage: python convert.py --engine chain|ezdwg|libredwg|oda|accoreconsole --dwg <file.dwg> --out <file.dxf>
                         --log <file.convert.json> [--exe <converter>] [--libredwg-exe <dwg2dxf>]
                         [--timeout 600] [--reuse]
--reuse (0.1.1, D9): keep an earlier conversion when its log says ok for the same engine, its input
hash equals the DWG on disk and its output hash equals the DXF on disk; nothing is rewritten.
Writes the DXF (via a temporary name, then rename) and a converter log: engine and version, input
hash and DWG version, output hash, duration, return code, entities the engine skipped.
Exit: 0 converted · 2 input error or no DXF produced · 3 engine missing, DWG version not supported,
or timeout. The only external programs called are the named converters (SPC-0006 §2.3.5).
With --engine chain the log records the engine that converted (`engine`), the steps tried (`chain`) and,
after a fallback, the first engine's error (`fallback_from`).
"""
__version__ = "0.2.2"

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dwg_common import (  # noqa: E402
    EXIT_DEPENDENCY, EXIT_INPUT, EXIT_OK, DwgError, atomic_write_json, check_path, emit, sha256_file,
)

# DWG file signature → release (first 6 bytes of the file)
ACADVER = {"AC1009": "R12", "AC1012": "R13", "AC1014": "R14", "AC1015": "R2000", "AC1018": "R2004",
           "AC1021": "R2007", "AC1024": "R2010", "AC1027": "R2013", "AC1032": "R2018"}
EZDWG_SUPPORTED = ("AC1015", "AC1018", "AC1021", "AC1024", "AC1027", "AC1032")
EZDWG_PINNED = "0.16.0"   # D13 [AGREED] 5 Oct 2026, D13-2 [AGREED] 16 Oct 2026 20:13: run environment pins ezdwg to this version


def dwg_version(path):
    with open(path, "rb") as fh:
        sig = fh.read(6).decode("ascii", errors="replace")
    if not sig.startswith("AC"):
        raise DwgError(EXIT_INPUT, f"not a DWG file (signature {sig!r})")
    return sig


def run_ezdwg(dwg, out_tmp, log):
    try:
        import ezdwg
        from importlib.metadata import version
    except ImportError as exc:
        raise DwgError(EXIT_DEPENDENCY, "ezdwg is not installed") from exc
    log["engine_version"] = version("ezdwg")
    if log["engine_version"] != EZDWG_PINNED:
        # D13: warn only; the conversion still runs (owner decision 5 Oct 2026).
        log["warnings"].append({"code": "ENGINE_VERSION_UNPINNED",
                                "detail": f"ezdwg {log['engine_version']} installed; pinned {EZDWG_PINNED} (D13) "
                                          "- results not comparable with pinned runs"})
    if log["input"]["acadver"] not in EZDWG_SUPPORTED:
        raise DwgError(EXIT_DEPENDENCY, f"DWG {log['input']['acadver']} not supported by ezdwg (R2000–R2018)")
    # Keep dimensions as DIMENSION entities (override flags, brief §5.5) and inserts as INSERTs.
    res = ezdwg.to_dxf(str(dwg), str(out_tmp), dxf_version="R2018", explode_dimensions=False,
                       flatten_inserts=False, preserve_colors=True, include_unsupported=False)
    log["entities"] = {"total": res.total_entities, "written": res.written_entities,
                       "skipped": res.skipped_entities, "skipped_by_type": dict(sorted(res.skipped_by_type.items()))}
    if res.skipped_entities:
        log["warnings"].append({"code": "ENGINE_SKIPPED_ENTITIES",
                                "detail": f"ezdwg skipped {res.skipped_entities} of {res.total_entities} entities: "
                                          + ", ".join(f"{k} {v}" for k, v in sorted(res.skipped_by_type.items()))})
    attrib_check(ezdwg, dwg, log)
    n_attrib = res.skipped_by_type.get("ATTRIB", 0)
    if n_attrib and not any(w["code"] == "TITLE_BLOCK_ATTRIBS_UNDECODED" for w in log["warnings"]):
        # ezdwg 0.16 decodes the attributes but does not write ATTRIB entities: title-block values are still
        # not in the DXF, so the K5-2 / K2 warning is kept (session 24, D13-2 evidence)
        log["warnings"].append({"code": "TITLE_BLOCK_ATTRIBS_UNDECODED",
                                "detail": f"ezdwg did not write {n_attrib} block attribute(s) to the DXF; title-block "
                                          "values are not in the DXF - use the oda or accoreconsole engine (K2)"})
    log["returncode"] = 0


TAG_RE = re.compile(r"^[A-Za-z0-9_.#\\-]{1,255}$")
# K5 profile (Namar 30% package, 11 Oct 2026): paper-space block holding the Parsons title-block values.
TB_ATTRIB_BLOCKS = ("Ladun - Qairwan ATT",)


def attrib_check(ezdwg, dwg, log):
    """K5-2 (owner approved 11 Oct 2026): title-block attributes ezdwg cannot decode.

    Parsons title-block values are attributes of a paper-space block (TB_ATTRIB_BLOCKS). On AC1032 files
    ezdwg 0.12.9 decodes them as garbled text or not at all; ezdwg 0.16.0 decodes them but does not write the
    ATTRIB entities. Either way none reach the DXF. Counted as
    undecoded: an attribute whose tag is not plain letters / digits / _ . # -, and a known title-block
    insert without any readable attribute."""
    try:
        rows = ezdwg.raw.decode_attrib_entities(str(dwg))
        d = ezdwg.read(str(dwg))
        inserts = {}
        for space in (d.modelspace(), d.paperspace()):
            for e in space.iter_entities():
                if e.dxftype == "INSERT":
                    inserts[e.handle] = e.dxf.get("name")
    except Exception as exc:  # the check is informative only
        log["attributes"] = {"checked": False, "reason": f"{type(exc).__name__}: {str(exc)[:160]}"}
        return
    def owner(r):
        return r[-1][1] if isinstance(r[-1], (tuple, list)) and len(r[-1]) > 1 else None
    bad = [r for r in rows if not (isinstance(r[2], str) and TAG_RE.match(r[2]))]
    good_owners = {owner(r) for r in rows if r not in bad}
    tb_missing = [h for h, n in inserts.items() if n in TB_ATTRIB_BLOCKS and h not in good_owners]
    blocks = sorted({inserts[owner(r)] for r in bad if inserts.get(owner(r))} | {inserts[h] for h in tb_missing})
    log["attributes"] = {"checked": True, "total": len(rows), "undecoded": len(bad),
                         "title_block_inserts_unread": len(tb_missing), "blocks": blocks}
    if bad or tb_missing:
        log["warnings"].append({"code": "TITLE_BLOCK_ATTRIBS_UNDECODED",
                                "detail": f"ezdwg could not decode {len(bad)} of {len(rows)} block attribute(s); "
                                          f"{len(tb_missing)} title-block insert(s) without readable attributes"
                                          + (f" (blocks: {', '.join(blocks[:5])})" if blocks else "")
                                          + "; title-block values are not in the DXF - use the oda or "
                                            "accoreconsole engine (K2)"})


def find_exe(arg, env, name):
    exe = arg or os.environ.get(env)
    if not exe or not Path(exe).is_file():
        raise DwgError(EXIT_DEPENDENCY, f"{name} not found (pass --exe or set {env})")
    return Path(exe)


def run_external(cmd, timeout, log):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)   # no shell (list form)
    except subprocess.TimeoutExpired as exc:
        raise DwgError(EXIT_DEPENDENCY, f"converter timeout after {timeout} s") from exc
    except OSError as exc:
        raise DwgError(EXIT_DEPENDENCY, f"converter could not start: {exc}") from exc
    log["returncode"] = p.returncode
    log["stdout_tail"] = (p.stdout or "")[-2000:]
    log["stderr_tail"] = (p.stderr or "")[-2000:]


def run_oda(dwg, out_tmp, log, exe, timeout):
    # ODAFileConverter <in dir> <out dir> <version> <type> <recurse> <audit> [filter]
    exe = find_exe(exe, "LDIP_ODA_EXE", "ODA File Converter")
    log["exe"] = str(exe)
    log["engine_version"] = "unknown"   # [GAP] K2: read from the installed product once chosen
    with tempfile.TemporaryDirectory(dir=out_tmp.parent) as td:
        run_external([str(exe), str(dwg.parent), td, "ACAD2018", "DXF", "0", "1", dwg.name], timeout, log)
        produced = Path(td) / (dwg.stem + ".dxf")
        if produced.is_file():
            shutil.move(str(produced), str(out_tmp))


def run_accore(dwg, out_tmp, log, exe, timeout):
    # accoreconsole.exe /i <dwg> /s <script> ; DXFOUT <file> Version 2018, 16 decimals. [GAP] verify on
    # the review workstation (K1/K2): prompt sequence can differ between releases.
    exe = find_exe(exe, "LDIP_ACCORE_EXE", "AutoCAD Core Console")
    log["exe"] = str(exe)
    log["engine_version"] = "unknown"
    with tempfile.TemporaryDirectory(dir=out_tmp.parent) as td:
        target = Path(td) / (dwg.stem + ".dxf")
        scr = Path(td) / "dxfout.scr"
        scr.write_text(f'FILEDIA 0\n_.DXFOUT "{target}" _V 2018 16\n', encoding="utf-8")
        run_external([str(exe), "/i", str(dwg), "/s", str(scr), "/l", "en-US"], timeout, log)
        if target.is_file():
            shutil.move(str(target), str(out_tmp))


def libredwg_exe(arg):
    """dwg2dxf path, or None when LibreDWG is not enabled (DF-1: not before licence review L-1)."""
    exe = arg or os.environ.get("LDIP_LIBREDWG_EXE")
    return Path(exe) if exe and Path(exe).is_file() else None


def run_libredwg(dwg, out_tmp, log, exe, timeout):
    exe = libredwg_exe(exe)
    if exe is None:
        raise DwgError(EXIT_DEPENDENCY, "LibreDWG not enabled (licence review L-1; set LDIP_LIBREDWG_EXE after it closes)")
    log["exe"] = str(exe)
    try:
        v = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=30)
        log["engine_version"] = (v.stdout or v.stderr).strip().splitlines()[-1] if (v.stdout or v.stderr).strip() else "unknown"
    except (OSError, subprocess.TimeoutExpired):
        log["engine_version"] = "unknown"
    run_external([str(exe), "-y", "--as", "r2018", "-o", str(out_tmp), str(dwg)], timeout, log)
    if out_tmp.is_file() and out_tmp.stat().st_size and log.get("returncode"):
        # dwg2dxf returns non-zero for recoverable read errors while still writing a DXF
        log["warnings"].append({"code": "ENGINE_READ_ERRORS",
                                "detail": f"dwg2dxf exit {log['returncode']}: the DXF was written; "
                                          + (log.get("stderr_tail") or "")[-200:].replace("\n", " ")})


def run_engine(engine, dwg, out_tmp, log, a):
    if engine == "ezdwg":
        try:
            run_ezdwg(dwg, out_tmp, log)
        except DwgError:
            raise
        except Exception as exc:   # engine failed on this file (corrupt or unsupported content)
            raise DwgError(EXIT_INPUT, f"ezdwg could not convert the file: {type(exc).__name__}: {exc}") from exc
    elif engine == "libredwg":
        run_libredwg(dwg, out_tmp, log, a.libredwg_exe, a.timeout)
    elif engine == "oda":
        run_oda(dwg, out_tmp, log, a.exe, a.timeout)
    else:
        run_accore(dwg, out_tmp, log, a.exe, a.timeout)
    if not out_tmp.is_file() or out_tmp.stat().st_size == 0:
        raise DwgError(EXIT_INPUT, f"{engine} produced no DXF (return code {log.get('returncode')})")


def run_chain(dwg, out_tmp, log, a):
    """K21-3 reader order: ezdwg -> LibreDWG (when enabled) -> owner step (local AutoCAD route)."""
    log["chain"] = []
    steps = ["ezdwg", "libredwg"]
    errors = []
    for eng in steps:
        if eng == "libredwg" and libredwg_exe(a.libredwg_exe) is None:
            log["chain"].append({"engine": eng, "result": "not_enabled",
                                 "detail": "licence review L-1 open (DF-1); LDIP_LIBREDWG_EXE not set"})
            continue
        sub = {"warnings": []}
        out_tmp.unlink(missing_ok=True)
        try:
            run_engine(eng, dwg, out_tmp, sub_log(log, sub), a)
        except DwgError as e:
            log["chain"].append({"engine": eng, "result": "failed", "code": e.code, "detail": e.detail[:400]})
            errors.append(f"{eng}: {e.detail[:300]}")
            continue
        log["chain"].append({"engine": eng, "result": "ok"})
        log["engine"] = eng
        for k, v in sub.items():
            if k != "warnings":
                log[k] = v
        log["warnings"].extend(sub["warnings"])
        if errors:
            log["fallback_from"] = errors[0]
            log["warnings"].append({"code": "ENGINE_FALLBACK", "detail": f"converted by {eng} after: " + "; ".join(errors)})
        return
    log["next"] = "local_autocad"
    raise DwgError(EXIT_DEPENDENCY, "no reader in the chain converted the file (" + "; ".join(errors or ["no step ran"])
                   + "); next: local AutoCAD route on the owner's computer (P-APS-1 / local connector)")


def sub_log(log, sub):
    sub["input"] = log["input"]
    return sub


def reusable(dwg, out, logp, engine):
    """The earlier log if it can be kept as is (same engine, same DWG, same DXF), else None."""
    try:
        prev = json.loads(Path(logp).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if prev.get("status") != "ok" or not Path(out).is_file() \
            or (prev.get("requested_engine") or prev.get("engine")) != engine:
        return None
    if (prev.get("input") or {}).get("sha256") != sha256_file(dwg):
        return None
    if (prev.get("output") or {}).get("sha256") != sha256_file(out):
        return None
    return prev


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True, choices=["chain", "ezdwg", "libredwg", "oda", "accoreconsole"])
    ap.add_argument("--dwg", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--exe")
    ap.add_argument("--libredwg-exe")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--reuse", action="store_true")
    a = ap.parse_args(argv)
    log = {"script": f"convert.py {__version__}", "engine": a.engine, "requested_engine": a.engine,
           "engine_version": None, "warnings": []}
    try:
        dwg = check_path(a.dwg, must_exist=True)
        out, logp = check_path(a.out), check_path(a.log)
        if dwg.suffix.lower() != ".dwg":
            raise DwgError(EXIT_INPUT, f"not a .dwg file: {dwg.name}")
        sig = dwg_version(dwg)
        if a.reuse:
            prev = reusable(dwg, out, logp, a.engine)
            if prev:
                emit({"status": "ok", "reused": True, "engine": prev.get("engine"), "engine_version": prev.get("engine_version"),
                      "acadver": sig, "warnings": len(prev.get("warnings", [])), "out": str(out), "log": str(logp)})
                return EXIT_OK
        log["input"] = {"path": dwg.as_posix(), "sha256": sha256_file(dwg), "bytes": dwg.stat().st_size,
                        "acadver": sig, "release": ACADVER.get(sig, "unknown")}
        out.parent.mkdir(parents=True, exist_ok=True)
        out_tmp = out.with_name(out.name + ".tmp")
        out_tmp.unlink(missing_ok=True)
        t0 = time.monotonic()
        if a.engine == "chain":
            run_chain(dwg, out_tmp, log, a)
        else:
            run_engine(a.engine, dwg, out_tmp, log, a)
        log["duration_s"] = round(time.monotonic() - t0, 1)
        os.replace(out_tmp, out)
        log["output"] = {"path": out.as_posix(), "sha256": sha256_file(out), "bytes": out.stat().st_size,
                         "dxf_version": "R2018"}
        log["status"] = "ok"
        atomic_write_json(logp, log)
        emit({"status": "ok", "engine": log["engine"], "engine_version": log["engine_version"],
              **({"fallback_from": log["fallback_from"][:120]} if log.get("fallback_from") else {}),
              "acadver": sig, "warnings": len(log["warnings"]), "out": str(out), "log": str(logp)})
        return EXIT_OK
    except DwgError as e:
        log.update({"status": "error", "code": e.code, "detail": e.detail})
        try:
            atomic_write_json(a.log, log)
        except OSError:
            pass
        emit({"status": "error", "code": e.code, "detail": e.detail, **({"next": log["next"]} if log.get("next") else {})})
        return e.code


if __name__ == "__main__":
    sys.exit(main())
