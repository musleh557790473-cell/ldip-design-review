"""Parsons title-block reader for PDF pages (K5, owner approval 11 Oct 2026: K5-1).

The Parsons LDIP sheet template (Namar 30% package, A1 and A1-on-A0) prints label → value pairs in the
bottom-right corner: "Drawing No.:", "Revision:", "Sheet:", "Date:", "Scale at A1:", "Drawing Title:",
"Purpose of Issue:", "Project No.:", "Designed:" / "Drawn:" / "Checked:" / "Approved:".
The reader does not use fixed coordinates. It looks for the label words inside the search zone (right
ZONE_X share, bottom ZONE_Y share of the displayed page), then reads each value from the column under its
label: from the label's left edge to the next label on the same row, and down to the next label below.
Works on any sheet size and on rotated pages (spans are already in displayed coordinates).
A page whose zone has no "Drawing No.:" label has no title block (cover sheets: K5-3).
"""

ZONE_X = 0.75     # search zone: x0 >= ZONE_X * page width
ZONE_Y = 0.60     # search zone: top >= ZONE_Y * page height
ROW_TOL = 3.0     # pt: label words on the same row
WORD_GAP = 12.0   # pt: max gap between the words of one label

# field -> label words (in reading order). "client" is read only to bound the purpose column.
LABELS = {
    "document_no": ["Drawing", "No.:"],
    "revision": ["Revision:"],
    "sheet": ["Sheet:"],
    "date": ["Date:"],
    "scale": ["Scale", "at", "A1:"],
    "title": ["Drawing", "Title:"],
    "status": ["Purpose", "of", "Issue:"],
    "project_no": ["Project", "No.:"],
    "designed": ["Designed:"],
    "drawn": ["Drawn:"],
    "checked": ["Checked:"],
    "approved": ["Approved:"],
    "client": ["Client:"],
}
OUTPUT_FIELDS = ["document_no", "revision", "title", "date", "status", "sheet", "scale", "project_no",
                 "designed", "drawn", "checked", "approved"]


def _find_labels(spans):
    order = sorted(range(len(spans)), key=lambda i: (round(spans[i]["bbox"][1]), spans[i]["bbox"][0]))
    used, found = set(), {}
    for field, words in LABELS.items():
        for i in order:
            s = spans[i]
            if i in used or s["text"] != words[0]:
                continue
            seq = [i]
            for w in words[1:]:
                last = spans[seq[-1]]["bbox"]
                nxt = [j for j in order if j not in used and j not in seq and spans[j]["text"] == w
                       and abs(spans[j]["bbox"][1] - s["bbox"][1]) < ROW_TOL
                       and 0 <= spans[j]["bbox"][0] - last[2] < WORD_GAP]
                if not nxt:
                    seq = None
                    break
                seq.append(nxt[0])
            if seq:
                bb = [spans[j]["bbox"] for j in seq]
                found[field] = {"x0": bb[0][0], "top": min(b[1] for b in bb), "bottom": max(b[3] for b in bb),
                                "x1": max(b[2] for b in bb), "ids": set(seq)}
                used.update(seq)
                break
    return found, used


def read_page(page):
    """Title block of one page record (extract_pdf page dict) or None."""
    w, h = page.get("width_pt"), page.get("height_pt")
    if not w or not h:
        return None
    zone = [s for s in page.get("spans", []) if s["bbox"][0] >= ZONE_X * w and s["bbox"][1] >= ZONE_Y * h]
    labels, used = _find_labels(zone)
    if "document_no" not in labels:
        return None
    fields = {}
    for field, lab in labels.items():
        if field == "client":
            continue
        right = min([m["x0"] for m in labels.values() if abs(m["top"] - lab["top"]) < ROW_TOL
                     and m["x0"] > lab["x0"] + 1] + [w])
        below = min([m["top"] for m in labels.values() if m["top"] > lab["bottom"] and m["x0"] < right - 2
                     and m["x1"] > lab["x0"]] + [h])
        vals = [zone[i] for i in range(len(zone)) if i not in used
                and lab["top"] + 1 < zone[i]["bbox"][1] < below - 1
                and lab["x0"] - 3 <= zone[i]["bbox"][0] < right - 2]
        vals.sort(key=lambda s: (round(s["bbox"][1] / 4), s["bbox"][0]))
        text = " ".join(s["text"] for s in vals).strip()
        box = [min(s["bbox"][0] for s in vals), min(s["bbox"][1] for s in vals),
               max(s["bbox"][2] for s in vals), max(s["bbox"][3] for s in vals)] if vals else None
        fields[field] = {"value": text or None, "bbox": box,
                         "label_bbox": [lab["x0"], lab["top"], lab["x1"], lab["bottom"]]}
    tb = {k: (fields.get(k) or {}).get("value") for k in OUTPUT_FIELDS}
    if tb["document_no"]:
        tb["document_no"] = tb["document_no"].replace(" ", "").upper()
    tb["fields"] = fields
    tb["source_ref"] = f"page {page['page']}, label-anchored (Parsons LDIP template, title_blocks.md)"
    return tb


def read_document(pages):
    """(title_block of the first page that has one, per-page list, warnings)."""
    per_page, warnings = [], []
    for p in pages:
        tb = read_page(p) if not p.get("error") else None
        p["title_block"] = tb
        if tb:
            per_page.append(tb)
    if not per_page:
        warnings.append({"code": "TITLE_BLOCK_NOT_FOUND",
                         "detail": "no 'Drawing No.:' label in the title-block zone of any page (cover sheet, "
                                   "scanned page or another template)"})
        return None, warnings
    first = per_page[0]
    for k in ("document_no", "revision"):
        if not first.get(k):
            warnings.append({"code": "TITLE_BLOCK_FIELD_EMPTY", "detail": f"{k} label found but no value under it"})
    nums = sorted({t["document_no"] for t in per_page if t.get("document_no")})
    if len(nums) > 1:
        warnings.append({"code": "TITLE_BLOCK_MULTIPLE",
                         "detail": f"{len(nums)} different drawing numbers on the pages: " + ", ".join(nums[:10])})
    return first, warnings
