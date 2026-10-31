"""Shared helpers for skill 1 extracting-dwg-data (SPC-0006 §2.3). No network, no shell calls
except optional LibreDWG/ODA discovery helpers used by convert.py.

0.2.0 (Amendment O-a): nested-content check (O-a.3.1), insert_owner_chain (O-a.3.2),
optional LibreDWG JSON cross-check helper.
"""
__version__ = "0.2.0"

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

EXIT_OK, EXIT_INPUT, EXIT_DEPENDENCY, EXIT_SCHEMA, EXIT_REFUSED = 0, 2, 3, 4, 5
MAX_PATH = 250   # SPC-0002 R1
NESTED_TYPES = ("INSERT", "TEXT", "MTEXT")
# AutoCAD common-entity-header entmode: 1 = paper space, 2 = model space → top-level
TOP_LEVEL_ENTMODES = frozenset({1, 2})
CROSS_CHECK_TOL_M = 0.001


class DwgError(Exception):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code, self.detail = code, detail


def check_path(path, must_exist=False):
    p = Path(path)
    if len(str(p.resolve())) > MAX_PATH:
        raise DwgError(EXIT_INPUT, f"path longer than {MAX_PATH} characters: {path}")
    if must_exist and not p.is_file():
        raise DwgError(EXIT_INPUT, f"file not found: {path}")
    return p


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_json(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False))


def r3(x):
    """Coordinates to 0.001 m (SPC-0006 §2.3.6)."""
    return None if x is None else round(float(x), 3)


def r2(x):
    """Areas to 0.01 m2."""
    return None if x is None else round(float(x), 2)


# ---------------------------------------------------------------------------
# O-a.3.1 Nested-content check (raw DWG vs DXF, by layer name — not handles)
# ---------------------------------------------------------------------------

def _empty_type_counts():
    return {t: 0 for t in NESTED_TYPES}


def count_raw_content_by_layer(dwg_path):
    """Count INSERT / TEXT / MTEXT per layer name from ezdwg raw decoders.

    Returns {layer_name: {"INSERT": n, "TEXT": n, "MTEXT": n}}.
    """
    from ezdwg.raw import (
        decode_insert_entities, decode_text_entities, decode_mtext_entities,
        decode_layer_names, decode_object_entity_layer_handles,
    )
    path = str(Path(dwg_path).resolve())
    layer_names = dict(decode_layer_names(path))
    counts = defaultdict(_empty_type_counts)

    def add(rows, kind):
        handles = [r[0] for r in rows]
        if not handles:
            return
        ent_layers = dict(decode_object_entity_layer_handles(path, handles))
        for h in handles:
            lname = layer_names.get(ent_layers.get(h), "0")
            counts[lname][kind] += 1

    add(list(decode_insert_entities(path)), "INSERT")
    add(list(decode_text_entities(path)), "TEXT")
    add(list(decode_mtext_entities(path)), "MTEXT")
    return {k: dict(v) for k, v in sorted(counts.items())}


def count_dxf_content_by_layer(dxf_path):
    """Count INSERT / TEXT / MTEXT per layer over all block records of a DXF.

    Amendment O-a: every block record (model, paper, bound-xref, anonymous *U, …).
    Handles are not compared — layer names only.
    """
    import ezdxf
    doc = ezdxf.readfile(str(dxf_path))
    counts = defaultdict(_empty_type_counts)
    for block in doc.blocks:
        for e in block:
            kind = e.dxftype()
            if kind in NESTED_TYPES:
                counts[e.dxf.layer][kind] += 1
    return {k: dict(v) for k, v in sorted(counts.items())}


def nested_content_diff(raw_counts, dxf_counts):
    """Compare raw vs DXF counts by layer name.

    Returns a result dict:
      ok: True when no layer has raw > DXF for INSERT/TEXT/MTEXT
      layers_with_loss: count of layers with any loss
      entities_lost: total entities lost (sum of positive raw−dxf per type/layer)
      by_layer: {layer: {type: lost_n, ...}, ...} only layers with loss
    """
    layers = set(raw_counts) | set(dxf_counts)
    by_layer = {}
    entities_lost = 0
    for layer in sorted(layers):
        raw = raw_counts.get(layer) or _empty_type_counts()
        dxf = dxf_counts.get(layer) or _empty_type_counts()
        lost = {}
        for kind in NESTED_TYPES:
            delta = int(raw.get(kind, 0)) - int(dxf.get(kind, 0))
            if delta > 0:
                lost[kind] = delta
                entities_lost += delta
        if lost:
            by_layer[layer] = lost
    return {
        "ok": entities_lost == 0,
        "layers_with_loss": len(by_layer),
        "entities_lost": entities_lost,
        "by_layer": by_layer,
    }


