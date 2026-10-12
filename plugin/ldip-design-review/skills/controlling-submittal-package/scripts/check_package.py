#!/usr/bin/env python3
"""Run the LDIP Rulebook PKG rules on a submittal and/or on the MIDP's own numbers (SPC-0004 §6).

Usage: python check_package.py --rules <rules_INT.json> --site QRWN|NRJS|NMAR
           [--package <package.json> [--midp-match <midp_match.json>] [--issue-date YYYY-MM-DD]]
           [--midp <midp.json> --midp-self]  --out <pkg_results.json>
Carriers checked:
  file   document numbers in the received file names (package.json): PKG-001 to PKG-009, PKG-011
  midp   the numbers planned in the MIDP (--midp-self): PKG-001, -002, -003, -004, -005 (file name vs the
         workbook's "Document Reference" cell) and -009. PKG-006 is not run on the MIDP (one MIDP lists
         several sites).
Every value comes from the rules file (get_rules.py output): code lists, site map, legacy numbers,
effective date, drawing formats and the regular expressions quoted in each rule's method. A missing
value makes the rule or sub-check `not_run` with the reason; nothing is assumed.
Aggregation (SPC-0004 §5.7): one pattern per rule + field + reason, with the count, the distinct codes
and up to ten samples; MIDP patterns are kept per workbook sheet so each one can be re-read.
Legacy numbers (PKG-001/-002/-004/-006 legacy_numbers, or --issue-date before effective_from) go to a
separate `/legacy` pattern with severity Observation (SPC-0004 §6.2B).
PKG-007 needs a drawing / transmittal list (build_register.py --list); PKG-008 uses the MIDP formats of
exact / normalised matches, and the rulebook drawing formats for an unmatched drawing; PKG-011 needs
skill-1 ingestion files read with --resolve-base. PKG-010 belongs to skill 7.
Exit: 0 written · 2 input error. No network, no shell calls.
"""
__version__ = "0.3.2"

import argparse
import collections
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pkg_common import (register_base,  # noqa: E402
                        EXIT_INPUT, EXIT_OK, atomic_write_json, emit, fail, param, parse_number, path_ok,  # noqa: E402
                        read_json)

MAX_SAMPLES = 10
FIELD_LIST = [("program", 0, "program"), ("entity", 1, "entity"), ("organization", 2, "organization"),
              ("package", 3, "package"), ("section", 4, "section"), ("discipline", 5, "discipline"),
              ("type", 6, "doc_type")]


def method_regex(rule):
    m = re.search(r"Regex\s+(\^\S+?\$)", rule.get("method", "")) if rule else None
    return re.compile(m.group(1)) if m else None


class Items:
    """Number items of one carrier, with where each was read from."""

    def __init__(self, carrier):
        self.carrier = carrier
        self.items = []

    def add(self, number, where):
        self.items.append({"number": number, "parse": parse_number(number), **where})


class Patterns:
    def __init__(self):
        self.p = collections.OrderedDict()

    def hit(self, key, item, extract, message, codes=None, **extra):
        pat = self.p.setdefault(key, {"pattern": key[1], "carrier": key[0], "group": key[2], "count": 0,
                                      "codes": collections.Counter(), "samples": [], "message": message, **extra})
        pat["count"] += 1
        if codes:
            pat["codes"][codes] += 1
        if len(pat["samples"]) < MAX_SAMPLES:
            s = {"number": item.get("number"), "extract": extract}
            for k in ("path", "sha256", "cell", "sheet", "file"):
                if item.get(k) is not None:
                    s[k] = item[k]
            pat["samples"].append(s)

    def out(self, source_of):
        res = []
        for key, pat in self.p.items():
            pat = dict(pat)
            codes = pat.pop("codes")
            if codes:
                pat["codes"] = dict(codes.most_common())
                pat["message"] += " (" + ", ".join(f"{c} {n}" for c, n in codes.most_common(8)) + \
                                  (", …" if len(codes) > 8 else "") + ")"
            pat["source"] = source_of(pat)
            res.append(pat)
        return res


