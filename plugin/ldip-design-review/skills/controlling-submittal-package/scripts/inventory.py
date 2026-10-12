#!/usr/bin/env python3
"""List the files of a submittal folder for standalone use of skill 6 (owner decision D1, 11 Oct 2026).

Usage: python inventory.py --root <submittal folder> --site QRWN|NRJS|NMAR [--sub <label>] --out <inventory.json>
Every file under --root is listed with its path relative to --root, extension, MIDP exchange format,
size, SHA-256 and the document number at the start of its name (None when the name carries none). A
folder ending in .gdb (file geodatabase) is one deliverable: its hash is the SHA-256 of the sorted
"relative path  file hash" lines. Office lock files (~$*), *.tmp and desktop.ini / thumbs.db are skipped
and counted. A .zip file is listed itself and each member is listed too (owner decision P-S6-5, 11 Oct
2026) as "<zip path>!/<member>", kind zip_member, hashed from its decompressed content (nothing is
written to disk). P-S6-7: a file without a number inside a numbered zip, in the folder extracted
from it, or in a numbered folder takes that number as `number_inherited`. Encrypted members, members of nested archives and members beyond ZIP_MAX_BYTES
(decompressed, per archive) are not read and are counted in zip_not_read. The same shape is written by merge_batches.py in a review run.
Exit: 0 written · 2 input error. Read-only on --root. No network, no shell calls.
"""
__version__ = "0.3.0"

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pkg_common import (EXIT_INPUT, EXIT_OK, IGNORED_NAMES, atomic_write_json, emit, fail, format_of,  # noqa: E402
                        number_from_name, path_ok, sha256_file)

SITES = ("QRWN", "NRJS", "NMAR")
ZIP_MAX_BYTES = 4 * 1024 ** 3     # decompressed bytes read per archive (guards against zip bombs)


def zip_members(path, rel):
    """Members of one archive: (entries, not_read list)."""
    out, not_read, total = [], [], 0
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        return [], [{"path": rel, "reason": f"archive unreadable: {type(exc).__name__}"}]
    with zf:
        for info in sorted(zf.infolist(), key=lambda i: i.filename):
            if info.is_dir():
                continue
            name = info.filename.rsplit("/", 1)[-1]
            mrel = f"{rel}!/{info.filename}"
            if info.flag_bits & 0x1:
                not_read.append({"path": mrel, "reason": "encrypted"})
                continue
            if total + info.file_size > ZIP_MAX_BYTES:
                not_read.append({"path": mrel, "reason": f"archive read limit {ZIP_MAX_BYTES} bytes reached"})
                continue
            h = hashlib.sha256()
            try:
                with zf.open(info) as fh:
                    for b in iter(lambda: fh.read(1 << 20), b""):
                        h.update(b)
            except (zipfile.BadZipFile, OSError, NotImplementedError, RuntimeError) as exc:
                not_read.append({"path": mrel, "reason": f"member unreadable: {type(exc).__name__}"})
                continue
            total += info.file_size
            ext = Path(name).suffix.lower()
            entry = {"path": mrel, "name": name, "ext": ext, "format": format_of(name), "bytes": info.file_size,
                     "sha256": h.hexdigest(), "number": number_from_name(name), "kind": "zip_member",
                     "container": rel}
            if ext == ".zip":
                not_read.append({"path": mrel, "reason": "nested archive not opened"})
            out.append(entry)
    return out, not_read


def gdb_hash(folder):
    lines = sorted(f"{p.relative_to(folder).as_posix()}  {sha256_file(p)}" for p in folder.rglob("*") if p.is_file())
    size = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest(), size