def check_nested_content(dwg_path, dxf_path):
    """O-a.3.1: run raw vs DXF nested-content check. Raises nothing; returns diff dict + warning."""
    try:
        raw = count_raw_content_by_layer(dwg_path)
        dxf = count_dxf_content_by_layer(dxf_path)
        diff = nested_content_diff(raw, dxf)
    except Exception as exc:  # informative: keep DXF; warn that the check did not run
        reason = f"{type(exc).__name__}: {str(exc)[:200]}"
        return {
            "ok": True,
            "layers_with_loss": 0,
            "entities_lost": 0,
            "by_layer": {},
            "checked": False,
            "reason": reason,
            "warning": {
                "code": "NESTED_CHECK_NOT_RUN",
                "detail": f"nested-content check could not run: {reason}",
            },
        }
    diff["checked"] = True
    if not diff["ok"]:
        diff["warning"] = {
            "code": "NESTED_CONTENT_LOSS",
            "detail": (
                f"DXF lost nested INSERT/TEXT/MTEXT vs raw DWG: "
                f"{diff['layers_with_loss']} layer(s), {diff['entities_lost']} entit(y/ies)"
            ),
            "layers_with_loss": diff["layers_with_loss"],
            "entities_lost": diff["entities_lost"],
        }
    return diff


def dxf_loadable(dxf_path):
    """True when ezdxf can open the DXF; else (False, error message).

    LibreDWG can write a DXF that ezdxf rejects (e.g. QRWN 'Invalid sort handle code 331').
    """
    try:
        import ezdxf
        ezdxf.readfile(str(dxf_path))
        return True, None
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# O-a.3.2 Owner helper — insert_owner_chain
# ---------------------------------------------------------------------------

def _owner_chain_from_placements(placements, insert_handles):
    """Build owner chains from decode_entity_placements rows.

    entmode 1/2 = top-level (ignore owner). Walk owner handles otherwise;
    on a repeated handle, truncate and warn OWNER_CYCLE.
    """
    by_h = {h: (entmode, owner) for h, entmode, owner in placements}
    out = {}
    for h in insert_handles:
        if h not in by_h:
            continue
        entmode, owner = by_h[h]
        warnings = []
        if entmode in TOP_LEVEL_ENTMODES:
            out[h] = {
                "handle": h, "entmode": entmode, "owner": owner,
                "top_level": True, "chain": [], "warnings": warnings,
            }
            continue
        chain, seen = [], set()
        cur = owner
        while cur and cur != 0:
            if cur in seen:
                warnings.append({
                    "code": "OWNER_CYCLE",
                    "detail": f"owner chain cycle at handle {cur}; dropped parents after first visit",
                    "handle": cur,
                })
                break
            seen.add(cur)
            chain.append(cur)
            nxt = by_h.get(cur)
            if nxt is None:
                break
            em, ow = nxt
            if em in TOP_LEVEL_ENTMODES:
                break
            cur = ow
        out[h] = {
            "handle": h, "entmode": entmode, "owner": owner,
            "top_level": False, "chain": chain, "warnings": warnings,
        }
    return out


def _owner_chain_cycle_fallback(inserts, owners):
    """When entmode placements unavailable: walk decode_insert_owner_handles with cycle rule."""
    owner_of = {h: o for h, o in owners}
    insert_handles = [r[0] for r in inserts]
    out = {}
    for h in insert_handles:
        warnings = []
        chain, seen = [], set()
        cur = owner_of.get(h)
        while cur and cur != 0:
            if cur in seen:
                warnings.append({
                    "code": "OWNER_CYCLE",
                    "detail": f"owner chain cycle at handle {cur}; dropped parents after first visit",
                    "handle": cur,
                })
                break
            seen.add(cur)
            chain.append(cur)
            cur = owner_of.get(cur)
        out[h] = {
            "handle": h, "entmode": None, "owner": owner_of.get(h),
            "top_level": False, "chain": chain, "warnings": warnings,
            "fallback": "cycle_rule",
        }
    return out


