#!/usr/bin/env python3
"""DWG → DXF with a switchable engine (SPC-0006 §4.1.3, §4.1.5).

Engines:
  ezdwg          Python package (MIT), reads DWG R2000–R2018; under test (SPC-0006 Appendix A).
  oda            ODA File Converter (external program; --exe or env LDIP_ODA_EXE). Licence: K2 [GAP].
  accoreconsole  AutoCAD Core Console, Windows only (--exe or env LDIP_ACCORE_EXE). Script DXFOUT.

Usage: python convert.py --engine ezdwg|oda|accoreconsole --dwg <file.dwg> --out <file.dxf>
                         --log <file.convert.json> [--exe <converter>] [--timeout 600] [--reuse]
--reuse (0.1.1, D9): keep an earlier conversion when its log says ok for the same engine, its input
hash equals the DWG on disk and its output hash equals the DXF on disk; nothing is rewritten.
Writes the DXF (via a temporary name, then rename) and a converter log: engine and version, input
hash and DWG version, output hash, duration, return code, entities the engine skipped.
Exit: 0 converted · 2 input error or no DXF produced · 3 engine missing, DWG version not supported,
or timeout. The only external program called is the named converter (SPC-0006 §2.3.5).
"""
__version__ = "0.1.2"

import argparse
import json
import os
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
EZDWG_PINNED = "0.12.9"   # D13 [AGREED] 5 Oct 2026: run environment pins ezdwg to this version


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
    log["returncode"] = 0


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


def reusable(dwg, out, logp, engine):
    """The earlier log if it can be kept as is (same engine, same DWG, same DXF), else None."""
    try:
        prev = json.loads(Path(logp).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if prev.get("status") != "ok" or prev.get("engine") != engine or not Path(out).is_file():
        return None
    if (prev.get("input") or {}).get("sha256") != sha256_file(dwg):
        return None
    if (prev.get("output") or {}).get("sha256") != sha256_file(out):
        return None
    return prev


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True, choices=["ezdwg", "oda", "accoreconsole"])
    ap.add_argument("--dwg", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--exe")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--reuse", action="store_true")
    a = ap.parse_args(argv)
    log = {"script": f"convert.py {__version__}", "engine": a.engine, "engine_version": None, "warnings": []}
    try:
        dwg = check_path(a.dwg, must_exist=True)
        out, logp = check_path(a.out), check_path(a.log)
        if dwg.suffix.lower() != ".dwg":
            raise DwgError(EXIT_INPUT, f"not a .dwg file: {dwg.name}")
        sig = dwg_version(dwg)
        if a.reuse:
            prev = reusable(dwg, out, logp, a.engine)
            if prev:
                emit({"status": "ok", "reused": True, "engine": a.engine, "engine_version": prev.get("engine_version"),
                      "acadver": sig, "warnings": len(prev.get("warnings", [])), "out": str(out), "log": str(logp)})
                return EXIT_OK
        log["input"] = {"path": dwg.as_posix(), "sha256": sha256_file(dwg), "bytes": dwg.stat().st_size,
                        "acadver": sig, "release": ACADVER.get(sig, "unknown")}
        out.parent.mkdir(parents=True, exist_ok=True)
        out_tmp = out.with_name(out.name + ".tmp")
        out_tmp.unlink(missing_ok=True)
        t0 = time.monotonic()
        if a.engine == "ezdwg":
            try:
                run_ezdwg(dwg, out_tmp, log)
            except DwgError:
                raise
            except Exception as exc:   # engine failed on this file (corrupt or unsupported content)
                raise DwgError(EXIT_INPUT, f"ezdwg could not convert the file: {type(exc).__name__}: {exc}") from exc
        elif a.engine == "oda":
            run_oda(dwg, out_tmp, log, a.exe, a.timeout)
        else:
            run_accore(dwg, out_tmp, log, a.exe, a.timeout)
        log["duration_s"] = round(time.monotonic() - t0, 1)
        if not out_tmp.is_file() or out_tmp.stat().st_size == 0:
            raise DwgError(EXIT_INPUT, f"converter produced no DXF (return code {log.get('returncode')})")
        os.replace(out_tmp, out)
        log["output"] = {"path": out.as_posix(), "sha256": sha256_file(out), "bytes": out.stat().st_size,
                         "dxf_version": "R2018"}
        log["status"] = "ok"
        atomic_write_json(logp, log)
        emit({"status": "ok", "engine": a.engine, "engine_version": log["engine_version"],
              "acadver": sig, "warnings": len(log["warnings"]), "out": str(out), "log": str(logp)})
        return EXIT_OK
    except DwgError as e:
        log.update({"status": "error", "code": e.code, "detail": e.detail})
        try:
            atomic_write_json(a.log, log)
        except OSError:
            pass
        emit({"status": "error", "code": e.code, "detail": e.detail})
        return e.code


if __name__ == "__main__":
    sys.exit(main())