def legacy(item, rule, issue_date):
    nums = [n.upper() for n in (param(rule, "legacy_numbers") or [])]
    if item["number"].upper() in nums or item["parse"]["base"].upper() in nums:
        return True
    eff = param(rule, "effective_from")
    return bool(eff and issue_date and issue_date < eff)


SHEET_RE = re.compile(r"^\d{3}$")   # MIDP sheet field (SRC-14), owner decision P-S6-6, 11 Oct 2026
COVER_SEQUENCE = "00001"            # K5-3: a drawing with this sequence and no title block is a cover


def midp_scheme(parse, seq_rx):
    """P-S6-6: a conforming 10-field drawing / model number laid out as the MIDP numbers are
    (…-type-sequence-sheet-revision) instead of SRC-02 (…-type-level-sequence-revision): field 8 is a
    sequence and field 9 a three-digit sheet."""
    if not (parse["conforming_count"] and parse["expected_fields"] == 10 and seq_rx):
        return False
    f = parse["fields"]
    return bool(seq_rx.match(f[7]) and SHEET_RE.match(f[8]))


def sequence_of(parse, seq_rx):
    if midp_scheme(parse, seq_rx):
        return parse["fields"][7]
    return (parse.get("parts") or {}).get("sequence")


def group_of(item):
    return item["sheet"] if item.get("sheet") else "files"