def insert_owner_chain(path):
    """O-a.3.2: INSERT owner chains from ezdwg raw placements.

    Uses ``decode_entity_placements`` → (handle, entmode, owner). entmode 1/2 =
    top-level (ignore owner). When placements cannot be read, falls back to the
    owner-handle cycle rule (G1 / S33 §3B).

    Returns ``{"source": "placements"|"cycle_fallback", "inserts": {handle: info}}``.
    QRWN probe: handle 18 → entmode 2, owner 0 → top_level True, chain [].
    """
    from ezdwg.raw import (
        decode_entity_placements, decode_insert_entities, decode_insert_owner_handles,
    )
    p = str(Path(path).resolve())
    inserts = list(decode_insert_entities(p))
    insert_handles = [r[0] for r in inserts]
    try:
        placements = list(decode_entity_placements(p))
        if not placements:
            raise RuntimeError("no placements")
        return {
            "source": "placements",
            "inserts": _owner_chain_from_placements(placements, insert_handles),
        }
    except Exception:
        owners = list(decode_insert_owner_handles(p))
        return {
            "source": "cycle_fallback",
            "inserts": _owner_chain_cycle_fallback(inserts, owners),
        }


# ---------------------------------------------------------------------------
# Optional LibreDWG JSON ↔ ezdwg INSERT cross-check (O-a --cross-check)
# ---------------------------------------------------------------------------

def find_dwgread(libredwg_exe=None):
    """Locate ``dwgread`` next to dwg2dxf or on PATH."""
    env = os.environ.get("LDIP_DWGREAD_EXE")
    if env and Path(env).is_file():
        return Path(env)
    if libredwg_exe:
        sibling = Path(libredwg_exe).resolve().parent / "dwgread"
        if sibling.is_file():
            return sibling
    which = shutil.which("dwgread")
    return Path(which) if which else None


def libredwg_version_string(libredwg_exe=None, timeout=30):
    """Version from ``dwgread --version`` (Amendment O-a); fallback dwg2dxf --version."""
    dwgread = find_dwgread(libredwg_exe)
    exe = dwgread or (Path(libredwg_exe) if libredwg_exe else None)
    if exe is None or not Path(exe).is_file():
        return "unknown"
    try:
        v = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=timeout)
        text = (v.stdout or v.stderr or "").strip()
        return text.splitlines()[-1] if text else "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def _ezdwg_insert_map(dwg_path):
    from ezdwg.raw import decode_insert_entities
    out = {}
    for row in decode_insert_entities(str(Path(dwg_path).resolve())):
        h, x, y, z, sx, sy, sz, rot, name = row
        out[int(h)] = {"x": float(x), "y": float(y), "z": float(z),
                       "sx": float(sx), "sy": float(sy), "sz": float(sz),
                       "rot": float(rot), "name": name}
    return out


def _dwgread_entity_name(obj):
    """LibreDWG 0.14.8679 JSON: name in ``entity`` (e.g. INSERT); ``type`` is often an int."""
    for key in ("entity", "object", "dxftype", "type"):
        v = obj.get(key)
        if isinstance(v, str) and v.strip():
            return v.upper()
    return ""


def _dwgread_handle(obj):
    """Handle may be int, hex string, or a list (use the last item — LibreDWG 0.14.8679)."""
    handle = obj.get("handle") or obj.get("Handle") or obj.get("object_handle")
    if isinstance(handle, (list, tuple)):
        handle = handle[-1] if handle else None
    if handle is None:
        return None
    try:
        return int(handle) if isinstance(handle, int) else int(str(handle), 0)
    except (TypeError, ValueError):
        return None


