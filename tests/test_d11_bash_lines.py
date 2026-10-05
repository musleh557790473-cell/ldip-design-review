"""D11 (owner approved 1 Oct 2026): every command the agents and the command file show is a single
`python "<script>" …` call with no shell operators, so the guard (SPC-0005 §7.3) never denies a
prescribed line; and every agent tells the model so."""
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin/ldip-design-review"
DOCS = sorted([*PLUGIN.glob("agents/*.md"), *PLUGIN.glob("commands/*.md"), *PLUGIN.glob("skills/*/SKILL.md")])
CMD = re.compile(r"`(python [^`]+)`")
BAD = re.compile(r"&&|\|\||(?<![<\w])\|(?!\w)|;|\$\?|\$[A-Z_]+(?!\{)|\bmkdir\b|\becho\b|>|\bdate\b")


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(PLUGIN)))
def test_shown_commands_are_single_python_calls(doc):
    for m in CMD.finditer(doc.read_text(encoding="utf-8")):
        line = m.group(1).replace("${CLAUDE_PLUGIN_ROOT}", "PLUGINROOT")
        line = re.sub(r"<[^>]+>", "X", line)                 # placeholders such as <SK>, <RB>
        assert not BAD.search(line), f"{doc.name}: {m.group(1)}"


@pytest.mark.parametrize("agent", sorted(PLUGIN.glob("agents/*.md")), ids=lambda p: p.stem)
def test_agents_state_the_one_call_rule(agent):
    t = agent.read_text(encoding="utf-8")
    assert "one `python" in t and "`&&`" in t and "`$?`" in t, agent.name


def test_verifier_recheck_with_rulebook_passes_guard(tmp_path):
    sys.path.insert(0, str(REPO / "tests"))
    from test_plugin_scripts import RUN, guard  # noqa: E402
    root = tmp_path / "LDIP & Review"
    for d in ("runs", "rulebook", "findings"):
        (root / d).mkdir(parents=True, exist_ok=True)
    F = f"findings/{RUN}"
    c = (f'python "{PLUGIN}/skills/verifying-review-findings/scripts/recheck.py" --candidates {F}/candidates/gis.json '
         f'--rulebook rulebook/rules_v0.json --out {F}/evidence/verifier/recheck.json')
    assert guard(root, "Bash", {"command": c}, agent_type="ldip-design-review:verifier") == "allow"
