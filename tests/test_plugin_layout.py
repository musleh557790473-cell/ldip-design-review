"""Plugin layout checks for agents and the /review-submittal command (SPC-0005 §4, §9)."""
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
PLUGIN = Path(__file__).resolve().parents[1] / "plugin/ldip-design-review"
AGENTS = sorted((PLUGIN / "agents").glob("*.md"))
SPEC = {  # SPC-0005 §9.1-9.2 (skills not yet built are listed as YAML comments, P-S3-25)
    "intake-batch": ("Read, Glob, Bash, Write", "sonnet", 40),
    "gis-cadastral": ("Read, Glob, Grep, Bash, Write", "sonnet", 40),
    "verifier": ("Read, Glob, Grep, Bash, Write", "opus", 60),
}


def front(p):
    t = p.read_text(encoding="utf-8")
    assert t.startswith("---\n")
    return yaml.safe_load(t.split("---\n")[1]), t


def test_agents_present():
    assert {p.stem for p in AGENTS} == set(SPEC)


@pytest.mark.parametrize("path", AGENTS, ids=lambda p: p.stem)
def test_agent_frontmatter(path):
    fm, text = front(path)
    tools, model, turns = SPEC[path.stem]
    assert fm["name"] == path.stem
    assert fm["description"].startswith("LDIP design review only — invoked by /review-submittal.")   # §4.13
    assert fm["tools"] == tools and "Agent" not in fm["tools"]                                        # §4.1
    assert fm["disallowedTools"] == "Agent, Skill, WebFetch, WebSearch, mcp__*"                       # §4.2
    assert fm["model"] == model and fm["maxTurns"] == turns
    for s in fm["skills"]:
        assert (PLUGIN / "skills" / s / "SKILL.md").is_file(), s
    assert '"agent": "' + path.stem + '"' in text                                                     # §4.8
    assert len(text.splitlines()) < 200                                                               # §9.3


def test_command():
    fm, text = front(PLUGIN / "commands/review-submittal.md")
    assert set(t.strip() for t in fm["allowed-tools"].split(",")) == {"Agent", "Read", "Glob", "Bash", "Write"}
    for script in ("run_manifest.py", "snapshot.py", "format_check.py"):
        assert script in text
    assert "reasoning/**" in text and "retry_errors" in text