def _walk_dwgread_inserts(obj, found):
    """Collect INSERT-like objects from dwgread -O JSON (LibreDWG 0.14.8679 + older shapes)."""
    if isinstance(obj, dict):
        typ = _dwgread_entity_name(obj)
        h = _dwgread_handle(obj)
        if typ == "INSERT" and h is not None:
            ins = (obj.get("ins_pt") or obj.get("insert") or obj.get("INS_PT")
                   or obj.get("point") or {})
            if isinstance(ins, (list, tuple)):
                x = float(ins[0]) if len(ins) > 0 else 0.0
                y = float(ins[1]) if len(ins) > 1 else 0.0
                z = float(ins[2]) if len(ins) > 2 else 0.0
            elif isinstance(ins, dict):
                x = float(ins.get("x", ins.get("X", 0)) or 0)
                y = float(ins.get("y", ins.get("Y", 0)) or 0)
                z = float(ins.get("z", ins.get("Z", 0)) or 0)
            else:
                x = float(obj.get("x", obj.get("X", 0)) or 0)
                y = float(obj.get("y", obj.get("Y", 0)) or 0)
                z = float(obj.get("z", obj.get("Z", 0)) or 0)
            scale = obj.get("scale")
            if isinstance(scale, (list, tuple)) and len(scale) >= 2:
                sx = float(scale[0])
                sy = float(scale[1])
                sz = float(scale[2]) if len(scale) > 2 else 1.0
            else:
                sx = float(obj.get("scale_x", obj.get("xscale", obj.get("SX", 1))) or 1)
                sy = float(obj.get("scale_y", obj.get("yscale", obj.get("SY", 1))) or 1)
                sz = float(obj.get("scale_z", obj.get("zscale", obj.get("SZ", 1))) or 1)
            found[h] = {"x": x, "y": y, "z": z, "sx": sx, "sy": sy, "sz": sz}
        for v in obj.values():
            _walk_dwgread_inserts(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _walk_dwgread_inserts(v, found)


def cross_check_inserts(dwg_path, libredwg_exe=None, tol_m=CROSS_CHECK_TOL_M, timeout=600,
                        temp_dir=None):
    """Compare INSERT pos/scale by handle: ``dwgread -O JSON`` vs ezdwg (tol 0.001 m).

    Writes JSON under a TemporaryDirectory (beside ``temp_dir`` / the output folder when given),
    never next to the source DWG. Never raises — failures return ``checked: false`` + reason.
    """
    try:
        dwgread = find_dwgread(libredwg_exe)
        if dwgread is None:
            return {"checked": False, "reason": "dwgread not found (set LDIP_DWGREAD_EXE or PATH)"}
        ezdwg_map = _ezdwg_insert_map(dwg_path)
        parent = Path(temp_dir) if temp_dir else None
        td_kw = {"dir": str(parent)} if parent is not None and parent.is_dir() else {}
        with tempfile.TemporaryDirectory(**td_kw) as td:
            td_json = Path(td) / "dwgread.json"
            p = subprocess.run(
                [str(dwgread), "-O", "JSON", "-o", str(td_json), str(dwg_path)],
                capture_output=True, text=True, timeout=timeout,
            )
            if not td_json.is_file():
                return {"checked": False, "reason": f"dwgread produced no JSON (exit {p.returncode})",
                        "stderr_tail": (p.stderr or "")[-500:]}
            data = json.loads(td_json.read_text(encoding="utf-8", errors="replace"))
            libre_map = {}
            _walk_dwgread_inserts(data, libre_map)
        mismatches = []
        matched = 0
        for h, a in ezdwg_map.items():
            b = libre_map.get(h)
            if b is None:
                mismatches.append({"handle": h, "kind": "missing_in_dwgread"})
                continue
            for key in ("x", "y", "z", "sx", "sy", "sz"):
                if abs(a[key] - b[key]) > tol_m:
                    mismatches.append({"handle": h, "kind": "value", "field": key,
                                       "ezdwg": a[key], "dwgread": b[key]})
                    break
            else:
                matched += 1
        for h in set(libre_map) - set(ezdwg_map):
            mismatches.append({"handle": h, "kind": "missing_in_ezdwg"})
        return {
            "checked": True,
            "tol_m": tol_m,
            "ezdwg_inserts": len(ezdwg_map),
            "dwgread_inserts": len(libre_map),
            "matched": matched,
            "mismatches": len(mismatches),
            "missing_in_dwgread": sum(1 for m in mismatches if m["kind"] == "missing_in_dwgread"),
            "missing_in_ezdwg": sum(1 for m in mismatches if m["kind"] == "missing_in_ezdwg"),
            "value_mismatches": sum(1 for m in mismatches if m["kind"] == "value"),
            "mismatch_samples": mismatches[:20],
        }
    except Exception as exc:
        return {"checked": False, "reason": f"{type(exc).__name__}: {exc}"}
