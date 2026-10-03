"""Agent Skills conformance, both directions: foreign layout loads, ours exports."""

import os
import re

import pytest

import freecad_ai.extensions.skills as skills_mod
from freecad_ai.extensions.skill_frontmatter import parse_frontmatter
from freecad_ai.extensions.skills import SkillsRegistry

_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_YAML_SPECIAL_START = tuple("[]{}&*!|>'\"%@`#,?:-")
BUILTIN = skills_mod.BUILTIN_SKILLS_DIR
BUILTIN_NAMES = sorted(n for n in os.listdir(BUILTIN)
                       if os.path.isfile(os.path.join(BUILTIN, n, "SKILL.md")))


def _relative_links(body):
    # A link inside a code span or fence is an example, not a link.
    body = re.sub(r"```.*?```", "", body, flags=re.S)
    body = re.sub(r"`[^`\n]*`", "", body)
    for target in _LINK_RE.findall(body):
        target = target.split("#", 1)[0]
        if target and "://" not in target and not target.startswith(("/", "mailto:")):
            yield target


@pytest.fixture
def foreign(tmp_path, monkeypatch):
    sd = tmp_path / "skills" / "pdf-forms"
    (sd / "references" / "fields").mkdir(parents=True)
    (sd / "scripts").mkdir()
    (sd / "assets").mkdir()
    (sd / "SKILL.md").write_text(
        "---\nname: pdf-forms\ndescription: >\n  Fill PDF forms. Use when the user\n"
        "  mentions a form.\nlicense: Apache-2.0\nallowed-tools: Bash Read\n---\n"
        "# PDF forms\nRead [the reference](reference.md) and "
        "[text fields](references/fields/text.md#rules), then run "
        "[the filler](scripts/fill.py) with [the template](assets/form.pdf).\n")
    (sd / "reference.md").write_text("# Reference\nGeneral notes.\n")
    (sd / "references" / "fields" / "text.md").write_text("# Text\nRules.\n")
    (sd / "scripts" / "fill.py").write_text("print('fill')\n")
    (sd / "assets" / "form.pdf").write_bytes(b"%PDF-1.7\0binary")
    monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setattr(skills_mod, "BUILTIN_SKILLS_DIR", str(tmp_path / "none"))
    return sd


def test_foreign_skill_links_all_resolve(foreign):
    reg = SkillsRegistry(extra_dirs=[])
    skill = reg.get_skill("pdf-forms")
    assert skill.description == "Fill PDF forms. Use when the user mentions a form."
    for link in _relative_links(skill.body):
        assert reg._resolve_key(skill, link) is not None, link


@pytest.mark.parametrize("name", BUILTIN_NAMES)
def test_builtin_skill_is_a_valid_agent_skill(name):
    with open(os.path.join(BUILTIN, name, "SKILL.md"), encoding="utf-8") as f:
        text = f.read()
    fm, body = parse_frontmatter(text)
    assert fm.get("name") == name and _NAME_RE.match(name) and len(name) <= 64
    assert isinstance(fm.get("description"), str) and 1 <= len(fm["description"]) <= 1024
    # Strict YAML parsers (other harnesses) reject an unquoted ": " or a
    # special leading character; ours is lenient, so check the raw lines.
    raw_fm = text.split("---", 2)[1]
    for line in raw_fm.splitlines():
        if line.startswith((" ", "\t")) or ":" not in line:
            continue
        value = line.split(":", 1)[1].strip()
        if value and value[0] not in "\"'>|":
            assert ": " not in value and not value.startswith(_YAML_SPECIAL_START), line
    reg = SkillsRegistry(extra_dirs=[])
    skill = reg.get_skill(name)
    for link in _relative_links(body):
        assert reg._resolve_key(skill, link) is not None, f"{name}: {link}"
