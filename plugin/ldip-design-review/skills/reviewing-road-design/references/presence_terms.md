# Presence terms and logic — phase B1 (SPC-0006 Rev E §4.13B)

Skill parameters (DB-2, owner 15 Oct 2026 13:07). The scripts read them from `scripts/road_common.py`
(`PRESENCE_ELEMENTS`, `PRESENCE_CONDITIONS`, `PARTIAL_COVERAGE`); this page lists them for readers and a
test keeps the two equal. Terms are case-insensitive regular expressions matched on whole words in DWG
layer names (with entities), block names and texts, and in PDF vector-text lines.

## 1. Element terms

| Element | Rule | Terms |
|---|---|---|
| `sidewalk` | ROD-101 | `side\s?walks?`, `foot\s?paths?`, `walkways?`, `pedestrian\s+paths?` |
| `curb_ramp` | ROD-102 | `(?:curb\|kerb)\s+ramps?`, `ramps?`, `dropped\s+kerbs?` |
| `tactile` | ROD-103 | `tactile`, `warning\s+tiles?`, `blister` |
| `ped_crossing` | ROD-104 | `pedestrian\s+crossings?`, `zebra`, `cross\s?walks?`, `raised\s+crossings?`, `ped(?:estrian)?\s+x(?:ing)?` |
| `sight_triangle` | ROD-105 | `sight\s+triangles?`, `visibility\s+splays?`, `sight\s+lines?` |
| `swept_path` | ROD-106 | `swept\s+paths?`, `vehicle\s+tracking`, `auto\s?turn` |
| `catch_basin` | ROD-107 | `catch\s+basins?`, `gull(?:y\|ies)`, `inlets?` |
| `accessible_parking` | ROD-108 | `accessible\s+(?:parking\|bays?\|spaces?)`, `disabled\s+(?:parking\|bays?)`, `handicap(?:ped)?`, `ada\s+(?:parking\|spaces?\|bays?)` |
| `street_name_sign` | ROD-109 | `street\s+name`, `road\s+name\s+signs?`, `name\s+plates?` |
| `road_marking` | ROD-110 | `road\s+markings?`, `markings?`, `lane\s+lines?`, `stop\s+lines?`, `white\s+lines?`, `yellow\s+lines?`, `thermoplastic` |
| `calming_sign` | ROD-111 | `w2-1`, `\bslow\b`, `hump\s+ahead` |
| `splitter_island` | ROD-112 | `splitter\s+islands?`, `splitter` |
| `traffic_calming` | ROD-113 | `speed\s+humps?`, `speed\s+tables?`, `raised\s+(?:table\|intersection)s?`, `chicanes?`, `traffic\s+calming` |
| `bus_stop` | ROD-114 | `bus\s+stops?`, `bus\s+bays?`, `bus\s+shelters?`, `bus\s+stations?` |
| `turning_area` | ROD-115 | `cul-?de-?sacs?`, `turning\s+(?:heads?\|areas?\|circles?)`, `hammer\s?heads?` |
| `parking_lane` | ROD-116 | `parallel\s+parking`, `on-?street\s+(?:car\s+)?(?:visitor\s+)?parking`, `parking\s+(?:lanes?\|bays?)`, `lay-?bys?` |

A term preceded in the same text by "without", "no" or "not" (up to 25 characters before it, same
clause) is a **statement of absence** (`stated_absent`), e.g. "18 METER LOCAL ROAD (WITHOUT PARALLEL
PARKING)".

## 2. Condition terms (the rule applies at all)

| Rule | Terms |
|---|---|
| ROD-112 | `roundabouts?` |
| ROD-114 | `bus\s+stops?`, `bus\s+stations?`, `public\s+transport(?:ation)?`, `transit` |

ROD-113 (calming near schools / playgrounds): DLUS land uses given with `--land-uses`; a school,
playground, kindergarten or nursery use makes it apply (package level). ROD-115 (turning area at dead
ends): needs road-end positions — not decided in phase B1 unless a turning area is shown (K17 / K19).

## 3. Partial coverage (DB-5)

ROD-101: a PDF line with a sidewalk term and "both sides … <n> %" with n < 100 (e.g. "continuous
walkways provided on both sides of the roads for at least 85 %") → finding, *Probable*.

## 4. Decision per rule (check_presence.py)

1. Rule phase later than the submission phase → `not_run` "not required at stage".
2. Accepted carriers: the element carriers of the rule's required inputs for the latest phase that
   applies (items without a C-number; otherwise the rule `inputs` without `dlus`). A design-report
   statement counts only where the rule lists `design_report` for that phase (DB-3: PD only).
3. Condition not shown → `not_run` (internal *Not auto-verifiable*).
4. Shown in an accepted carrier → pass (`intent_only` when only the report states it).
5. Only negated → one finding with every negating text (M4).
6. Absent from every accepted carrier → one finding; evidence = what was searched and the carriers not
   in the submittal (M5); capped at *Probable* when an undecodable text record (K18) lies on a layer that
   matches the element terms (DB-4). From DD a report statement is quoted in the finding (DB-3).
7. Negated in an accepted carrier and shown elsewhere → one `inconsistency` finding, *Probable*.
8. ROD-101 per typical section: fewer than two sidewalks → finding.

Status = the lowest of the rule `default_status` for the evidence source, the rule / parameter cap from
skill 8 (advisory → *Probable*) and the caps above.

## 5. Limits of phase B1

- No count per junction, crossing, road end, low point or parking area (phase B2, after K17 / K19).
- The carrier type of each file is given by the caller; a file given under a wrong carrier changes the
  result.
- Terms find what is named. An element drawn without any name, layer or block that says what it is
  cannot be found; the finding then says what was searched so the reviewer can check.
