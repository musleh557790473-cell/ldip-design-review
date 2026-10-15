"""Shared helpers for skill 10 reviewing-road-design (SPC-0006 Rev D §4.13). No network, no shell calls.

Every rule value, tolerance, applicability and cap comes from the rules file written by skill 8
(get_rules.py --agent RDE). This module holds only reading conventions: class synonyms (D10-2, on the
D-13 mapping of SPC-0004 Rev E §5.8), criterion labels and number parsing.
"""
__version__ = "0.2.0"

import hashlib
import json
import os
import re
import sys
from pathlib import Path

EXIT_OK, EXIT_INPUT, EXIT_DEPENDENCY, EXIT_SCHEMA = 0, 2, 3, 4
MAX_PATH = 250
STATUS_RANK = {"Confirmed": 3, "Missing information": 3, "Probable": 2, "Not auto-verifiable": 1}
EVIDENCE_TIER = {"dwg_vector": "deterministic", "landxml": "deterministic", "script_output": "deterministic",
                 "pdf_vector_text": "extracted_text_crop", "pdf_ocr": "extracted_text_crop"}
CONFIDENCE = {"deterministic": "High", "extracted_text_crop": "Medium"}

# D10-2 [AGREED 15 Oct 2026 10:34]: Parsons class terms -> rulebook keys (D-13). Matched on whole words,
# longest first; a term not listed gives no class (never guessed).
CLASS_SYNONYMS = [
    ("neighbourhood collector", "sub_collector"), ("neighborhood collector", "sub_collector"),
    ("urban collector", "sub_collector"), ("sub collector", "sub_collector"), ("sub-collector", "sub_collector"),
    ("collector", "sub_collector"),
    ("local / access", "local_access"), ("local/access", "local_access"), ("local access", "local_access"),
    ("access road", "local_access"), ("local road", "local_access"), ("local", "local_access"),
    ("service road", "service_road"), ("frontage road", "service_road"),
    ("arterial", "arterial"),
]

# Criterion labels in a Parsons design-criteria table -> criterion key (§4.13.6). First match wins.
CRITERIA_LABELS = [
    (r"^design\s+speed", "design_speed"),
    (r"^posted\s+speed", "posted_speed"),
    (r"^functional\s+classification", "functional_class"),
    (r"right[\s-]*of[\s-]*way|\brow\b", "row"),
    (r"^lane\s+width", "lane_width"),
    (r"^number\s+of\s+lanes", "lanes_each_direction"),
    (r"^median", "median"),
    (r"^(karb|kerb|curb)", "curb"),
    (r"^carriageway\s+cross\s*slope|^carriageway\s+crossfall", "crossfall"),
    (r"^sidewalk\s+cross\s*slope", "sidewalk_cross_slope"),
    (r"superelevation|e-?max", "e_max"),
    (r"^minimum\s+horizontal\s+radius", "r_min"),
    (r"^maximum\s+longitudinal\s+grade", "g_max"),
    (r"^minimum\s+longitudinal\s+grade", "g_min"),
    (r"k\s+crest", "k_crest"),
    (r"k\s+sag", "k_sag"),
    (r"^minimum\s+vertical\s+curve\s+length", "l_min"),
    (r"^vertical\s+clearance", "clearance"),
    (r"^design\s+vehicle", "design_vehicle"),
    (r"^check\s+vehicle|^control\s+vehicle", "check_vehicle"),
]

NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")
ROAD_LABEL = re.compile(r"^(RD|RA)-(?:M-)?\d{1,3}$")

