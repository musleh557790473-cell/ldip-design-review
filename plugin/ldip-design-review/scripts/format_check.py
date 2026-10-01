#!/usr/bin/env python3
"""Format checker run by the Orchestrator after every sub-agent step (brief §4.3a; SPC-0005 §6; SPC-0006 §5).

Usage:
  python format_check.py <agent> <run-id> --root R --summary RETURN_SUMMARY.json
                         [--instance 1] [--attempt 1] [--host COMPUTERNAME]

<agent> is one of: intake-batch, intake-package, gis-cadastral, verifier, outputs-register.
--summary is the agent's final message (SPC-0005 §4.8) saved by the Orchestrator as a file.

Checks (SPC-0005 §6.3): S0 return summary · S1 files / .tmp / OneDrive conflict copies · S2 JSON
Schema + identity (intake-batch: batch manifests and ingestion files, P-S3-19) · S3 script exit codes · FC1 confidence = strongest evidence tier · FC2 earlier
fields unchanged · FC3 artifact hashes · FC4 input hashes · FC5 rule exists, not inactive, status
within the rule cap · FC7 every candidate once in verified.json · FC8 every verified finding
registered, gate PENDING · C3 batch ownership (P-S3-4, intake-batch).

Every result is appended to runs/<run>/format_checks.json. The one-line stdout summary carries
"result" (pass|fail) and "retry_errors" — the only text the Orchestrator may pass back to the agent
on a retry (SPC-0005 §6.5). Exit 0 = the check ran; 2 = input error. This script never reads
findings/<run>/reasoning/**. No network, no shell calls.
"""
__version__ = "0.4.0"

import argparse
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ldip_common import (  # noqa: E402
    parse_summary,
    EXIT_DEPENDENCY, EXIT_INPUT, EXIT_OK, RUN_ID_RE, ScriptError, any_match, atomic_write_json, expand,
    iso_utc, read_json, sha256_file,
)
import check_batches  # noqa: E402

PLUGIN_ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or HERE.parent)
# FC5 uses skill 8's function: one implementation of the rule cap (owner, 30 Sep 2026).
sys.path.insert(0, str(PLUGIN_ROOT / "skills" / "applying-ldip-rulebook" / "scripts"))
from rulebook_lib import cap_errors  # noqa: E402
SCHEMA_PATH = PLUGIN_ROOT / "schemas" / "finding.schema.json"
INGESTION_SCHEMA = PLUGIN_ROOT / "schemas" / "ingestion.schema.json"
BATCH_SCHEMA = PLUGIN_ROOT / "schemas" / "batch_manifest.schema.json"
MATRIX_PATH = PLUGIN_ROOT / "hooks" / "access_matrix.json"

SUMMARY_KEYS = ("agent", "instance", "run_id", "status", "files_written", "counts", "rules_skipped",
                "gaps", "inconsistencies", "errors")  # SPC-0005 §4.8
TIER_CONF = {"deterministic": "High", "extracted_text_crop": "Medium", "visual": "Low"}  # brief §5.4
CONF_RANK = {"High": 3, "Medium": 2, "Low": 1}

AGENTS = {
    "intake-batch": {"stage": None, "code": None, "files": [], "batch_check": True},
    "intake-package": {"stage": "candidate", "code": "INT", "files": ["findings/{run}/candidates/intake.json"]},
    "gis-cadastral": {"stage": "candidate", "code": "GIS", "files": ["findings/{run}/candidates/gis.json"]},
    "verifier": {"stage": "verified", "code": None, "files": ["findings/{run}/verified.json"],
                 "earlier": "candidates", "fc7": True},
    "outputs-register": {"stage": "registered", "code": None, "files": ["outputs/{run}/registered.json"],
                         "earlier": "verified", "fc8": True},
}
# Fields a later stage may add; everything else from the earlier stage must be unchanged (FC2).
ADDED_BY = {"verified": {"verification", "stage"},
            "registered": {"stage", "commercial", "addressee", "output_route", "gate", "register_no", "links"}}


class Report:
    def __init__(self):
        self.checks, self.errors, self.notes = {}, [], []

    def run(self, cid):
        self.checks.setdefault(cid, "pass")

    def fail(self, cid, message, file=None, path=None):
        self.checks[cid] = "fail"
        self.errors.append({"check": cid, "file": file, "path": path, "message": message})

    def skip(self, cid, why):
        if self.checks.get(cid) != "fail":
            self.checks[cid] = "not_run"
        self.notes.append(f"{cid} not run: {why}")


