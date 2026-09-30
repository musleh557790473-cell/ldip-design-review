"""Shared helpers for the ldip-design-review plugin scripts and guard hook.

Conventions: SPC-0006 Rev B §2.3 (exit codes, atomic writes, pathlib, 250-char
limit, determinism, no network, no shell).
"""
__version__ = "0.2.0"

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

# SPC-0006 §2.3.3
EXIT_OK = 0
EXIT_INPUT = 2
EXIT_DEPENDENCY = 3
EXIT_SCHEMA = 4
EXIT_REFUSED = 5

MAX_PATH = 250  # SPC-0002 R1, SPC-0006 §2.3.4
RIYADH = timezone(timedelta(hours=3), "Asia/Riyadh")  # no DST in KSA
SITES = ("QRWN", "NRJS", "NMAR")  # SPC-0002 §4.1
ACTIVE_RUN_FILE = "runs/_active_run.json"
RUN_ID_RE = re.compile(r"^(QRWN|NMAR|NRJS)-[A-Za-z0-9._-]+-[0-9]{8}T[0-9]{4}$")  # SPC-0003
SUB_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")  # short form, no spaces (SPC-0002 §4.2)


class ScriptError(Exception):
    """Carries an SPC-0006 exit code."""

    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def now_riyadh():
    return datetime.now(RIYADH)


def iso_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def check_path_len(p):
    if len(str(p)) > MAX_PATH:
        raise ScriptError(EXIT_INPUT, f"path longer than {MAX_PATH} characters: {p}")
    return p


def sha256_file(path, bufsize=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(bufsize)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def atomic_write_json(path, obj):
    """Write <path>.tmp then rename (SPC-0002 §6.2). Sorted keys (§2.3.6)."""
    path = Path(path)
    check_path_len(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        os.replace(tmp, path)
    except OSError as exc:  # e.g. file locked (R3)
        raise ScriptError(EXIT_INPUT, f"rename failed for {path}: {exc}") from exc


def append_jsonl(path, obj):
    path = Path(path)
    check_path_len(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, sort_keys=True, ensure_ascii=False) + "\n")


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ScriptError(EXIT_INPUT, f"file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ScriptError(EXIT_INPUT, f"invalid JSON in {path}: {exc}") from exc


# ---------------------------------------------------------------- glob logic
_GLOB_CACHE = {}


def _glob_to_regex(pattern):
    """'**' = any depth (incl. zero dirs when followed by '/'), '*' = one segment part, '?' = one char."""
    if pattern in _GLOB_CACHE:
        return _GLOB_CACHE[pattern]
    i, out = 0, []
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    rx = re.compile("^" + "".join(out) + "$")
    _GLOB_CACHE[pattern] = rx
    return rx


def glob_match(rel_posix, pattern):
    return bool(_glob_to_regex(pattern).match(rel_posix))


def any_match(rel_posix, patterns):
    return any(glob_match(rel_posix, p) for p in patterns)


def expand(patterns, values):
    out = []
    for p in patterns:
        for k, v in values.items():
            if v is not None:
                p = p.replace(k, v)
        if "{" in p:  # unresolved placeholder -> pattern can never match
            continue
        out.append(p)
    return out


def to_posix_abs(path_str, cwd):
    """Normalise a tool path (Windows or POSIX, relative or absolute) to an absolute POSIX string.
    Resolves '..' lexically and follows symlinks when the path exists (SPC-0005 §7.2)."""
    s = str(path_str).replace("\\", "/")
    p = Path(s)
    if not p.is_absolute():
        p = Path(str(cwd).replace("\\", "/")) / p
    try:
        p = p.resolve(strict=False)
    except (OSError, RuntimeError):
        p = Path(os.path.normpath(str(p)))
    return PurePosixPath(str(p).replace("\\", "/")).as_posix()


def rel_to_root(abs_posix, root_posix):
    root = root_posix.rstrip("/")
    if abs_posix == root:
        return ""
    if abs_posix.startswith(root + "/"):
        return abs_posix[len(root) + 1:]
    return None


def configured_review_root(hooks_dir=None):
    """LDIP_Review root as the guard hook sees it (P-S3-8): env LDIP_REVIEW_ROOT, else
    guard_config.json {"ldip_review_root": ...} in $CLAUDE_PLUGIN_DATA or the plugin hooks folder.
    Returns an absolute POSIX string, or None when not configured."""
    root = os.environ.get("LDIP_REVIEW_ROOT")
    if not root:
        for d in (os.environ.get("CLAUDE_PLUGIN_DATA"), str(hooks_dir) if hooks_dir else None):
            if d and (Path(d) / "guard_config.json").is_file():
                root = json.loads((Path(d) / "guard_config.json").read_text(encoding="utf-8")).get("ldip_review_root")
                break
    return to_posix_abs(root, "/") if root else None