# Phase B1 (SPC-0006 Rev E §4.13B.4, DB-2): element -> rule and search terms (layer names, block names, DWG
# text, PDF text; case-insensitive regex on whole words). Skill parameters, listed for readers in
# references/presence_terms.md. A term preceded by a negation in the same text is a statement of absence.
PRESENCE_ELEMENTS = {
    "sidewalk": {"rule": "ROD-101", "terms": [r"side\s?walks?", r"foot\s?paths?", r"walkways?", r"pedestrian\s+paths?"]},
    "curb_ramp": {"rule": "ROD-102", "terms": [r"(?:curb|kerb)\s+ramps?", r"ramps?", r"dropped\s+kerbs?"]},
    "tactile": {"rule": "ROD-103", "terms": [r"tactile", r"warning\s+tiles?", r"blister"]},
    "ped_crossing": {"rule": "ROD-104", "terms": [r"pedestrian\s+crossings?", r"zebra", r"cross\s?walks?",
                                                 r"raised\s+crossings?", r"ped(?:estrian)?\s+x(?:ing)?"]},
    "sight_triangle": {"rule": "ROD-105", "terms": [r"sight\s+triangles?", r"visibility\s+splays?", r"sight\s+lines?"]},
    "swept_path": {"rule": "ROD-106", "terms": [r"swept\s+paths?", r"vehicle\s+tracking", r"auto\s?turn"]},
    "catch_basin": {"rule": "ROD-107", "terms": [r"catch\s+basins?", r"gull(?:y|ies)", r"inlets?"]},
    "accessible_parking": {"rule": "ROD-108", "terms": [r"accessible\s+(?:parking|bays?|spaces?)", r"disabled\s+(?:parking|bays?)",
                                                       r"handicap(?:ped)?", r"ada\s+(?:parking|spaces?|bays?)"]},
    "street_name_sign": {"rule": "ROD-109", "terms": [r"street\s+name", r"road\s+name\s+signs?", r"name\s+plates?"]},
    "road_marking": {"rule": "ROD-110", "terms": [r"road\s+markings?", r"markings?", r"lane\s+lines?", r"stop\s+lines?",
                                                 r"white\s+lines?", r"yellow\s+lines?", r"thermoplastic"]},
    "calming_sign": {"rule": "ROD-111", "terms": [r"w2-1", r"\bslow\b", r"hump\s+ahead"]},
    "splitter_island": {"rule": "ROD-112", "terms": [r"splitter\s+islands?", r"splitter"]},
    "traffic_calming": {"rule": "ROD-113", "terms": [r"speed\s+humps?", r"speed\s+tables?", r"raised\s+(?:table|intersection)s?",
                                                    r"chicanes?", r"traffic\s+calming"]},
    "bus_stop": {"rule": "ROD-114", "terms": [r"bus\s+stops?", r"bus\s+bays?", r"bus\s+shelters?", r"bus\s+stations?"]},
    "turning_area": {"rule": "ROD-115", "terms": [r"cul-?de-?sacs?", r"turning\s+(?:heads?|areas?|circles?)", r"hammer\s?heads?"]},
    "parking_lane": {"rule": "ROD-116", "terms": [r"parallel\s+parking", r"on-?street\s+(?:car\s+)?(?:visitor\s+)?parking",
                                                 r"parking\s+(?:lanes?|bays?)", r"lay-?bys?"]},
}
# Conditions (§4.13B.5 item 3): evidence that the rule applies at all.
PRESENCE_CONDITIONS = {
    "ROD-112": [r"roundabouts?"],
    "ROD-114": [r"bus\s+stops?", r"bus\s+stations?", r"public\s+transport(?:ation)?", r"transit"],
}
# DB-5: a statement that an element is provided on only part of the network ("walkways on both sides for
# at least 85 %"): element term and a coverage below 100 % in the same line.
PARTIAL_COVERAGE = {
    "ROD-101": {"element": r"(?:walkways?|side\s?walks?|foot\s?paths?)",
                "coverage": r"both\s+sides\b[^%]{0,60}?\b(\d{1,2}(?:\.\d+)?)\s*%"},
}
NEGATION = re.compile(r"\b(?:without|no|not)\b[^.;:]{0,25}$", re.I)
UNDECODABLE = re.compile("[\uac00-\ud7af\u4e00-\u9fff\ue000-\uf8ff\ufffd]")


def undecodable(text):
    """True for a text record that skill 1 could not decode (K18): Hangul / CJK / private-use / U+FFFD."""
    return bool(UNDECODABLE.search(str(text or "")))


def term_hits(text, terms):
    """[(term, start, end, negated)] for each term match in text."""
    out = []
    for t in terms:
        for m in re.finditer(r"(?<![a-z0-9])" + t + r"(?![a-z0-9])", str(text or ""), re.I):
            out.append((t, m.start(), m.end(), bool(NEGATION.search(text[:m.start()]))))
    return out


class RoadError(Exception):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code, self.detail = code, detail


def class_of(text):
    """Rulebook class key for a Parsons class term, or None (D10-2)."""
    t = " " + re.sub(r"\s+", " ", str(text or "").lower()).strip() + " "
    for term, key in CLASS_SYNONYMS:
        if re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", t):
            return key
    return None


def criterion_of(label):
    t = re.sub(r"\s+", " ", str(label or "").lower()).strip()
    for pat, key in CRITERIA_LABELS:
        if re.search(pat, t):
            return key
    return None


def numbers(text):
    """All numbers in a cell, in order ('15/18' -> [15.0, 18.0]; 'Raised / 1.2' -> [1.2]; 'None' -> [])."""
    return [float(x) for x in NUM.findall(str(text or "").replace(",", ""))]


def check_path(path, must_exist=False):
    p = Path(path)
    if len(str(p.resolve())) > MAX_PATH:
        raise RoadError(EXIT_INPUT, f"path longer than {MAX_PATH} characters: {path}")
    if must_exist and not p.is_file():
        raise RoadError(EXIT_INPUT, f"file not found: {path}")
    return p


def read_json(path):
    try:
        return json.loads(check_path(path, must_exist=True).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RoadError(EXIT_INPUT, f"not valid JSON: {path}: {exc}") from exc


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_json(path, obj):
    p = check_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False, sort_keys=True))


def source_of(ing):
    """Source block of an ingestion file (skills 1, 2, 3) for evidence."""
    s = ing.get("source") or {}
    return {"path": s.get("path"), "sha256": s.get("sha256"), "document_no": s.get("document_no"),
            "file_type": s.get("file_type")}


def rules_index(rules_file):
    """{rule_id: rule} for the rules an agent may run, plus the skipped list and stage map."""
    rb = rules_file
    return {r["rule_id"]: r for r in rb.get("rules", [])}, rb.get("rules_skipped", []), rb.get("stage_map") or {}


def param(rule, name):
    p = (rule.get("parameters") or {}).get(name)
    if p is None or p.get("tag") == "GAP":
        return None
    return p


def tol(p):
    t = (p or {}).get("tolerance") or {}
    return float(t["value"]) if t.get("value") is not None else 0.0


def applies(rule, cls):
    sc = ((rule.get("applicability") or {}).get("street_class")) or ["all"]
    return "all" in sc or cls in sc


def lower(a, b):
    return a if STATUS_RANK.get(a, 0) <= STATUS_RANK.get(b, 0) else b


def fail_exit(code, detail):
    emit({"status": "error", "code": code, "detail": detail})
    return code


def main_guard(fn):
    try:
        return fn()
    except RoadError as e:
        return fail_exit(e.code, e.detail)


if __name__ == "__main__":
    sys.exit(0)