def load_validator():
    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError as exc:
        raise ScriptError(EXIT_DEPENDENCY, "jsonschema is not installed") from exc
    schema = read_json(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validator_for(path):
    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError as exc:
        raise ScriptError(EXIT_DEPENDENCY, "jsonschema is not installed") from exc
    return Draft202012Validator(read_json(path), format_checker=FormatChecker())


def check_ingestion(rep, root, manifest):
    """S2 for intake-batch (P-S3-19): each b<nn>/batch_manifest.json against batch_manifest.schema.json
    with run identity and counts; each *.dwg.json / *.pdf.json against ingestion.schema.json, with the
    file name matching its source and the source hash matching the manifest entry."""
    rep.run("S2")
    base = root / "0.ai_context" / manifest["site"] / manifest["sub_id"]
    vb, vi = validator_for(BATCH_SCHEMA), validator_for(INGESTION_SCHEMA)
    for bdir in sorted(p for p in base.glob("b*") if p.is_dir()):
        mp = bdir / "batch_manifest.json"
        rel_m = mp.relative_to(root).as_posix()
        if not mp.is_file():
            continue                                  # reported by C3
        try:
            m = read_json(mp)
        except ScriptError as e:
            rep.fail("S2", f"batch manifest unreadable: {e.detail}", file=rel_m)
            continue
        errs = list(vb.iter_errors(m))
        for e in errs[:20]:
            rep.fail("S2", e.message[:200], file=rel_m, path="/" + "/".join(map(str, e.absolute_path)))
        if errs:
            continue
        for k, want in (("run_id", manifest.get("run_id")), ("site", manifest["site"]), ("sub_id", manifest["sub_id"]),
                        ("batch", bdir.name)):
            if want is not None and m[k] != want:
                rep.fail("S2", f"{k} '{m[k]}' differs from the run ('{want}')", file=rel_m, path=f"/{k}")
        tally = {s_: sum(1 for f in m["files"] if f["status"] == s_) for s_ in ("extracted", "failed", "skipped")}
        if tally != m["counts"]:
            rep.fail("S2", f"counts {m['counts']} differ from files[] {tally}", file=rel_m, path="/counts")
        for i, f in enumerate(m["files"]):
            if f["status"] != "extracted" or not f["output"]:
                continue
            out = str(f["output"]).replace("\\", "/")
            op = root / out if "/" in out else bdir / out
            rel_o = op.relative_to(root).as_posix() if op.is_relative_to(root) else out
            if not op.is_file():
                rep.fail("S2", "listed output not found", file=rel_m, path=f"/files/{i}/output")
                continue
            try:
                ing = read_json(op)
            except ScriptError as e:
                rep.fail("S2", f"ingestion file unreadable: {e.detail}", file=rel_o)
                continue
            ierrs = list(vi.iter_errors(ing))
            for e in ierrs[:20]:
                rep.fail("S2", e.message[:200], file=rel_o, path="/" + "/".join(map(str, e.absolute_path)))
            if ierrs:
                continue
            if op.name != Path(ing["source"]["path"]).name + ".json":
                rep.fail("S2", f"file name should be {Path(ing['source']['path']).name}.json", file=rel_o)
            if ing["source"]["sha256"] != f["sha256"]:
                rep.fail("S2", "source.sha256 differs from the batch manifest entry", file=rel_o, path="/source/sha256")


def rel(root, p):
    return Path(p).resolve().relative_to(root).as_posix()


# ------------------------------------------------------------------ S0 / S1
def check_summary(rep, summary_path, agent, run_id):
    rep.run("S0")
    try:
        s, fenced = parse_summary(Path(summary_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        rep.fail("S0", f"return summary is not valid JSON ({exc.__class__.__name__}); output may be partial. "
                       "Send one raw JSON object only: no code fence and no text before or after it")
        return {}
    if fenced:
        rep.notes.append("S0: summary was one fenced JSON block with nothing around it; accepted (D7)")
    if not isinstance(s, dict):
        rep.fail("S0", "return summary is not a JSON object")
        return {}
    for k in SUMMARY_KEYS:
        if k not in s:
            rep.fail("S0", f"return summary missing key '{k}'", path=k)
    if s.get("status") != "completed":
        rep.fail("S0", f"status is '{s.get('status')}', expected 'completed'", path="status")
    if s.get("partial") or s.get("is_partial"):
        rep.fail("S0", "output marked partial (maxTurns reached)", path="partial")
    if s.get("agent") not in (agent, f"ldip-design-review:{agent}"):
        rep.fail("S0", f"agent is '{s.get('agent')}', expected '{agent}'", path="agent")
    if s.get("run_id") != run_id:
        rep.fail("S0", f"run_id is '{s.get('run_id')}', expected '{run_id}'", path="run_id")
    return s


def check_files(rep, root, agent, summary, write_globs, required, host):
    rep.run("S1")
    listed = summary.get("files_written") if isinstance(summary.get("files_written"), list) else []
    for f in listed:
        p = (root / str(f).replace("\\", "/")).resolve()
        try:
            r = rel(root, p)
        except ValueError:
            rep.fail("S1", "listed file is outside LDIP_Review", path=str(f))
            continue
        if not p.is_file():
            rep.fail("S1", "listed file does not exist", file=r)
        elif not any_match(r, write_globs):
            rep.fail("S1", "listed file is outside the agent's write set", file=r)
    for r in required:
        if not (root / r).is_file():
            rep.fail("S1", "required output file missing", file=r)
    conflict = re.compile(rf"-{re.escape(host)}(\.|-\d+\.)", re.I) if host else None
    for dirpath, _, files in os.walk(root):
        rd = Path(dirpath).relative_to(root).as_posix()
        if rd.startswith("golden_set/answer_key"):
            continue
        for fn in files:
            r = fn if rd == "." else f"{rd}/{fn}"
            base = r[:-4] if r.endswith(".tmp") else r
            if not any_match(base, write_globs):
                continue
            if r.endswith(".tmp"):
                rep.fail("S1", "temporary file left behind (atomic write not completed, SPC-0002 §6.2)", file=r)
            elif conflict and conflict.search(fn):
                rep.fail("S1", "OneDrive conflict copy (SPC-0002 R2)", file=r)
    return listed


# ------------------------------------------------------------------ finding checks
def load_findings(rep, root, relpath):
    p = root / relpath
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        rep.fail("S2", f"cannot read JSON ({exc.__class__.__name__})", file=relpath)
        return None
    if not isinstance(data, list):
        rep.fail("S2", "findings file must be a JSON array", file=relpath)
        return None
    return data


def check_schema_identity(rep, validator, relpath, findings, spec, manifest):
    rep.run("S2")
    seen = set()
    for i, f in enumerate(findings):
        for e in validator.iter_errors(f):
            rep.fail("S2", e.message[:200], file=relpath, path=f"/{i}/" + "/".join(str(x) for x in e.absolute_path))
        if not isinstance(f, dict):
            continue
        fid = f.get("finding_id", "")
        if fid in seen:
            rep.fail("S2", f"duplicate finding_id {fid}", file=relpath, path=f"/{i}/finding_id")
        seen.add(fid)
        want = {"stage": spec["stage"], "run_id": manifest["run_id"], "site": manifest["site"],
                "sub_id": manifest["sub_id"]}
        if spec.get("code"):
            want["agent"] = spec["code"]
        for k, v in want.items():
            if f.get(k) != v:
                rep.fail("S2", f"{k} is '{f.get(k)}', expected '{v}'", file=relpath, path=f"/{i}/{k}")
        if not str(fid).startswith(manifest["run_id"] + "/"):
            rep.fail("S2", "finding_id does not start with the run-id", file=relpath, path=f"/{i}/finding_id")


def all_evidence(f):
    ev = list(f.get("evidence") or [])
    ev += list(((f.get("verification") or {}).get("verifier_evidence")) or [])
    return [e for e in ev if isinstance(e, dict)]


def check_evidence(rep, root, relpath, findings, manifest):
    rep.run("S3"), rep.run("FC1"), rep.run("FC3"), rep.run("FC4")
    inputs = {i["path"]: i["sha256"] for i in manifest.get("inputs", [])}
    for i, f in enumerate(findings):
        if not isinstance(f, dict):
            continue
        for j, e in enumerate(all_evidence(f)):
            sc = e.get("script")
            if isinstance(sc, dict) and sc.get("exit_code") != 0:
                rep.fail("S3", f"script {sc.get('name')} exit code {sc.get('exit_code')}", file=relpath,
                         path=f"/{i}/evidence/{j}/script/exit_code")
            ap = e.get("artifact_path")
            if ap:
                p = root / str(ap).replace("\\", "/")
                if not p.is_file():
                    rep.fail("FC3", f"artifact not found: {ap}", file=relpath, path=f"/{i}/evidence/{j}/artifact_path")
                elif not e.get("artifact_sha256"):
                    rep.fail("FC3", "artifact_sha256 missing", file=relpath, path=f"/{i}/evidence/{j}/artifact_sha256")
                elif sha256_file(p) != e["artifact_sha256"]:
                    rep.fail("FC3", f"artifact hash mismatch: {ap}", file=relpath, path=f"/{i}/evidence/{j}/artifact_sha256")
        tiers = [TIER_CONF.get(e.get("tier")) for e in (f.get("evidence") or []) if isinstance(e, dict)]
        tiers = [t for t in tiers if t]
        if tiers:
            strongest = max(tiers, key=lambda t: CONF_RANK[t])
            if f.get("confidence") != strongest:
                rep.fail("FC1", f"confidence '{f.get('confidence')}' but strongest evidence tier gives '{strongest}'",
                         file=relpath, path=f"/{i}/confidence")
        loc = f.get("location") or {}
        fp, fs = loc.get("file_path"), loc.get("file_sha256")
        if fp and fs:
            fp = str(fp).replace("\\", "/")
            if fp in inputs:
                if inputs[fp] != fs:
                    rep.fail("FC4", "file_sha256 differs from the run manifest input hash", file=relpath,
                             path=f"/{i}/location/file_sha256")
            elif (root / fp).is_file():
                if sha256_file(root / fp) != fs:
                    rep.fail("FC4", "file_sha256 differs from the file on disk", file=relpath,
                             path=f"/{i}/location/file_sha256")
            else:
                rep.fail("FC4", f"location file not found: {fp}", file=relpath, path=f"/{i}/location/file_path")


def check_rule_cap(rep, relpath, findings, rules, rulebook_version):
    rep.run("FC5")
    for e in cap_errors(findings, rules, rulebook_version):
        rep.fail("FC5", e["message"], file=relpath, path=e["path"])


def earlier_findings(rep, root, run, kind):
    if kind == "candidates":
        files = sorted((root / "findings" / run / "candidates").glob("*.json"))
    else:
        files = [root / "findings" / run / "verified.json"]
    out = {}
    for p in files:
        r = p.relative_to(root).as_posix()
        data = load_findings(rep, root, r) if p.is_file() else None
        if data is None:
            rep.fail("FC2", "earlier-stage file missing or unreadable", file=r)
            continue
        for f in data:
            if isinstance(f, dict):
                out.setdefault(f.get("finding_id"), []).append(f)
    return out


def check_unchanged(rep, relpath, findings, earlier, stage):
    rep.run("FC2")
    added = ADDED_BY[stage]
    for i, f in enumerate(findings):
        if not isinstance(f, dict):
            continue
        prev = (earlier.get(f.get("finding_id")) or [None])[0]
        if prev is None:
            rep.fail("FC2", "finding has no earlier-stage record (the Verifier and Outputs shall not add findings)",
                     file=relpath, path=f"/{i}/finding_id")
            continue
        for k, v in prev.items():
            if k in added and k != "links":
                continue
            if k == "links":
                pv = {kk: vv for kk, vv in (v or {}).items() if kk != "duplicate_of"}
                cv = {kk: vv for kk, vv in (f.get("links") or {}).items() if kk != "duplicate_of"}
                if pv != cv:
                    rep.fail("FC2", "earlier field changed: links", file=relpath, path=f"/{i}/links")
                continue
            if f.get(k) != v:
                rep.fail("FC2", f"earlier field changed: {k}", file=relpath, path=f"/{i}/{k}")


def check_completeness(rep, relpath, findings, earlier, cid):
    rep.run(cid)
    ids = [f.get("finding_id") for f in findings if isinstance(f, dict)]
    for fid in sorted(k for k in earlier if k):
        n = ids.count(fid)
        if n != 1:
            rep.fail(cid, f"{fid} appears {n} times (expected exactly once)", file=relpath)
    for fid in sorted(set(i for i in ids if i not in earlier)):
        rep.fail(cid, f"{fid} is not in the earlier stage", file=relpath)
    if cid == "FC8":
        for i, f in enumerate(findings):
            if isinstance(f, dict) and (f.get("gate") or {}).get("status") != "PENDING":
                rep.fail("FC8", "gate.status shall be PENDING for rows of this run", file=relpath, path=f"/{i}/gate/status")


# ------------------------------------------------------------------ main flow
def run_checks(agent, run, root, summary_path, host=None):
    if agent not in AGENTS:
        raise ScriptError(EXIT_INPUT, f"agent must be one of {sorted(AGENTS)}")
    if not RUN_ID_RE.match(run):
        raise ScriptError(EXIT_INPUT, f"invalid run-id {run}")
    root = Path(root).resolve()
    manifest = read_json(root / "runs" / run / "manifest.json")
    spec = AGENTS[agent]
    matrix = read_json(MATRIX_PATH)
    vals = {"{site}": manifest["site"], "{sub}": manifest["sub_id"], "{run}": run}
    write_globs = expand(matrix["agents"][agent].get("write", []), vals)
    required = [p.replace("{run}", run) for p in spec["files"]]

    rep = Report()
    summary = check_summary(rep, summary_path, agent, run)
    listed = check_files(rep, root, agent, summary, write_globs, required, host)

    if spec.get("batch_check"):
        rep.run("C3")
        cb = check_batches.check(root, manifest["site"], manifest["sub_id"])
        for v in cb["violations"]:
            rep.fail("C3", v["issue"], file=v.get("path"), path=v.get("batch"))
        check_ingestion(rep, root, manifest)
    if agent == "intake-package":
        rep.skip("S2-package", "package.json / rev_diff.json schemas not yet built [GAP]")

    if spec["stage"]:
        validator = load_validator()
        rb_path = root / manifest["rulebook_file"] if manifest.get("rulebook_file") else None
        rules = {}
        if rb_path and rb_path.is_file():
            rules = {r.get("rule_id"): r for r in read_json(rb_path).get("rules", []) if isinstance(r, dict)}
        else:
            rep.fail("FC5", "run rulebook file not found", file=manifest.get("rulebook_file"))
        for relpath in required:
            if not (root / relpath).is_file():
                continue
            findings = load_findings(rep, root, relpath)
            if findings is None:
                continue
            check_schema_identity(rep, validator, relpath, findings, spec, manifest)
            check_evidence(rep, root, relpath, findings, manifest)
            check_rule_cap(rep, relpath, findings, rules, manifest.get("rulebook_version"))
            if spec.get("earlier"):
                earlier = earlier_findings(rep, root, run, spec["earlier"])
                check_unchanged(rep, relpath, findings, earlier, spec["stage"])
                check_completeness(rep, relpath, findings, earlier, "FC7" if spec.get("fc7") else "FC8")

    hashes = {}
    for f in listed:
        p = root / str(f).replace("\\", "/")
        if p.is_file():
            hashes[str(f)] = sha256_file(p)
    result = "fail" if rep.errors else "pass"
    return rep, result, hashes


def main(argv=None):
    ap = argparse.ArgumentParser(description="LDIP format checker")
    ap.add_argument("agent")
    ap.add_argument("run")
    ap.add_argument("--root", required=True)
    ap.add_argument("--summary", required=True)
    ap.add_argument("--instance", default="1")
    ap.add_argument("--attempt", type=int, default=1)
    ap.add_argument("--host", default=os.environ.get("COMPUTERNAME"))
    a = ap.parse_args(argv)
    try:
        rep, result, hashes = run_checks(a.agent, a.run, a.root, a.summary, a.host)
        retry = [f"{e['check']} {e.get('file') or ''}{e.get('path') or ''}: {e['message']}".strip()
                 for e in rep.errors]
        entry = {"agent": a.agent, "instance": a.instance, "attempt": a.attempt, "result": result,
                 "checks": dict(sorted(rep.checks.items())), "errors": rep.errors, "notes": rep.notes,
                 "files_written_sha256": hashes, "checked_at": iso_utc(),
                 "format_check": f"format_check.py {__version__}"}
        log = Path(a.root).resolve() / "runs" / a.run / "format_checks.json"
        entries = read_json(log) if log.is_file() else []
        if not isinstance(entries, list):
            raise ScriptError(EXIT_INPUT, "format_checks.json is not a JSON array")
        entries.append(entry)
        atomic_write_json(log, entries)
        print(json.dumps({"status": "ok", "agent": a.agent, "attempt": a.attempt, "result": result,
                          "errors": len(rep.errors), "retry_errors": retry[:50]}, ensure_ascii=False))
        return EXIT_OK
    except ScriptError as err:
        print(json.dumps({"status": "error", "code": err.code, "detail": err.detail}))
        return err.code


if __name__ == "__main__":
    sys.exit(main())