def run_rules(items, rules, a, extra_ctx):
    R = {r["rule_id"]: r for r in rules["rules"]}
    out = {}
    car = items.carrier

    def result(rid, pats, not_run=None, subs=None, applicable=True):
        if not applicable:
            return
        r = {"rule_id": rid, "carrier": car}
        if rid not in R:
            r.update(result="not_run", reason="rule not returned by get_rules.py (inactive or absent)")
        elif not_run:
            r.update(result="not_run", reason=not_run)
        else:
            r.update(result="fail" if pats.p else "pass", patterns=pats.out(extra_ctx["source_of"]))
        if subs:
            r["subchecks_not_run"] = subs
        out.setdefault(rid, []).append(r)

    # rules_v1 (owner 12 Oct 2026: the latest MIDP governs over SRC-02): PKG-001 accepted_layouts
    accept_midp = "midp" in [str(x).lower() for x in (param(R.get("PKG-001"), "accepted_layouts") or [])]
    for it in items.items:
        it["midp_ok"] = accept_midp and it["parse"].get("layout") == "midp"

    # PKG-001 structure
    rule = R.get("PKG-001")
    pats, subs = Patterns(), []
    # P-S6-8 (owner, 12 Oct 2026, numbering only): numbering_scope = midp_deliverables -> the structure of a
    # number is checked only for deliverables planned in the MIDP; others need no MIDP number.
    not_in = set()
    if car == "file" and param(rule, "numbering_scope") == "midp_deliverables":
        mm = extra_ctx.get("midp_match")
        if mm:
            not_in = {m["base"] for m in mm.get("matches", []) if not (m.get("in_midp") or {}).get("value", True)}
            if not_in:
                subs.append({"subcheck": "not_in_midp", "reason": f"{len(not_in)} deliverable(s) not planned in the "
                             "MIDP: number structure not checked (numbering_scope = midp_deliverables)"})
        else:
            subs.append({"subcheck": "numbering_scope", "reason": "no --midp-match: every number checked"})
    for it in items.items:
        it["not_in_midp"] = register_base(it["parse"]) in not_in
    if rule:
        n_ok = sum(1 for it in items.items if it["midp_ok"])
        if n_ok:
            subs.append({"subcheck": "midp_layout_accepted", "reason": f"{n_ok} number(s) in the MIDP layout "
                         "(type-sequence-sheet[-revision]) accepted by accepted_layouts; information only"})
        n_dm, n_doc = param(rule, "fields_drawing_model"), param(rule, "fields_document")
        for it in items.items:
            p = it["parse"]
            exp = n_dm if p["type"] in ("DWG", "MOD") else n_doc
            if it["midp_ok"] or it["not_in_midp"] or exp is None or p["field_count"] == exp:
                continue
            lg = legacy(it, rule, a.issue_date)
            kind = "drawing_model" if p["type"] in ("DWG", "MOD") else "document"
            pats.hit((car, f"{kind}_fields_{p['field_count']}_expected_{exp}" + ("/legacy" if lg else ""), group_of(it)),
                     it, f"{it.get('cell') + '=' if it.get('cell') else ''}{it['number']}",
                     f"{'Legacy number' if lg else 'Number'} has {p['field_count']} fields; "
                     f"{'drawings and models' if kind == 'drawing_model' else 'documents'} need {exp}",
                     **({"severity": "Observation"} if lg else {}))
        seq_rx = method_regex(R.get("PKG-003"))
        for it in items.items:
            if not it["midp_ok"] and not it["not_in_midp"] and midp_scheme(it["parse"], seq_rx):
                it["midp_scheme"] = True
                pats.hit((car, "midp_scheme_naming", group_of(it)), it,
                         f"{it.get('cell') + '=' if it.get('cell') else ''}{it['number']}",
                         "Number follows the MIDP layout (type-sequence-sheet-revision), not SRC-02 "
                         "(type-level-sequence-revision); level and sequence are not checked separately")
        for f in extra_ctx.get("no_number", []):
            pats.hit((car, "no_document_number", "files"), {"path": f["path"], "sha256": f["sha256"], "number": None},
                     f["path"], "File name carries no document number")
    result("PKG-001", pats, subs=subs)

    # PKG-002 code lists
    rule = R.get("PKG-002")
    pats, subs = Patterns(), []
    if rule:
        cl = rule.get("code_lists") or {}
        missing_lists = [ln for _, _, ln in FIELD_LIST if ln not in cl] + ([] if "level" in cl else ["level"])
        if missing_lists:
            subs.append({"subcheck": "code_lists", "reason": f"code list(s) not in the rules file: {', '.join(missing_lists)}"})
        for it in items.items:
            f = it["fields"] if "fields" in it else it["parse"]["fields"]
            lg = legacy(it, rule, a.issue_date)
            checks = [(nm, i, ln) for nm, i, ln in FIELD_LIST]
            if it["parse"]["conforming_count"] and it["parse"]["expected_fields"] == 10 and not it.get("midp_scheme") \
                    and not it["midp_ok"]:
                checks.append(("level", 7, "level"))
            for nm, i, ln in checks:
                if ln not in cl or i >= len(f):
                    continue
                if f[i].upper() not in [str(c).upper() for c in cl[ln]]:
                    pats.hit((car, f"{nm}_code_not_listed" + ("/legacy" if lg else ""), group_of(it)), it,
                             f"{it.get('cell') + '=' if it.get('cell') else ''}{it['number']}",
                             f"{'Legacy number' if lg else 'Number'} uses {nm} codes not in the rulebook code list",
                             codes=f[i].upper(), field=nm, **({"severity": "Observation"} if lg else {}))
    result("PKG-002", pats, subs=subs)

    # PKG-003 sequence
    rule = R.get("PKG-003")
    pats, subs = Patterns(), []
    rx = method_regex(rule)
    if rule:
        subs.append({"subcheck": "category_vs_title",
                     "reason": "[GAP] sequence-category keywords are not in the rules file (seq_category holds digits only)"})
        if param(rule, "category_applies_to_documents") is None:
            subs.append({"subcheck": "category_for_documents", "reason": "[GAP] category_applies_to_documents"})
        skipped = sum(1 for it in items.items if not it["parse"]["conforming_count"] and not it["midp_ok"])
        if skipped:
            subs.append({"subcheck": "sequence_format", "reason": f"{skipped} number(s) not checked: field count does "
                                                                   f"not conform (PKG-001), sequence position unknown"})
        n_scheme = sum(1 for it in items.items if it.get("midp_scheme"))
        if n_scheme:
            subs.append({"subcheck": "sequence_format_midp_scheme",
                         "reason": f"{n_scheme} number(s) in the MIDP layout (PKG-001 midp_scheme_naming): their "
                                   "sequence is field 8 and matched the pattern when the layout was recognised"})
        for it in items.items:
            if rx is None or it.get("midp_scheme") or not (it["parse"]["conforming_count"] or it["midp_ok"]):
                continue
            seq = it["parse"]["midp"]["sequence"] if it["midp_ok"] else it["parse"]["parts"]["sequence"]
            if not rx.match(seq):
                pats.hit((car, "sequence_format", group_of(it)), it,
                         f"{it.get('cell') + '=' if it.get('cell') else ''}{it['number']}",
                         "Sequence field is not 5 digits", codes=seq)
    result("PKG-003", pats, not_run=None if rx or not rule else "no regular expression in the rule method", subs=subs)

    # PKG-004 revision format
    rule = R.get("PKG-004")
    pats, subs = Patterns(), []
    rx = method_regex(rule)
    if rule:
        subs.append({"subcheck": "stage_consistency", "reason": "stage_consistency is PROPOSED in the rules file; "
                                                                "only the series is checked"})
        seq_rx = method_regex(R.get("PKG-003"))
        for it in items.items:
            p = it["parse"]
            if rx is None:
                continue
            if it["midp_ok"]:
                rev = p["midp"]["revision"]
                if rev is None:
                    if car == "file":   # a received file carries a revision; MIDP numbers carry none
                        pats.hit((car, "revision_missing", group_of(it)), it, it["number"],
                                 "Number in the MIDP layout without a revision field")
                    continue
                if not rx.match(rev):
                    pats.hit((car, "revision_format", group_of(it)), it,
                             f"{it.get('cell') + '=' if it.get('cell') else ''}{it['number']}",
                             "Last field (revision) is not a revision code of the rulebook", codes=rev)
                continue
            if not p["conforming_count"]:
                # one field short and ending in a sequence number: the revision field is absent
                if seq_rx and p["field_count"] == p["expected_fields"] - 1 and seq_rx.match(p["fields"][-1]):
                    lg = legacy(it, rule, a.issue_date)
                    pats.hit((car, "revision_missing" + ("/legacy" if lg else ""), group_of(it)), it,
                             f"{it.get('cell') + '=' if it.get('cell') else ''}{it['number']}",
                             "Number ends with the sequence field: no revision code",
                             **({"severity": "Observation"} if lg else {}))
                continue
            rev = p["revision"]
            if not rx.match(rev):
                lg = legacy(it, rule, a.issue_date)
                pats.hit((car, "revision_format" + ("/legacy" if lg else ""), group_of(it)), it,
                         f"{it.get('cell') + '=' if it.get('cell') else ''}{it['number']}",
                         "Last field (revision) is not a revision code of the rulebook", codes=rev,
                         **({"severity": "Observation"} if lg else {}))
    result("PKG-004", pats, not_run=None if rx or not rule else "no regular expression in the rule method", subs=subs)

    # PKG-005 carriers
    rule = R.get("PKG-005")
    if rule and car == "midp":
        pats = Patterns()
        for f in extra_ctx.get("midp_files", []):
            ref = (f["meta"].get("Document Reference") or {})
            if ref.get("value") and f.get("number_from_name") and ref["value"].upper() != f["number_from_name"].upper():
                it = {"number": f["number_from_name"], "cell": ref["cell"], "sheet": ref["sheet"], "file": f["index"]}
                pats.hit((car, "file_name_vs_document_reference", ref["sheet"]), it,
                         f"{ref['cell']}={ref['value']}",
                         f"File name {f['number_from_name']} differs from the workbook's Document Reference "
                         f"{ref['value']}")
        result("PKG-005", pats)
    elif rule and car == "file":
        pats, subs = Patterns(), []
        seq_rx = method_regex(R.get("PKG-003"))
        unread, covers = collections.Counter(), []
        for reg in extra_ctx["register"]:
            c = reg["carriers"]
            nums = {"file_name": {x["number"] for x in c.get("file_name", [])}}
            read_any = False
            for k in ("dwg_title_block", "pdf_title_block"):
                read = [x for x in c.get(k, []) if x.get("read") and x.get("document_no")]
                if read:
                    nums[k] = {x["document_no"] for x in read}
                    read_any = True
            looked = [x for k in ("dwg_title_block", "pdf_title_block") for x in c.get(k, [])]
            if reg["parse"]["type"] == "DWG" and not read_any and looked \
                    and all(x.get("reason") == "TITLE_BLOCK_NOT_FOUND" for x in looked) \
                    and sequence_of(reg["parse"], seq_rx) == COVER_SEQUENCE:
                covers.append(reg["base"])          # K5-3: cover sheet, no title block expected
            elif reg["parse"]["type"] == "DWG":
                for k in ("dwg_title_block", "pdf_title_block"):
                    fmt = "DWG" if k.startswith("dwg") else "PDF"
                    if any(x.get("read") and x.get("document_no") for x in c.get(k, [])):
                        continue
                    if c.get(k):
                        for x in c[k]:
                            unread[(k, x.get("reason") or "TITLE_BLOCK_NOT_FOUND")] += 1
                    elif fmt in reg.get("formats_received", []):
                        unread[(k, "no ingestion file")] += 1
            if c.get("drawing_list"):
                nums["drawing_list"] = {x["number"] for x in c["drawing_list"]}
            bases = {k: {parse_number(n)["base"] for n in v} for k, v in nums.items()}
            keys = sorted(bases)
            for i in range(len(keys)):
                for j in range(i + 1, len(keys)):
                    if bases[keys[i]] != bases[keys[j]]:
                        f0 = reg["files"][0]
                        it = {"number": reg["base"], "path": f0["path"], "sha256": f0["sha256"]}
                        pats.hit((car, f"{keys[i]}_vs_{keys[j]}", "files"), it,
                                 f"{keys[i]}: {', '.join(sorted(nums[keys[i]]))} | {keys[j]}: {', '.join(sorted(nums[keys[j]]))}",
                                 f"Document number differs between the {keys[i].replace('_', ' ')} and the "
                                 f"{keys[j].replace('_', ' ')}")
            # K5-4 (owner approved 11 Oct 2026): the file-name revision is compared too
            revs = {k: {x.get("revision") for x in c.get(k, []) if x.get("revision") and (k == "file_name" or
                                                                                        k == "drawing_list" or x.get("read"))}
                    for k in ("file_name", "dwg_title_block", "pdf_title_block", "drawing_list")}
            revs = {k: v for k, v in revs.items() if v}
            ks = sorted(revs)
            for i in range(len(ks)):
                for j in range(i + 1, len(ks)):
                    if revs[ks[i]] != revs[ks[j]]:
                        f0 = reg["files"][0]
                        pats.hit((car, f"revision_{ks[i]}_vs_{ks[j]}", "files"),
                                 {"number": reg["base"], "path": f0["path"], "sha256": f0["sha256"]},
                                 f"{ks[i]}: {', '.join(sorted(revs[ks[i]]))} | {ks[j]}: {', '.join(sorted(revs[ks[j]]))}",
                                 f"Revision differs between the {ks[i].replace('_', ' ')} and the {ks[j].replace('_', ' ')}")
        for (k, why), n in sorted(unread.items()):
            subs.append({"subcheck": f"{k}_not_read", "reason": f"{n} drawing(s): {why}"
                         + (" (ezdwg cannot decode the Parsons title-block attributes; needs the oda or "
                            "accoreconsole engine, K2)" if why == "TITLE_BLOCK_ATTRIBS_UNDECODED" else "")})
        if covers:
            subs.append({"subcheck": "cover_sheets", "reason": f"{len(covers)} cover sheet(s) without a title block "
                         f"(sequence {COVER_SEQUENCE}, K5-3): " + ", ".join(sorted(covers)[:10])})
        result("PKG-005", pats, subs=subs)

    # PKG-006 section vs site (file carrier only)
    rule = R.get("PKG-006")
    if car == "file":
        pats = Patterns()
        smap = param(rule, "site_map") if rule else None
        if rule and smap:
            allowed = [c.upper() for c in smap.get(a.site, [])]
            for it in items.items:
                f = it["parse"]["fields"]
                if len(f) > 4 and f[4].upper() not in allowed:
                    lg = legacy(it, rule, a.issue_date)
                    pats.hit((car, "section_not_for_site" + ("/legacy" if lg else ""), "files"), it, it["number"],
                             f"Section code is not one allowed for {a.site} ({', '.join(allowed)})", codes=f[4].upper(),
                             **({"severity": "Observation"} if lg else {}))
        result("PKG-006", pats, not_run=None if (not rule or smap) else "site_map not in the rules file")

    # PKG-007 completeness against the list (file carrier only)
    if car == "file":
        pkg = extra_ctx["package"]
        pats = Patterns()
        if (pkg.get("transmittal") or {}).get("received") is False:
            # owner, 12 Oct 2026: no transmittal / drawing list received with the submission = missing
            pats.hit((car, "transmittal_not_received", "package"), {"number": None, "path": "(package)"},
                     "no transmittal or drawing list in the submission",
                     "No transmittal or drawing list was received with the submission",
                     issue_type="missing_information")
        elif pkg.get("transmittal"):
            t = pkg["transmittal"]
            for m in pkg["missing"]:
                pats.hit((car, "listed_not_received", "files"),
                         {"number": m["number"], "cell": m["cell"], "sheet": m["sheet"]}, f"{m['cell']}={m['number']}",
                         "Listed on the drawing / transmittal list but not received",
                         issue_type="missing_information", list_source=t)
            for u in pkg["unlisted"]:
                pats.hit((car, "received_not_listed", "files"), {"number": u["base"], "path": u["paths"][0]},
                         u["paths"][0], "Received but not on the drawing / transmittal list",
                         issue_type="package_control", severity="Minor", status="Confirmed")
            for f in pkg["files_without_number"]:
                pats.hit((car, "received_not_listed", "files"), {"number": None, "path": f["path"], "sha256": f["sha256"]},
                         f["path"], "Received but not on the drawing / transmittal list",
                         issue_type="package_control", severity="Minor", status="Confirmed")
        result("PKG-007", pats, not_run=None if pkg.get("transmittal") else
               "no drawing or transmittal list supplied (K4: Aconex transmittal format not yet defined)")

    # PKG-013 stage completeness (file carrier only; owner activated 12 Oct 2026)
    rule = R.get("PKG-013")
    if car == "file" and rule:
        mm = extra_ctx.get("midp_match") or {}
        st = mm.get("stage")
        pats = Patterns()
        if st:
            for p in st.get("planned_not_received", []):
                ref = {"sheet": p["sheet"], "midp_path": p["midp_path"], "midp_sha256": p["midp_sha256"],
                       "midp_number": p["midp_number"], "midp_updated": p["midp_updated"]}
                pats.hit((car, "planned_not_received", p["sheet"]),
                         {"number": p["number"], "cell": p["cell"], "sheet": p["sheet"]},
                         f"{p['cell']}={p['number']}",
                         f"Planned in the MIDP for stage '{st['stage']}' but not received",
                         issue_type="missing_information", midp=ref,
                         midp_clause=f"Data exchange '{st['stage']}', sheet '{p['sheet']}' (PKG-013)")
        result("PKG-013", pats, not_run=None if st else
               "MIDP stage of the submission not given (match_midp.py --stage)")

    # PKG-008 formats (file carrier only)
    rule = R.get("PKG-008")
    if car == "file":
        pkg, mm = extra_ctx["package"], extra_ctx.get("midp_match")
        pats, subs = Patterns(), [{"subcheck": "native_files", "reason": "[GAP] which deliverables need a native "
                                   "authoring file is not defined per row; DWG origin is recorded in package.json"}]
        dfm = [x.upper() for x in (param(rule, "drawing_formats") or [])]
        by_base = {m["base"]: m for m in (mm or {}).get("matches", [])}
        not_checked = collections.Counter()
        for reg in pkg["sheet_register"]:
            m = by_base.get(reg["base"])
            got = set(reg["formats_received"])
            f0 = reg["files"][0]
            if m and m["match"] in ("exact", "normalised"):
                req = set(m["midp"]["formats"])
                for fmt in sorted(req - got):
                    cell = m["midp"]["format_cells"][fmt]
                    pats.hit((car, f"missing_{fmt}/midp", m["midp"]["sheet"]),
                             {"number": reg["base"], "cell": cell, "sheet": m["midp"]["sheet"], "path": f0["path"]},
                             f"{cell}=X", f"Exchange format {fmt} flagged in the MIDP was not received",
                             issue_type="missing_information", midp=m["midp"], codes=fmt)
            elif reg["parse"]["type"] == "DWG" and dfm:
                for fmt in sorted(set(dfm) - got):
                    pats.hit((car, f"missing_{fmt}/rulebook", "files"),
                             {"number": reg["base"], "path": f0["path"], "sha256": f0["sha256"]},
                             f"{reg['base']}: received {'+'.join(sorted(got)) or '-'}",
                             f"Drawing received without its {fmt} file (rulebook drawing formats)",
                             issue_type="missing_information", codes=fmt)
                if mm:
                    not_checked[m["match"] if m else "unmatched"] += 1
            else:
                not_checked[(m or {}).get("match", "no MIDP supplied")] += 1
        if not mm:
            subs.append({"subcheck": "midp_formats", "reason": "MIDP not supplied: only the rulebook drawing formats "
                                                               "were checked"})
        if not_checked:
            subs.append({"subcheck": "midp_formats_unmatched",
                         "reason": "documents not checked against the MIDP: " +
                                   ", ".join(f"{k} {v}" for k, v in sorted(not_checked.items()))})
        result("PKG-008", pats, subs=subs)

    # PKG-009 duplicates
    rule = R.get("PKG-009")
    pats = Patterns()
    if rule:
        if car == "file":
            by = collections.defaultdict(list)
            for reg in extra_ctx["register"]:
                for f in reg["files"]:
                    by[(f["number"], Path(f["path"]).suffix.lower())].append(f)
            for (num, ext), fs in sorted(by.items()):
                if len({f["sha256"] for f in fs}) > 1:
                    pats.hit((car, "same_number_different_content", "files"),
                             {"number": num, "path": fs[0]["path"], "sha256": fs[0]["sha256"]},
                             "; ".join(f"{f['path']} sha256 {f['sha256'][:12]}" for f in fs[:4]),
                             "Same number, revision and format received more than once with different content")
        else:
            seen = collections.defaultdict(list)
            for it in items.items:
                seen[it["number"]].append(it)
            for num, its in seen.items():
                if len(its) > 1:
                    for it in its:
                        pats.hit((car, "duplicate_number", group_of(it)), it, f"{it['cell']}={num}",
                                 "Number planned more than once in the MIDP")
    result("PKG-009", pats)

    if car == "file":
        out.setdefault("PKG-010", []).append({"rule_id": "PKG-010", "carrier": car, "result": "not_run",
                                              "reason": "belongs to skill 7 comparing-drawing-revisions (not built)"})
        rule = R.get("PKG-011")
        pats = Patterns()
        origins = extra_ctx["package"].get("dwg_origin", [])
        checked = [o for o in origins if o.get("resolve_checked")]
        for o in checked:
            for kind, names in o["unresolved"].items():
                for n in names:
                    pats.hit((car, f"unresolved_{kind}", "files"), {"number": o["base"], "path": o["path"]},
                             f"{o['path']}: {kind[:-1]} {n}", f"DWG references {kind} that are not in the package",
                             source_type="dwg_vector")
        result("PKG-011", pats, not_run=None if checked or not rule else
               "no skill-1 ingestion file read with --resolve-base (external references not checked)")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", required=True)
    ap.add_argument("--site", required=True, choices=["QRWN", "NRJS", "NMAR"])
    ap.add_argument("--package")
    ap.add_argument("--midp-match")
    ap.add_argument("--midp")
    ap.add_argument("--midp-self", action="store_true")
    ap.add_argument("--issue-date")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if not path_ok(a.out):
        return fail(EXIT_INPUT, "output path longer than 250 characters")
    if not a.package and not (a.midp and a.midp_self):
        return fail(EXIT_INPUT, "give --package, or --midp with --midp-self, or both")
    if a.issue_date and not re.match(r"^\d{4}-\d{2}-\d{2}$", a.issue_date):
        return fail(EXIT_INPUT, "--issue-date must be YYYY-MM-DD")
    try:
        rules = read_json(a.rules)
        pkg = read_json(a.package) if a.package else None
        mm = read_json(a.midp_match) if a.midp_match else None
        midp = read_json(a.midp) if a.midp else None
    except (OSError, ValueError) as exc:
        return fail(EXIT_INPUT, f"cannot read input: {exc}")
    results = []
    if pkg:
        items = Items("file")
        for reg in pkg["sheet_register"]:
            seen = set()
            for f in reg["files"]:
                if f["number"] not in seen:
                    seen.add(f["number"])
                    items.add(f["number"], {"path": f["path"], "sha256": f["sha256"]})

        def src_file(pat):
            return {"carrier": "file", "source_type": pat.get("source_type", "script_output"),
                    "root": pkg.get("root"), "mode": pkg.get("mode")}
        ctx = {"register": pkg["sheet_register"], "package": pkg, "midp_match": mm, "source_of": src_file,
               "no_number": pkg.get("files_without_number", [])}
        for rid, rr in sorted(run_rules(items, rules, a, ctx).items()):
            results += rr
    if midp and a.midp_self:
        items = Items("midp")
        for r in midp["rows"]:
            items.add(r["number"], {"cell": r["cell"], "sheet": r["sheet"], "file": r["file"]})
        mfiles = midp["files"]

        def src_midp(pat):
            fi = pat["samples"][0].get("file", 0) if pat["samples"] else 0
            f = mfiles[fi]
            return {"carrier": "midp", "source_type": "xlsx_cell", "path": f["path"], "sha256": f["sha256"],
                    "sheet": pat["group"], "document_no": f["number_from_name"]}
        # keep MIDP patterns per workbook file as well as per sheet
        ctx = {"source_of": src_midp, "midp_files": mfiles}
        for rid, rr in sorted(run_rules(items, rules, a, ctx).items()):
            results += rr
    skipped = rules.get("rules_skipped", [])
    doc = {"tool": {"name": "check_package.py", "version": __version__}, "site": a.site,
           "rulebook_version": rules.get("rulebook_version"), "issue_date": a.issue_date, "results": results,
           "rules_skipped": skipped}
    atomic_write_json(a.out, doc)
    summ = collections.Counter()
    for r in results:
        summ[f"{r['carrier']}:{r['result']}"] += 1
    emit({"status": "ok", "out": str(a.out), "results": dict(sorted(summ.items())),
          "patterns": sum(len(r.get("patterns", [])) for r in results),
          "fail": sorted({f"{r['carrier']}:{r['rule_id']}" for r in results if r["result"] == "fail"})})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
