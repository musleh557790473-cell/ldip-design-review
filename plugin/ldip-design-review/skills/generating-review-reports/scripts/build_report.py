#!/usr/bin/env python3
"""Write the readable DRAFT review report from a findings view (skill 24, SPC-0006 §4.10.1; P-S22-6).

Usage: python build_report.py --view <findings_view.json> --out-md <report.md> --out-xlsx <report.xlsx>
           [--results <result.json> ...] [--run-log <run.jsonl>] [--title "<text>"]
Contents (Markdown and Excel carry the same numbers):
  header       title, generated time, plugin / skill / rulebook versions, input files with SHA-256
  counts       status x severity (candidates in their own row "Unverified (candidate)"); by stage; by route
  by rule      count per rule with its statuses
  findings     one row per finding; CRS-route preview; engineer queue; manual-check list; inconsistencies
  not run      rules and sub-checks not run, with reasons, read from skill result files (--results:
               any entry {rule_id, result: not_run, reason} and any rules_skipped list) and from the run
               log (--run-log: rule_skipped and fail events)
  rules run    rule ids whose result is pass or fail in --results
Every page and sheet is marked DRAFT with the plugin version. The report is a view: it adds no data and
never changes a status, severity or route. Check every number with check_report.py.
Exit: 0 written · 2 input error · 3 openpyxl missing. No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from report_common import (DRAFT, EXIT_DEP, EXIT_INPUT, EXIT_OK, SEVERITIES, STATUSES, UNVERIFIED,  # noqa: E402
                           atomic_save, atomic_write_text, emit, fail, now_text, path_ok, plugin_version, read_json,
                           stamp)

SKILL = "generating-review-reports"
MATRIX_HEAD = "Status \\ Severity"
ROUTE_ORDER = ["CRS", "RFI", "RISK_EOT_REGISTER", "ENGINEER_QUEUE", "MANUAL_CHECK", "NONE", "INCONSISTENT",
               "UNVERIFIED"]


def counts(rows):
    """The numbers check_report.py recomputes: status x severity, stages, routes, rules."""
    m = collections.Counter((r["status"] or UNVERIFIED, r["severity"]) for r in rows)
    matrix = {s: {v: m[(s, v)] for v in SEVERITIES} for s in [*STATUSES, UNVERIFIED]}
    return {"total": len(rows), "matrix": matrix,
            "stages": dict(collections.Counter(r["stage"] for r in rows)),
            "routes": dict(collections.Counter(r["route"] for r in rows)),
            "rules": dict(collections.Counter(r["rule_id"] or "(no rule)" for r in rows))}


def walk(obj, found):
    if isinstance(obj, dict):
        if isinstance(obj.get("rule_id"), str) and obj.get("result") in ("pass", "fail", "not_run"):
            found.append(obj)
        for k, v in obj.items():
            if k == "rules_skipped" and isinstance(v, list):
                for e in v:
                    if isinstance(e, dict) and isinstance(e.get("rule_id"), str):
                        found.append({"rule_id": e["rule_id"], "result": "not_run",
                                      "reason": e.get("reason") or e.get("state_reason") or "skipped"})
            else:
                walk(v, found)
    elif isinstance(obj, list):
        for v in obj:
            walk(v, found)


def run_and_skipped(results, run_log):
    found, failed = [], []
    for p in results:
        walk(read_json(p), found)
    if run_log:
        for line in Path(run_log).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            ev = json.loads(line)
            if ev.get("event") == "rule_skipped":
                d = ev.get("detail") or {}
                found.append({"rule_id": d.get("rule_id", "?"), "result": "not_run", "reason": d.get("reason", ""),
                              "source": f"run log ({ev.get('agent')})"})
            elif ev.get("event") == "fail":
                failed.append({"agent": ev.get("agent"), "instance": ev.get("instance"), "ts": ev.get("ts"),
                               "detail": json.dumps(ev.get("detail") or {}, ensure_ascii=False)[:300]})
    ran = sorted({e["rule_id"] for e in found if e["result"] in ("pass", "fail")})
    skipped, seen = [], set()
    for e in found:
        if e["result"] != "not_run":
            continue
        sub = e.get("subcheck") or e.get("carrier") or ""
        key = (e["rule_id"], sub, str(e.get("reason", "")))
        if key not in seen:
            seen.add(key)
            skipped.append({"rule_id": e["rule_id"], "subcheck": sub, "reason": str(e.get("reason", ""))[:300],
                            "source": e.get("source", "results")})
    skipped.sort(key=lambda s: (s["rule_id"], s["subcheck"], s["reason"]))
    return ran, skipped, failed


def md_table(headers, rows):
    esc = lambda v: str("" if v is None else v).replace("|", "\\|").replace("\n", " ")  # noqa: E731
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(esc(v) for v in r) + " |" for r in rows]
    return out


def build_md(view, c, ran, skipped, failed, title, stamp_line):
    rows = view["findings"]
    L = [f"# {title}", "", f"**{DRAFT}**", "", f"_{stamp_line}_", "",
         f"Generated {now_text()} · plugin {plugin_version()} · mode {view.get('mode', 'standalone')}", ""]
    rbs = sorted({r["rulebook_version"] for r in rows if r["rulebook_version"]})
    L += ["## Inputs", ""]
    L += md_table(["File", "SHA-256", "Findings", "Stages"],
                  [[i["path"], i["sha256"], i["findings"], ", ".join(f"{k} {v}" for k, v in i["stages"].items())]
                   for i in view["inputs"]])
    L += ["", f"Rulebook version(s) cited: {', '.join(rbs) if rbs else 'none'}", ""]
    L += ["## Counts by status and severity", ""]
    if c["total"] == 0:
        L += ["**No findings.**", ""]
    L += md_table([MATRIX_HEAD, *SEVERITIES, "Total"],
                  [[s, *[c["matrix"][s][v] for v in SEVERITIES], sum(c["matrix"][s].values())]
                   for s in [*STATUSES, UNVERIFIED]]
                  + [["Total", *[sum(c["matrix"][s][v] for s in c["matrix"]) for v in SEVERITIES], c["total"]]])
    L += ["", "## Counts by route", "",
          "Routes of verified findings are provisional (SPC-0003 §6, standalone). UNVERIFIED = candidate, "
          "run skill 18 first.", ""]
    L += md_table(["Route", "Findings"], [[k, c["routes"].get(k, 0)] for k in ROUTE_ORDER])
    L += ["", "## Findings by rule", ""]
    by_rule = collections.defaultdict(list)
    for r in rows:
        by_rule[r["rule_id"] or "(no rule)"].append(r)
    L += md_table(["Rule", "Findings", "Statuses", "Title (first finding)"],
                  [[k, len(v), ", ".join(f"{s} {n}" for s, n in sorted(collections.Counter(
                      x["status"] or UNVERIFIED for x in v).items())), v[0]["title"]]
                   for k, v in sorted(by_rule.items())])
    L += ["", "## Findings", ""]
    L += md_table(["Finding", "Rule", "Severity", "Status", "Route", "Document / location", "Statement"],
                  [[r["finding_id"].split("/")[-1], r["rule_id"], r["severity"], r["status"] or UNVERIFIED,
                    r["route"], r["location"], r["statement"]] for r in rows]) if rows else ["None."]
    for head, route, extra in (("Engineer queue (Probable)", "ENGINEER_QUEUE", "recheck_result"),
                               ("Manual-check list (Not auto-verifiable)", "MANUAL_CHECK", "manual_check"),
                               ("Inconsistencies (left out of drafts, SPC-0005 §5.9.1)", "INCONSISTENT",
                                "route_basis"),
                               ("Unverified candidates (run skill 18 first)", "UNVERIFIED", "proposed_status")):
        sel = [r for r in rows if r["route"] == route]
        L += ["", f"## {head}", ""]
        L += md_table(["Finding", "Rule", "Location", extra.replace("_", " ").capitalize()],
                      [[r["finding_id"].split("/")[-1], r["rule_id"], r["location"],
                        json.dumps(r[extra], ensure_ascii=False) if isinstance(r[extra], dict) else r[extra]]
                       for r in sel]) if sel else ["None."]
    L += ["", "## Rules run", "", ", ".join(ran) if ran else "Not given (no --results).", ""]
    L += ["## Rules and sub-checks not run", ""]
    L += md_table(["Rule", "Sub-check", "Reason", "Source"],
                  [[s["rule_id"], s["subcheck"], s["reason"], s["source"]] for s in skipped]) if skipped else ["None reported."]
    L += ["", "## Failed steps", ""]
    L += md_table(["Agent", "Instance", "Time", "Detail"],
                  [[f["agent"], f["instance"], f["ts"], f["detail"]] for f in failed]) if failed else ["None reported."]
    L += ["", "---", "", f"_{stamp_line}_", ""]
    return "\n".join(L)


def build_xlsx(view, c, ran, skipped, failed, title, stamp_line, out):
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    head_fill, white = PatternFill("solid", fgColor="1F3864"), Font(bold=True, color="FFFFFF")
    wb = openpyxl.Workbook()

    def mark(ws):
        ws["A1"] = stamp_line
        ws["A1"].font = Font(bold=True, color="9C0006")
        ws.oddHeader.center.text = DRAFT
        ws.oddFooter.center.text = f"ldip-design-review {plugin_version()}"

    def table(ws, headers, rows, widths, start=3):
        ws.cell(row=start, column=1)
        for j, h in enumerate(headers, 1):
            cell = ws.cell(row=start, column=j, value=h)
            cell.font, cell.fill = white, head_fill
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        for i, r in enumerate(rows, start + 1):
            for j, v in enumerate(r, 1):
                v = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                ws.cell(row=i, column=j, value=v).alignment = Alignment(wrap_text=True, vertical="top")
        for j, w in enumerate(widths, 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(j)].width = w
        ws.freeze_panes = ws.cell(row=start + 1, column=1)
        return start + len(rows) + 1

    ws = wb.active
    ws.title = "Summary"
    mark(ws)
    ws["A2"] = title
    ws["A2"].font = Font(bold=True, size=14)
    r = 4
    rbs = sorted({x["rulebook_version"] for x in view["findings"] if x["rulebook_version"]})
    for k, v in [("Generated", now_text()), ("Plugin", plugin_version()),
                 ("Skill", f"{SKILL} (build_report {__version__})"), ("Mode", view.get("mode", "standalone")),
                 ("Rulebook version(s) cited", ", ".join(rbs) or "none"), ("Findings", c["total"])]:
        ws.cell(row=r, column=1, value=k).font = Font(bold=True)
        ws.cell(row=r, column=2, value=v)
        r += 1
    if c["total"] == 0:
        ws.cell(row=r, column=1, value="No findings.").font = Font(bold=True)
        r += 1
    r += 1
    end = table(ws, [MATRIX_HEAD, *SEVERITIES, "Total"],
                [[s, *[c["matrix"][s][v] for v in SEVERITIES], sum(c["matrix"][s].values())]
                 for s in [*STATUSES, UNVERIFIED]]
                + [["Total", *[sum(c["matrix"][s][v] for s in c["matrix"]) for v in SEVERITIES], c["total"]]],
                [34, 60, 12, 12, 14, 10], start=r)
    end = table(ws, ["Route", "Findings"], [[k, c["routes"].get(k, 0)] for k in ROUTE_ORDER],
                [34, 60], start=end + 1)
    table(ws, ["Input file", "SHA-256 / findings / stages"],
          [[i["path"], f"{i['sha256']} · {i['findings']} · " + ", ".join(f"{k} {v}" for k, v in i["stages"].items())]
           for i in view["inputs"]], [34, 60], start=end + 1)
    ws.freeze_panes = None

    rows = view["findings"]
    sheets = [
        ("Findings", ["Finding", "Run", "Rule", "Title", "Severity", "Status", "Stage", "Route", "Route basis",
                      "Document / location", "Statement", "Reference", "Evidence (first)"],
         [[x["finding_id"], x["run_id"], x["rule_id"], x["title"], x["severity"], x["status"] or UNVERIFIED,
           x["stage"], x["route"], x["route_basis"], x["location"], x["statement"], x["reference"],
           x["evidence"][0]["extract"] if x["evidence"] else ""] for x in rows],
         [30, 24, 10, 30, 11, 18, 11, 16, 30, 40, 70, 30, 40]),
        ("By rule", ["Rule", "Findings"], sorted([[k, v] for k, v in c["rules"].items()]), [16, 12]),
        ("CRS preview", ["Finding", "Rule", "Severity", "Status", "Document / location", "Statement"],
         [[x["finding_id"].split("/")[-1], x["rule_id"], x["severity"], x["status"], x["location"], x["statement"]]
          for x in rows if x["route"] == "CRS"], [14, 10, 11, 18, 50, 80]),
        ("Engineer queue", ["Finding", "Rule", "Severity", "Location", "Statement", "Re-check"],
         [[x["finding_id"].split("/")[-1], x["rule_id"], x["severity"], x["location"], x["statement"],
           x["recheck_result"]] for x in rows if x["route"] == "ENGINEER_QUEUE"], [14, 10, 11, 50, 80, 14]),
        ("Manual check", ["Finding", "Rule", "Location", "Statement", "What / where to check"],
         [[x["finding_id"].split("/")[-1], x["rule_id"], x["location"], x["statement"], x["manual_check"]]
          for x in rows if x["route"] == "MANUAL_CHECK"], [14, 10, 50, 70, 50]),
        ("Inconsistencies", ["Finding", "Rule", "Status", "Issue type", "Why"],
         [[x["finding_id"].split("/")[-1], x["rule_id"], x["status"], x["issue_type"], x["route_basis"]]
          for x in rows if x["route"] == "INCONSISTENT"], [14, 10, 18, 18, 70]),
        ("Unverified", ["Finding", "Rule", "Severity", "Proposed status", "Location", "Statement"],
         [[x["finding_id"].split("/")[-1], x["rule_id"], x["severity"], x["proposed_status"], x["location"],
           x["statement"]] for x in rows if x["route"] == "UNVERIFIED"], [14, 10, 11, 18, 50, 80]),
        ("Not run", ["Rule", "Sub-check", "Reason", "Source"],
         [[s["rule_id"], s["subcheck"], s["reason"], s["source"]] for s in skipped], [12, 24, 90, 18]),
        ("Rules run", ["Rule"], [[x] for x in ran], [14]),
        ("Failed steps", ["Agent", "Instance", "Time", "Detail"],
         [[f["agent"], f["instance"], f["ts"], f["detail"]] for f in failed], [16, 10, 22, 90]),
    ]
    for name, headers, data, widths in sheets:
        s = wb.create_sheet(name)
        mark(s)
        s["A2"] = f"{len(data)} row(s)" if data else "None."
        table(s, headers, data, widths)
    atomic_save(wb, out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--view", required=True)
    ap.add_argument("--out-md", required=True)
    ap.add_argument("--out-xlsx", required=True)
    ap.add_argument("--results", nargs="*", default=[])
    ap.add_argument("--run-log")
    ap.add_argument("--title", default="LDIP design review — run report")
    a = ap.parse_args(argv)
    if not a.out_md.lower().endswith(".md") or not a.out_xlsx.lower().endswith(".xlsx"):
        return fail(EXIT_INPUT, "--out-md must end in .md and --out-xlsx in .xlsx")
    for p in [a.view, a.out_md, a.out_xlsx, *a.results, *([a.run_log] if a.run_log else [])]:
        if not path_ok(p):
            return fail(EXIT_INPUT, f"path longer than 250 characters: {p}")
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        return fail(EXIT_DEP, "openpyxl is not installed")
    try:
        view = read_json(a.view)
        if view.get("view_version") != "1.0" or not isinstance(view.get("findings"), list):
            return fail(EXIT_INPUT, f"{a.view} is not a findings view from load_findings.py")
        ran, skipped, failed = run_and_skipped(a.results, a.run_log)
    except (OSError, ValueError) as exc:
        return fail(EXIT_INPUT, f"cannot read input: {exc}")
    c = counts(view["findings"])
    line = stamp(SKILL, "build_report.py", __version__)
    atomic_write_text(a.out_md, build_md(view, c, ran, skipped, failed, a.title, line))
    build_xlsx(view, c, ran, skipped, failed, a.title, line, a.out_xlsx)
    emit({"status": "ok", "findings": c["total"], "routes": dict(sorted(c["routes"].items())),
          "rules_run": len(ran), "not_run": len(skipped), "failed_steps": len(failed),
          "out_md": a.out_md, "out_xlsx": a.out_xlsx})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