def scan(root):
    files, skipped, zip_not_read = [], [], []
    stack = [root]
    while stack:
        d = stack.pop()
        for p in sorted(d.iterdir(), key=lambda x: x.name):
            rel = p.relative_to(root).as_posix()
            if p.is_dir():
                if p.suffix.lower() == ".gdb":
                    h, size = gdb_hash(p)
                    files.append({"path": rel, "name": p.name, "ext": ".gdb", "format": "GDB", "bytes": size,
                                  "sha256": h, "number": number_from_name(p.name), "kind": "folder"})
                else:
                    stack.append(p)
                continue
            low = p.name.lower()
            if low in IGNORED_NAMES or low.startswith("~$") or low.endswith(".tmp"):
                skipped.append(rel)
                continue
            if not path_ok(p):
                skipped.append(rel)
                continue
            files.append({"path": rel, "name": p.name, "ext": p.suffix.lower(), "format": format_of(p),
                          "bytes": p.stat().st_size, "sha256": sha256_file(p), "number": number_from_name(p.name),
                          "kind": "file"})
            if low.endswith(".zip"):
                members, nr = zip_members(p, rel)
                files += members
                zip_not_read += nr
    attach_numbers(files)
    files.sort(key=lambda f: f["path"])
    return files, sorted(skipped), zip_not_read


def attach_numbers(files):
    """P-S6-7 (owner approved 12 Oct 2026): a file without a number that belongs to a numbered deliverable
    takes that deliverable's number, recorded as `number_inherited` (the file keeps `number: null`):
      - a member of a numbered zip, or the same member found extracted next to the zip
        (<zip folder>/<member path> or <zip folder>/<zip stem>/<member path>);
      - a file inside a folder whose name carries a number (an extracted model folder)."""
    zips = [f for f in files if f["kind"] == "file" and f["ext"] == ".zip" and f["number"]]
    member_map = {}
    for z in zips:
        zdir = z["path"].rsplit("/", 1)[0] if "/" in z["path"] else ""
        stem = z["name"][:-4]
        for m in files:
            if m["kind"] == "zip_member" and m.get("container") == z["path"]:
                mp = m["path"].split("!/", 1)[1]
                for cand in (f"{zdir}/{mp}" if zdir else mp, f"{zdir}/{stem}/{mp}" if zdir else f"{stem}/{mp}"):
                    member_map[cand] = z
    for f in files:
        if f["number"]:
            continue
        if f["kind"] == "zip_member":
            z = next((x for x in zips if x["path"] == f.get("container")), None)
            if z:
                f["number_inherited"] = {"number": z["number"], "from": "zip", "source": z["path"]}
            continue
        if f["path"] in member_map:
            z = member_map[f["path"]]
            f["number_inherited"] = {"number": z["number"], "from": "zip", "source": z["path"]}
            continue
        parts = f["path"].split("/")[:-1]
        for i in range(len(parts), 0, -1):
            n = number_from_name(parts[i - 1])
            if n:
                f["number_inherited"] = {"number": n, "from": "folder", "source": "/".join(parts[:i])}
                break


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--site", required=True, choices=SITES)
    ap.add_argument("--sub", default="SA")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    root = Path(a.root)
    if not root.is_dir():
        return fail(EXIT_INPUT, f"--root is not a folder: {root}")
    if not path_ok(a.out):
        return fail(EXIT_INPUT, "output path longer than 250 characters")
    out_res = Path(a.out).resolve()
    try:
        out_res.relative_to(root.resolve())
        return fail(EXIT_INPUT, "--out must be outside the submittal folder (the folder is read-only)")
    except ValueError:
        pass
    files, skipped, zip_not_read = scan(root)
    doc = {"tool": {"name": "inventory.py", "version": __version__}, "mode": "standalone",
           "root": str(root.resolve()), "site": a.site, "sub_id": a.sub, "files": files, "skipped": skipped,
           "zip_not_read": zip_not_read,
           "counts": {"files": len(files), "with_number": sum(1 for f in files if f["number"]),
                      "skipped": len(skipped), "zip_members": sum(1 for f in files if f["kind"] == "zip_member"),
                      "zip_not_read": len(zip_not_read),
                      "number_inherited": sum(1 for f in files if f.get("number_inherited"))}}
    atomic_write_json(a.out, doc)
    emit({"status": "ok", "out": str(a.out), **doc["counts"]})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
