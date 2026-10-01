# Agent Skills Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Skill folders in the open Agent Skills format load and work in FreeCAD AI unchanged, and our built-in skills are valid Agent Skills, without changing how any existing skill behaves.

**Architecture:** A stdlib frontmatter parser feeds `SkillsRegistry`. The registry's load-time allowlist grows from "top-level `references/` stems" to "every file in the skill folder, keyed by POSIX relative path", with the old stems kept as aliases. A new `run_skill_script` tool runs allowlisted `.py` files through `execute_code` via a `runpy` wrapper. An opt-in `extra_skill_dirs` setting adds scan roots.

**Tech Stack:** Python 3.11 stdlib only, PySide6/PySide2 via `freecad_ai/ui/compat.py`, pytest.

**Spec:** `docs/superpowers/specs/2026-10-01-agent-skills-compat-design.md`

## Global Constraints

- No external dependencies (no PyYAML): stdlib only.
- Defaults reproduce today's behaviour: `extra_skill_dirs` defaults to `[]`.
- The folder name is a skill's identity and `/command`; frontmatter `name` never changes it.
- Description stored up to **1024** chars; system-prompt list shows up to **300**, then `…`; first-content-line fallback keeps today's **100** cap.
- Resource reads cap at **100 KB** (`100_000` bytes); manifest shows at most **40** entries; scan keeps at most **500** files per skill; unknown-key errors list at most **20** keys.
- Allowlist skips: `SKILL.md`, `handler.py`, `VALIDATION.md` (root only), names starting with `.`, dirs `__pycache__`, `node_modules`, `.venv`, `.git`.
- `Skill.content` stays the full file (`skills/optimize-skill/handler.py:54` rewrites the file from it); `Skill.body` is what gets injected.
- Qt imports only through `freecad_ai/ui/compat.py`; flat enum forms.
- Run tests as `env PYTHONPATH= .venv/bin/pytest …` (shell PYTHONPATH breaks pluggy). Full unit suite: `env PYTHONPATH= .venv/bin/pytest tests/unit -q --ignore=tests/unit/test_document_attach.py` (that file Qt-segfaults on clean master too).
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

**Deviations from spec, for review:**
1. The manifest keeps the heading `## Available references` (which `USE_SKILL`'s description names) and today's `(resource='<alias>')` hint next to aliased files; the three groups sit under it as `###` subheadings. Existing prompts then read the same as today, and `test_execute_skill_appends_references_manifest` passes unchanged.
2. Validation covers **every `.py` file in the skill**, not only the script, because a script can `import helper` from its own folder and the validator never sees that file otherwise.
3. The wrapper passes globals not starting with `_` (spec: `__`), so its own `_fcai_*` names don't leak into the script. It also removes modules imported from the script's folder after the run (Review Focus 2).
4. The integration test runs the wrapper through `_sandbox_test` (the real FreeCAD subprocess), not through the tool handler, because the handler's live `exec` needs the GUI process.

## Review Focus

1. **`extra_skill_dirs` hand-edited as a string** (`"~/.claude/skills"` instead of a list): expect it to be treated as one directory, not iterated character by character. Test in Task 6.
2. **Two skills each shipping `scripts/utils.py`:** expect the second skill's script to import *its own* `utils`, not the first one cached in `sys.modules`. Test in Task 4.
3. **An unquoted built-in description containing `": "`** (e.g. "holes: clearance, …"): PyYAML-based harnesses reject the whole skill. Expect the conformance test to catch it. Test in Task 8.
4. **A CRLF-authored `SKILL.md`** (written on Windows): expect frontmatter to be parsed, not injected as body text. Test in Task 1.
5. **An extra dir that is itself a skill folder** (user enters `~/.claude/skills/my-skill` rather than its parent): expect that one skill to load instead of nothing happening. Test in Task 6.

---

### Task 1: Frontmatter parser

**Files:**
- Create: `freecad_ai/extensions/skill_frontmatter.py`
- Test: `tests/unit/test_skill_frontmatter.py`

**Interfaces:**
- Produces: `parse_frontmatter(text: str) -> tuple[dict, str]` → `(fields, body)`. Values are `str`, `list[str]` (block or inline list) or `dict[str, str]` (one nested map). No frontmatter → `({}, text)`. Never raises.

- [ ] **Step 1: Write the failing tests**

```python
"""Agent Skills frontmatter subset, parsed without PyYAML."""

from freecad_ai.extensions.skill_frontmatter import parse_frontmatter


def test_no_frontmatter_returns_text_as_body():
    assert parse_frontmatter("# Title\nBody\n") == ({}, "# Title\nBody\n")


def test_plain_and_quoted_scalars():
    fm, body = parse_frontmatter(
        "---\nname: gear\ndescription: \"Holes: clearance\"\n"
        "license: 'It''s MIT'\n---\n# Gear\n")
    assert fm == {"name": "gear", "description": "Holes: clearance",
                  "license": "It's MIT"}
    assert body == "# Gear\n"


def test_folded_block_scalar():
    fm, _ = parse_frontmatter(
        "---\ndescription: >\n  Line one\n  continues here.\n\n  Second para.\nname: x\n---\n")
    assert fm["description"] == "Line one continues here.\nSecond para."
    assert fm["name"] == "x"


def test_literal_block_scalar_with_chomping_indicator():
    fm, _ = parse_frontmatter("---\ndescription: |-\n  a\n  b\n---\n")
    assert fm["description"] == "a\nb"


def test_metadata_map_and_allowed_tools_list():
    fm, _ = parse_frontmatter(
        "---\nmetadata:\n  author: alf\n  version: \"1.0\"\n"
        "allowed-tools:\n  - Read\n  - Bash\n---\n")
    assert fm["metadata"] == {"author": "alf", "version": "1.0"}
    assert fm["allowed-tools"] == ["Read", "Bash"]


def test_inline_list_and_string_allowed_tools():
    assert parse_frontmatter("---\nallowed-tools: [Read, Grep]\n---\n")[0] == {
        "allowed-tools": ["Read", "Grep"]}
    assert parse_frontmatter("---\nallowed-tools: Read Grep\n---\n")[0] == {
        "allowed-tools": "Read Grep"}


def test_crlf_frontmatter_is_parsed():
    fm, body = parse_frontmatter("---\r\nname: win\r\ndescription: From Windows\r\n---\r\n# W\r\n")
    assert fm == {"name": "win", "description": "From Windows"}
    assert body == "# W\r\n"


def test_unterminated_frontmatter_is_body():
    text = "---\nname: x\n# never closed\n"
    assert parse_frontmatter(text) == ({}, text)


def test_garbage_lines_are_ignored_not_raised():
    fm, _ = parse_frontmatter("---\n: nope\n  stray indent\nname: ok\n{weird\n---\n")
    assert fm == {"name": "ok"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_skill_frontmatter.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'freecad_ai.extensions.skill_frontmatter'`

- [ ] **Step 3: Write the implementation**

```python
"""Minimal YAML-frontmatter parser for SKILL.md (the Agent Skills subset).

No PyYAML: the workbench takes no external dependencies. Handles what the
Agent Skills format uses: plain and quoted scalars, ``>``/``|`` block
scalars, one nested map (``metadata:``) and lists (``allowed-tools``).
Anything else is skipped; malformed input never raises.
"""

import re

_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):(?:\s+(.*))?$")


def _split(text: str) -> tuple[str, str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return "", text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "".join(lines[1:i]), "".join(lines[i + 1:])
    return "", text


def _scalar(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        inner = raw[1:-1]
        return inner.replace("''", "'") if raw[0] == "'" else inner.replace('\\"', '"')
    if " #" in raw:
        raw = raw.split(" #", 1)[0].rstrip()
    return raw


def _block_scalar(style: str, block: list[str]) -> str:
    texts = [b.strip() for b in block]
    while texts and not texts[-1]:
        texts.pop()
    if style == "|":
        return "\n".join(texts)
    paras, current = [], []
    for t in texts:
        if t:
            current.append(t)
        elif current:
            paras.append(" ".join(current))
            current = []
    if current:
        paras.append(" ".join(current))
    return "\n".join(paras)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return ``(fields, body)``; ``({}, text)`` when there is no frontmatter."""
    fm, body = _split(text)
    fields: dict = {}
    lines = fm.splitlines()
    i = 0
    while i < len(lines):
        m = _KEY_RE.match(lines[i])
        i += 1
        if not m:
            continue
        key, raw = m.group(1), (m.group(2) or "").strip()
        block = []
        while i < len(lines) and (not lines[i].strip() or lines[i][:1] in " \t"):
            block.append(lines[i])
            i += 1
        if raw[:1] in (">", "|"):
            fields[key] = _block_scalar(raw[0], block)
        elif raw.startswith("[") and raw.endswith("]"):
            fields[key] = [_scalar(p) for p in raw[1:-1].split(",") if p.strip()]
        elif raw:
            extra = " ".join(b.strip() for b in block if b.strip())
            fields[key] = _scalar(f"{raw} {extra}" if extra else raw)
        else:
            items = [b.strip() for b in block if b.strip()]
            if items and all(it.startswith("- ") for it in items):
                fields[key] = [_scalar(it[2:]) for it in items]
            elif items:
                sub = {}
                for it in items:
                    mm = _KEY_RE.match(it)
                    if mm:
                        sub[mm.group(1)] = _scalar(mm.group(2) or "")
                fields[key] = sub
    return fields, body
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_skill_frontmatter.py -q`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/extensions/skill_frontmatter.py tests/unit/test_skill_frontmatter.py
git commit -m "feat(skills): stdlib frontmatter parser for the Agent Skills subset

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Registry uses the parser; body-only injection; 1024/300 descriptions

**Files:**
- Modify: `freecad_ai/extensions/skills.py` (`Skill` dataclass, `_scan_skills_dir` description block, `register`, `get_descriptions`, `execute_skill`, `get_skill_status` description block)
- Test: `tests/unit/test_skills.py` (append class `TestAgentSkillsFrontmatter`)

**Interfaces:**
- Consumes: `parse_frontmatter` (Task 1).
- Produces: `Skill.body: str`, `Skill.frontmatter: dict`; module constants `DESCRIPTION_MAX = 1024`, `PROMPT_DESCRIPTION_MAX = 300`; helpers `_describe(frontmatter: dict, body: str) -> str`, `_shorten(text: str, limit: int) -> str`. `get_skill_status()` entries gain `"compatibility": str`.

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_skills.py`)

```python
class TestAgentSkillsFrontmatter:
    def _registry(self, tmp_path, monkeypatch, skill_md, name="fm"):
        import freecad_ai.extensions.skills as skills_mod
        sd = tmp_path / "skills" / name
        sd.mkdir(parents=True)
        (sd / "SKILL.md").write_text(skill_md)
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(tmp_path / "skills"))
        monkeypatch.setattr(skills_mod, "BUILTIN_SKILLS_DIR", str(tmp_path / "none"))
        return SkillsRegistry()

    def test_long_description_kept_up_to_1024(self, tmp_path, monkeypatch):
        reg = self._registry(tmp_path, monkeypatch,
                             f"---\ndescription: {'x' * 2000}\n---\n# T\n")
        assert len(reg.get_skill("fm").description) == 1024

    def test_prompt_list_shortens_to_300(self, tmp_path, monkeypatch):
        reg = self._registry(tmp_path, monkeypatch,
                             f"---\ndescription: {'y' * 500}\n---\n# T\n")
        text = reg.get_descriptions()
        assert "y" * 299 + "…" in text and "y" * 300 not in text

    def test_folded_description(self, tmp_path, monkeypatch):
        reg = self._registry(tmp_path, monkeypatch,
                             "---\ndescription: >\n  Make gears.\n  Use for spur gears.\n---\n# T\n")
        assert reg.get_skill("fm").description == "Make gears. Use for spur gears."

    def test_injection_is_body_only(self, tmp_path, monkeypatch):
        reg = self._registry(tmp_path, monkeypatch,
                             "---\nname: fm\ndescription: D\n---\n# Body title\nSteps.\n")
        injected = reg.execute_skill("fm")["inject_prompt"]
        assert injected.startswith("# Body title")
        assert "description: D" not in injected
        assert reg.get_skill("fm").content.startswith("---\nname: fm")  # optimizer reads this

    def test_frontmatter_fields_stored(self, tmp_path, monkeypatch):
        reg = self._registry(tmp_path, monkeypatch,
                             "---\nname: other-name\ndescription: D\ncompatibility: FreeCAD 1.1\n"
                             "metadata:\n  author: alf\n---\n# T\n")
        skill = reg.get_skill("fm")
        assert skill.trigger == "/fm"                     # folder wins over name:
        assert skill.frontmatter["compatibility"] == "FreeCAD 1.1"
        assert skill.frontmatter["metadata"] == {"author": "alf"}

    def test_status_reads_frontmatter_description(self, tmp_path, monkeypatch):
        self._registry(tmp_path, monkeypatch,
                       "---\ndescription: From frontmatter\ncompatibility: FreeCAD 1.1\n---\n# T\nBody line\n")
        (info,) = SkillsRegistry.get_skill_status()
        assert info["description"] == "From frontmatter"
        assert info["compatibility"] == "FreeCAD 1.1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_skills.py -q -k AgentSkillsFrontmatter`
Expected: 6 failed (e.g. `assert 100 == 1024`, `AttributeError: 'Skill' object has no attribute 'frontmatter'`)

- [ ] **Step 3: Implement**

In `freecad_ai/extensions/skills.py`:

Add the import and constants under `from ..config import SKILLS_DIR`:

```python
from .skill_frontmatter import parse_frontmatter

DESCRIPTION_MAX = 1024        # Agent Skills limit
PROMPT_DESCRIPTION_MAX = 300  # what the system-prompt skill list shows
_FALLBACK_DESCRIPTION_MAX = 100
```

Add to the `Skill` dataclass, after `content`:

```python
    body: str = ""  # SKILL.md without frontmatter: what gets injected
    frontmatter: dict = field(default_factory=dict)
```

Add module helpers next to `_reference_summary`:

```python
def _describe(frontmatter: dict, body: str) -> str:
    """Frontmatter description, else the body's first non-heading line."""
    desc = frontmatter.get("description")
    if isinstance(desc, str) and desc.strip():
        return desc.strip()[:DESCRIPTION_MAX]
    for line in body.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line[:_FALLBACK_DESCRIPTION_MAX]
    return ""


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"
```

In `_scan_skills_dir`, replace everything from `# Extract description:` through the `if not description:` loop with:

```python
            frontmatter, body = parse_frontmatter(content)
            description = _describe(frontmatter, body)
```

and add `body=body, frontmatter=frontmatter,` to the `Skill(...)` call.

In `register`, add `body=content,` to the `Skill(...)` call.

In `get_descriptions`, replace `parts.append(skill.description)` with:

```python
                parts.append(_shorten(skill.description, PROMPT_DESCRIPTION_MAX))
```

In `execute_skill`, replace `content = skill.content + self.render_references_manifest(skill)` with:

```python
        content = skill.body + self.render_references_manifest(skill)
```

In `get_skill_status`, replace the block from `description = ""` through `except Exception: pass` with:

```python
            description = compatibility = ""
            try:
                with open(active_path, "r", encoding="utf-8") as f:
                    frontmatter, body = parse_frontmatter(f.read())
                description = _describe(frontmatter, body)
                compat = frontmatter.get("compatibility")
                compatibility = compat if isinstance(compat, str) else ""
            except Exception:
                pass
```

and add `"compatibility": compatibility,` to the `results.append({...})` dict.

- [ ] **Step 4: Run the skills tests**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_skills.py tests/unit/test_skill_command_errors.py -q`
Expected: all pass (existing frontmatter tests at `test_skills.py:84-133` still pass: their descriptions are short).

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/extensions/skills.py tests/unit/test_skills.py
git commit -m "feat(skills): full frontmatter descriptions, body-only injection

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Recursive file allowlist, path keys, legacy aliases, grouped manifest

**Files:**
- Modify: `freecad_ai/extensions/skills.py` (`Skill`, `_scan_skills_dir` references block, `render_references_manifest`, `get_skill_resource`)
- Modify: `freecad_ai/tools/freecad_tools.py` (`USE_SKILL` description + `resource` param text)
- Test: `tests/unit/test_skills.py` (append class `TestSkillFiles`)

**Interfaces:**
- Consumes: `Skill`, `_reference_summary` (existing).
- Produces: `Skill.files: dict[str, str]` (POSIX relative key → abspath); `Skill.references` unchanged in meaning (alias → abspath); `SkillsRegistry._resolve_key(skill, requested: str) -> str | None`; constants `MAX_SKILL_FILES = 500`, `MAX_RESOURCE_BYTES = 100_000`, `MANIFEST_MAX = 40`; helper `_scan_skill_files(skill_dir: str) -> dict[str, str]`.

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_skills.py`)

```python
class TestSkillFiles:
    def _skill(self, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        sd = tmp_path / "skills" / "pdfish"
        (sd / "references" / "tables").mkdir(parents=True)
        (sd / "scripts" / "__pycache__").mkdir(parents=True)
        (sd / "assets").mkdir()
        (sd / ".git").mkdir()
        (sd / "SKILL.md").write_text("---\nname: pdfish\ndescription: D\n---\nSee [forms](forms.md).\n")
        (sd / "handler.py").write_text("x = 1\n")
        (sd / "VALIDATION.md").write_text("# V\n")
        (sd / "forms.md").write_text("# Forms\nHow to fill forms.\n")
        (sd / "references" / "m3.md").write_text("# M3\nM3 table.\n")
        (sd / "references" / "tables" / "m4.md").write_text("# M4\nM4 table.\n")
        (sd / "scripts" / "fill.py").write_text("print('fill')\n")
        (sd / "scripts" / "__pycache__" / "fill.pyc").write_bytes(b"\0")
        (sd / "assets" / "logo.png").write_bytes(b"\x89PNG\0\0data")
        (sd / ".hidden").write_text("no")
        (sd / ".git" / "HEAD").write_text("no")
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(tmp_path / "skills"))
        monkeypatch.setattr(skills_mod, "BUILTIN_SKILLS_DIR", str(tmp_path / "none"))
        return sd

    def test_recursive_keys_and_skip_list(self, tmp_path, monkeypatch):
        self._skill(tmp_path, monkeypatch)
        skill = SkillsRegistry().get_skill("pdfish")
        assert set(skill.files) == {"forms.md", "references/m3.md",
                                    "references/tables/m4.md", "scripts/fill.py",
                                    "assets/logo.png"}
        assert set(skill.references) == {"m3"}   # legacy: top level of references/ only

    def test_symlink_leaving_the_skill_is_dropped(self, tmp_path, monkeypatch):
        sd = self._skill(tmp_path, monkeypatch)
        secret = tmp_path / "secret.txt"
        secret.write_text("TOKEN")
        os.symlink(secret, sd / "references" / "leak.md")
        os.symlink(sd / "forms.md", sd / "references" / "inside.md")
        skill = SkillsRegistry().get_skill("pdfish")
        assert "references/leak.md" not in skill.files
        assert "references/inside.md" in skill.files

    def test_file_cap(self, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        self._skill(tmp_path, monkeypatch)
        monkeypatch.setattr(skills_mod, "MAX_SKILL_FILES", 2)
        assert len(SkillsRegistry().get_skill("pdfish").files) == 2

    def test_path_keys_resolve_with_normalisation(self, tmp_path, monkeypatch):
        self._skill(tmp_path, monkeypatch)
        reg = SkillsRegistry()
        for key in ["references/tables/m4.md", "./references/tables/m4.md",
                    "references\\tables\\M4.md"]:
            assert "M4 table." in reg.get_skill_resource("pdfish", key)["output"], key
        assert "How to fill" in reg.get_skill_resource("pdfish", "forms.md")["output"]

    def test_legacy_alias_still_resolves(self, tmp_path, monkeypatch):
        self._skill(tmp_path, monkeypatch)
        assert "M3 table." in SkillsRegistry().get_skill_resource("pdfish", "m3")["output"]

    def test_binary_resource_is_refused_with_size(self, tmp_path, monkeypatch):
        self._skill(tmp_path, monkeypatch)
        err = SkillsRegistry().get_skill_resource("pdfish", "assets/logo.png")["error"]
        assert "bytes" in err and "script" in err

    def test_large_resource_is_truncated(self, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        self._skill(tmp_path, monkeypatch)
        monkeypatch.setattr(skills_mod, "MAX_RESOURCE_BYTES", 5)
        out = SkillsRegistry().get_skill_resource("pdfish", "forms.md")["output"]
        assert out.startswith("# For") and "truncated" in out

    def test_unknown_key_lists_at_most_20(self, tmp_path, monkeypatch):
        sd = self._skill(tmp_path, monkeypatch)
        for i in range(30):
            (sd / "references" / f"r{i:02d}.md").write_text("x\n")
        err = SkillsRegistry().get_skill_resource("pdfish", "nope")["error"]
        assert err.count("references/r") == 20 and "more" in err

    def test_manifest_groups(self, tmp_path, monkeypatch):
        self._skill(tmp_path, monkeypatch)
        text = SkillsRegistry().execute_skill("pdfish")["inject_prompt"]
        docs, scripts, assets = (text.index("### Documents"), text.index("### Scripts"),
                                 text.index("### Assets"))
        assert docs < text.index("`forms.md`") < scripts
        assert "How to fill forms." in text
        assert "(resource='m3')" in text                     # legacy hint kept
        assert scripts < text.index("`scripts/fill.py`") < assets
        assert "run_skill_script(skill='pdfish'" in text
        assert assets < text.index("`assets/logo.png`")

    def test_manifest_cap(self, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        self._skill(tmp_path, monkeypatch)
        monkeypatch.setattr(skills_mod, "MANIFEST_MAX", 2)
        text = SkillsRegistry().execute_skill("pdfish")["inject_prompt"]
        assert "…and 3 more" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_skills.py -q -k TestSkillFiles`
Expected: 10 failed (`AttributeError: 'Skill' object has no attribute 'files'` and similar)

- [ ] **Step 3: Implement**

In `freecad_ai/extensions/skills.py`, add `import logging` with the other imports, then after the Task 2 constants:

```python
MAX_SKILL_FILES = 500
MAX_RESOURCE_BYTES = 100_000
MANIFEST_MAX = 40
_SKIP_ROOT_FILES = {"SKILL.md", "handler.py", "VALIDATION.md"}
_SKIP_DIRS = {"__pycache__", "node_modules", ".venv", ".git"}

_log = logging.getLogger(__name__)
```

Add to `Skill`, after `references`:

```python
    files: dict = field(default_factory=dict)  # "dir/file.ext" -> abspath (allowlist)
```

Add the scanner next to `_reference_summary`:

```python
def _scan_skill_files(skill_dir: str) -> dict:
    """Allowlist of every file in a skill folder, keyed by POSIX relative path.

    Built once at load time; reads only ever pick from it, so the model can
    name a path but never reach outside the folder. Symlinks are kept only
    when their target is inside the skill's own realpath.
    """
    root = os.path.realpath(skill_dir)
    files = {}
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        dirnames[:] = sorted(d for d in dirnames
                             if not d.startswith(".") and d not in _SKIP_DIRS)
        rel_dir = os.path.relpath(dirpath, skill_dir)
        for fn in sorted(filenames):
            if fn.startswith(".") or (rel_dir == "." and fn in _SKIP_ROOT_FILES):
                continue
            path = os.path.join(dirpath, fn)
            real = os.path.realpath(path)
            if os.path.commonpath([root, real]) != root or not os.path.isfile(real):
                continue
            key = fn if rel_dir == "." else f"{rel_dir.replace(os.sep, '/')}/{fn}"
            if len(files) >= MAX_SKILL_FILES:
                _log.warning("Skill %s has more than %d files; the rest are ignored",
                             skill_dir, MAX_SKILL_FILES)
                return files
            files[key] = path
    return files
```

In `_scan_skills_dir`, replace the `references = {}` … `references[key] = ref_path` block with:

```python
            # Tier-3 progressive disclosure: every file in the folder, keyed by
            # its relative path (Agent Skills links), plus today's bare-stem
            # aliases for files directly in references/ (last sorted wins).
            files = _scan_skill_files(skill_dir)
            references = {}
            for key in sorted(files):
                parts = key.split("/")
                if len(parts) == 2 and parts[0] == "references":
                    references[os.path.splitext(parts[1])[0].lower()] = files[key]
```

and add `files=files,` to the `Skill(...)` call.

Replace `render_references_manifest` and `get_skill_resource` with:

```python
    def render_references_manifest(self, skill: Skill) -> str:
        """Markdown block advertising a skill's files (tier-3 disclosure)."""
        if not skill.files:
            return ""
        alias_of = {path: alias for alias, path in skill.references.items()}
        groups = {"Documents": [], "Scripts": [], "Assets": []}
        for key in sorted(skill.files):
            if key.startswith("scripts/"):
                groups["Scripts"].append(f"- `{key}`")
            elif key.startswith("assets/"):
                groups["Assets"].append(f"- `{key}`")
            else:
                path = skill.files[key]
                bullet = f"- `{key}`"
                if path in alias_of:
                    bullet += f" (resource='{alias_of[path]}')"
                summary = _reference_summary(path)
                if summary:
                    bullet += f" — {summary}"
                groups["Documents"].append(bullet)
        lines = [
            "\n\n## Available references",
            f"Read a file with use_skill(name='{skill.name}', resource='<path>').",
        ]
        if groups["Scripts"]:
            lines.append(f"Run a Python script with run_skill_script(skill='{skill.name}', "
                         f"script='<path>', args='<command-line args>').")
        shown = 0
        for title, bullets in groups.items():
            if not bullets or shown >= MANIFEST_MAX:
                continue
            take = bullets[:MANIFEST_MAX - shown]
            lines.append(f"### {title}")
            lines.extend(take)
            shown += len(take)
        if len(skill.files) > shown:
            lines.append(f"…and {len(skill.files) - shown} more")
        return "\n".join(lines)

    @staticmethod
    def _resolve_key(skill: Skill, requested: str) -> "str | None":
        """Map a requested path or legacy alias onto an allowlist key."""
        r = requested.strip().replace("\\", "/")
        while r.startswith("./"):
            r = r[2:]
        if r in skill.files:
            return r
        lowered = r.lower()
        for key in skill.files:
            if key.lower() == lowered:
                return key
        alias_path = skill.references.get(os.path.splitext(r)[0].lower())
        if alias_path:
            for key, path in skill.files.items():
                if path == alias_path:
                    return key
        return None

    def get_skill_resource(self, name: str, resource: str) -> dict:
        """Return the contents of one file from a skill's allowlist.

        `resource` is a relative path or a legacy references/ alias; it is
        only ever looked up in the pre-scanned Skill.files, never opened as
        a path, so directory traversal is impossible.

        Returns {"output": contents} or {"error": message}.
        """
        skill = self._skills.get(name)
        if not skill:
            return {"error": f"Unknown skill: {name}"}
        if not skill.files:
            return {"error": f"Skill '{name}' has no references."}
        key = self._resolve_key(skill, resource)
        if key is None:
            keys = sorted(skill.files)
            listed = ", ".join(keys[:20])
            if len(keys) > 20:
                listed += f", …and {len(keys) - 20} more"
            return {"error": (f"Reference '{resource}' not found in skill "
                              f"'{name}'. Available: {listed}")}
        path = skill.files[key]
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                data = f.read(MAX_RESOURCE_BYTES + 1)
        except OSError as e:
            return {"error": f"Could not read reference '{resource}': {e}"}
        truncated = len(data) > MAX_RESOURCE_BYTES
        data = data[:MAX_RESOURCE_BYTES]
        binary = b"\0" in data[:8192]
        if not binary:
            try:
                text = data.decode("utf-8", "ignore" if truncated else "strict")
            except UnicodeDecodeError:
                binary = True
        if binary:
            return {"error": (f"'{key}' is a binary file ({size} bytes). It is "
                              f"meant for the skill's scripts, not for reading.")}
        if truncated:
            text += (f"\n\n[truncated: showing the first {MAX_RESOURCE_BYTES} "
                     f"of {size} bytes]")
        return {"output": text}
```

In `freecad_ai/tools/freecad_tools.py`, in `USE_SKILL` replace the description's last sentence and the `resource` param text:

```python
        "returned instructions using your tools. If the skill lists 'Available "
        "references', read one on demand by calling use_skill again with the "
        "same name and that file's path (or alias) as `resource`; run a listed "
        "Python script with run_skill_script."
```

```python
        ToolParam("resource", "string",
                  "Optional file from the skill's 'Available references' list: "
                  "a relative path like 'references/tables.md', or a listed alias. "
                  "Loads that file instead of the skill itself",
                  required=False, default=""),
```

- [ ] **Step 4: Run the skills tests**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_skills.py -q`
Expected: all pass, including the unchanged traversal test (`../SKILL`, `/etc/passwd`, `..\\SKILL`) and `test_execute_skill_appends_references_manifest`.

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/extensions/skills.py freecad_ai/tools/freecad_tools.py tests/unit/test_skills.py
git commit -m "feat(skills): every file in a skill is reachable by its relative path

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Script wrapper

**Files:**
- Create: `freecad_ai/extensions/skill_scripts.py`
- Test: `tests/unit/test_skill_scripts.py`

**Interfaces:**
- Produces: `build_script_wrapper(path: str, argv: list[str]) -> str`, Python source that runs `path` as `__main__` with `sys.argv = [path, *argv]`, the script's directory first on `sys.path`, globals not starting with `_` passed in, `SystemExit(0|None)` swallowed, any other exit raised as `RuntimeError("script exited with status …")`, and `sys.argv`/`sys.path` plus modules imported from the script's directory restored/removed afterwards.

- [ ] **Step 1: Write the failing tests**

```python
"""build_script_wrapper, exec'd in plain Python (no FreeCAD needed)."""

import sys

import pytest

from freecad_ai.extensions.skill_scripts import build_script_wrapper


def _run(path, argv=(), ns=None):
    ns = {"__builtins__": __builtins__} if ns is None else ns
    exec(build_script_wrapper(str(path), list(argv)), ns)


def _script(tmp_path, body, name="main.py", sub="scripts"):
    d = tmp_path / sub
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(body)
    return p


def test_runs_as_main_with_file_and_argv(tmp_path, capsys):
    p = _script(tmp_path, "import sys\nif __name__ == '__main__':\n"
                          "    print(__file__, sys.argv[1:])\n")
    _run(p, ["--size", "a b"])
    assert capsys.readouterr().out.strip() == f"{p} ['--size', 'a b']"


def test_future_import_works(tmp_path, capsys):
    _run(_script(tmp_path, "from __future__ import annotations\nprint('ok')\n"))
    assert capsys.readouterr().out == "ok\n"


def test_freecad_globals_are_passed_in(tmp_path, capsys):
    _run(_script(tmp_path, "print(App)\n"), ns={"__builtins__": __builtins__, "App": "APP"})
    assert capsys.readouterr().out == "APP\n"


@pytest.mark.parametrize("call", ["sys.exit()", "sys.exit(0)"])
def test_clean_exit_is_success(tmp_path, call):
    _run(_script(tmp_path, f"import sys\n{call}\n"))


def test_nonzero_exit_is_an_error(tmp_path):
    with pytest.raises(RuntimeError, match="status 2"):
        _run(_script(tmp_path, "import sys\nsys.exit(2)\n"))


def test_argv_and_path_restored_after_exception(tmp_path):
    argv, path = sys.argv[:], sys.path[:]
    with pytest.raises(ValueError):
        _run(_script(tmp_path, "raise ValueError('x')\n"), ["a"])
    assert sys.argv == argv and sys.path == path


def test_sibling_import(tmp_path, capsys):
    _script(tmp_path, "VALUE = 'sibling'\n", name="helper.py")
    _run(_script(tmp_path, "import helper\nprint(helper.VALUE)\n"))
    assert capsys.readouterr().out == "sibling\n"


def test_same_named_sibling_modules_do_not_leak_between_skills(tmp_path, capsys):
    for skill in ("one", "two"):
        _script(tmp_path, f"VALUE = '{skill}'\n", name="fcai_utils.py", sub=f"{skill}/scripts")
        _script(tmp_path, "import fcai_utils\nprint(fcai_utils.VALUE)\n", sub=f"{skill}/scripts")
    _run(tmp_path / "one" / "scripts" / "main.py")
    _run(tmp_path / "two" / "scripts" / "main.py")
    assert capsys.readouterr().out == "one\ntwo\n"
    assert "fcai_utils" not in sys.modules
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_skill_scripts.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'freecad_ai.extensions.skill_scripts'`

- [ ] **Step 3: Write the implementation**

```python
"""Run a skill's Python script inside FreeCAD the way a CLI would run it.

The script is not pasted into execute_code: a preamble would break
``from __future__`` imports and shift traceback line numbers. Instead
execute_code runs a small runpy wrapper, so the script sees
``__name__ == "__main__"``, its real ``__file__`` (for assets/), CLI-style
``sys.argv`` and its own directory on ``sys.path``.
"""

import os

_TEMPLATE = '''\
import sys as _fcai_sys, runpy as _fcai_runpy
_fcai_saved = (_fcai_sys.argv[:], _fcai_sys.path[:])
_fcai_sys.argv = {argv}
_fcai_sys.path.insert(0, {script_dir})
try:
    _fcai_runpy.run_path({script}, run_name="__main__", init_globals={{
        _k: _v for _k, _v in globals().items() if not _k.startswith("_")}})
except SystemExit as _fcai_exit:
    if _fcai_exit.code not in (None, 0):
        raise RuntimeError("script exited with status %r" % (_fcai_exit.code,))
finally:
    _fcai_sys.argv, _fcai_sys.path[:] = _fcai_saved
    for _fcai_name, _fcai_mod in list(_fcai_sys.modules.items()):
        if (getattr(_fcai_mod, "__file__", None) or "").startswith({dir_prefix}):
            del _fcai_sys.modules[_fcai_name]
'''


def build_script_wrapper(path: str, argv: list) -> str:
    """Python source that runs ``path`` as ``__main__`` with ``argv``.

    A clean ``sys.exit()`` counts as success; any other exit status is
    raised, because inside FreeCAD an uncaught SystemExit must never reach
    the host process. Modules imported from the script's own directory are
    dropped afterwards, so two skills that both ship ``utils.py`` each get
    their own.
    """
    script_dir = os.path.dirname(os.path.abspath(path))
    return _TEMPLATE.format(
        script=repr(path),
        argv=repr([path, *argv]),
        script_dir=repr(script_dir),
        dir_prefix=repr(script_dir + os.sep),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_skill_scripts.py -q`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/extensions/skill_scripts.py tests/unit/test_skill_scripts.py
git commit -m "feat(skills): runpy wrapper that runs a skill script as __main__

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `run_skill_script` tool

**Files:**
- Modify: `freecad_ai/extensions/skills.py` (add `SkillsRegistry.resolve_script`)
- Modify: `freecad_ai/tools/freecad_tools.py` (handler + `RUN_SKILL_SCRIPT` after `USE_SKILL`; add to `ALL_TOOLS` after `USE_SKILL`)
- Test: `tests/unit/test_run_skill_script.py`

**Interfaces:**
- Consumes: `Skill.files`, `SkillsRegistry._resolve_key` (Task 3); `build_script_wrapper` (Task 4); `execute_code`, `_validate_code` (`freecad_ai/core/executor.py`); `get_dangerous_mode()` (`freecad_ai/core/dangerous_mode.py`, `.active: bool`).
- Produces: `SkillsRegistry.resolve_script(name: str, script: str) -> tuple[str, str, list[str]]` → `(abspath, error, all_py_paths_in_skill)`; tool `run_skill_script(skill, script, args="")`, category `"general"`.

- [ ] **Step 1: Write the failing tests**

```python
"""run_skill_script: allowlisted .py files through execute_code."""

from types import SimpleNamespace

import pytest

import freecad_ai.extensions.skills as skills_mod
from freecad_ai.tools import freecad_tools as ft


@pytest.fixture
def skill(tmp_path, monkeypatch):
    sd = tmp_path / "skills" / "maker"
    (sd / "scripts").mkdir(parents=True)
    (sd / "SKILL.md").write_text("# Maker\nMakes things.\n")
    (sd / "scripts" / "make.py").write_text("print('made')\n")
    (sd / "scripts" / "run.sh").write_text("echo no\n")
    monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setattr(skills_mod, "BUILTIN_SKILLS_DIR", str(tmp_path / "none"))
    return sd


@pytest.fixture
def calls(monkeypatch):
    recorded = []

    def fake_execute_code(code, skip_safety=False):
        recorded.append({"code": code, "skip_safety": skip_safety})
        return SimpleNamespace(success=True, stdout="made\n", stderr="")

    monkeypatch.setattr(ft, "execute_code", fake_execute_code)
    return recorded


def _dangerous(monkeypatch, active):
    import freecad_ai.core.dangerous_mode as dm
    monkeypatch.setattr(dm, "get_dangerous_mode", lambda: SimpleNamespace(active=active))


def test_runs_the_wrapper_with_split_args(skill, calls, monkeypatch):
    _dangerous(monkeypatch, False)
    result = ft._handle_run_skill_script("maker", "scripts/make.py", "--size 10 'a b'")
    assert result.success and result.output == "made"
    code = calls[0]["code"]
    assert "runpy" in code and repr(str(skill / "scripts" / "make.py")) in code
    assert "'--size', '10', 'a b'" in code
    assert calls[0]["skip_safety"] is False


def test_non_python_script_points_at_use_skill(skill, calls):
    result = ft._handle_run_skill_script("maker", "scripts/run.sh")
    assert not result.success and "use_skill" in result.error and not calls


def test_unknown_script_lists_scripts(skill, calls):
    result = ft._handle_run_skill_script("maker", "scripts/nope.py")
    assert not result.success and "scripts/make.py" in result.error


def test_unknown_skill(skill, calls):
    assert "Unknown skill" in ft._handle_run_skill_script("nope", "x.py").error


def test_dangerous_script_is_rejected_before_running(skill, calls, monkeypatch):
    _dangerous(monkeypatch, False)
    (skill / "scripts" / "make.py").write_text("import subprocess\n")
    result = ft._handle_run_skill_script("maker", "scripts/make.py")
    assert not result.success and "subprocess" in result.error and not calls


def test_dangerous_sibling_module_is_rejected_too(skill, calls, monkeypatch):
    _dangerous(monkeypatch, False)
    (skill / "scripts" / "helper.py").write_text("import subprocess\n")
    result = ft._handle_run_skill_script("maker", "scripts/make.py")
    assert not result.success and "scripts/helper.py" in result.error and not calls


def test_dangerous_mode_skips_validation(skill, calls, monkeypatch):
    _dangerous(monkeypatch, True)
    (skill / "scripts" / "make.py").write_text("import subprocess\n")
    assert ft._handle_run_skill_script("maker", "scripts/make.py").success
    assert calls[0]["skip_safety"] is True


def test_unbalanced_quotes_in_args(skill, calls):
    result = ft._handle_run_skill_script("maker", "scripts/make.py", "'open")
    assert not result.success and "args" in result.error and not calls


def test_registered_as_general_tool():
    tool = next(t for t in ft.ALL_TOOLS if t.name == "run_skill_script")
    assert tool.category == "general"
    assert [p.name for p in tool.parameters] == ["skill", "script", "args"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_run_skill_script.py -q`
Expected: FAIL with `AttributeError: module 'freecad_ai.tools.freecad_tools' has no attribute '_handle_run_skill_script'`

- [ ] **Step 3: Implement**

In `freecad_ai/extensions/skills.py`, add to `SkillsRegistry` after `get_skill_resource`:

```python
    def resolve_script(self, name: str, script: str) -> tuple:
        """Resolve a runnable script from a skill's allowlist.

        Returns (abspath, "", py_paths) or ("", error, []). py_paths lists
        every .py file in the skill, so the caller can validate the modules
        a script may import, not just the script itself.
        """
        skill = self._skills.get(name)
        if not skill:
            return "", f"Unknown skill: {name}", []
        key = self._resolve_key(skill, script)
        scripts = sorted(k for k in skill.files if k.endswith(".py"))
        if key is None:
            return "", (f"Script '{script}' not found in skill '{name}'. "
                        f"Available: {', '.join(scripts) or 'none'}"), []
        if not key.endswith(".py"):
            return "", (f"Only Python scripts run inside FreeCAD. Read '{key}' "
                        f"with use_skill(name='{name}', resource='{key}') instead."), []
        return skill.files[key], "", [(k, skill.files[k]) for k in scripts]
```

In `freecad_ai/tools/freecad_tools.py`, after the `USE_SKILL = ToolDefinition(...)` block:

```python
# ── run_skill_script ───────────────────────────────────────

def _handle_run_skill_script(skill: str, script: str, args: str = "") -> ToolResult:
    """Run a skill's Python script (Agent Skills scripts/) inside FreeCAD."""
    import shlex
    from ..core.dangerous_mode import get_dangerous_mode
    from ..core.executor import _validate_code
    from ..extensions.skill_scripts import build_script_wrapper
    from ..extensions.skills import SkillsRegistry

    path, err, py_files = SkillsRegistry().resolve_script(skill, script)
    if err:
        return ToolResult(success=False, output="", error=err)
    try:
        argv = shlex.split(args)
    except ValueError as e:
        return ToolResult(success=False, output="", error=f"Could not parse args: {e}")
    dangerous = get_dangerous_mode().active
    if not dangerous:
        # execute_code only sees the runpy wrapper, so validate the script and
        # every module it could import from its skill here.
        warnings = []
        for key, py_path in py_files:
            try:
                with open(py_path, "r", encoding="utf-8") as f:
                    text = f.read()
            except (OSError, UnicodeDecodeError) as e:
                return ToolResult(success=False, output="",
                                  error=f"Could not read {key}: {e}")
            warnings += [f"{key}: {w}" for w in _validate_code(text)]
        if warnings:
            return ToolResult(success=False, output="",
                              error="Pre-execution validation failed:\n" + "\n".join(warnings))
    result = execute_code(build_script_wrapper(path, argv), skip_safety=dangerous)
    if result.success:
        out = result.stdout.strip() or f"Script '{script}' ran successfully (no output)."
        return ToolResult(success=True, output=out,
                          data={"script": path, "stdout": result.stdout})
    return ToolResult(success=False, output=result.stdout, error=result.stderr)


RUN_SKILL_SCRIPT = ToolDefinition(
    name="run_skill_script",
    description=(
        "Run a Python script that a skill ships in its folder (listed under "
        "'Scripts' when the skill is loaded). It runs inside FreeCAD like a "
        "command-line program: `args` is split like a shell command line into "
        "sys.argv. Use the script path exactly as listed, e.g. 'scripts/make.py'."
    ),
    category="general",
    parameters=[
        ToolParam("skill", "string", "Skill name, e.g. 'gear'"),
        ToolParam("script", "string", "Script path from the skill's list, e.g. 'scripts/make.py'"),
        ToolParam("args", "string", "Command-line arguments, e.g. \"--teeth 20 --out 'my gear'\"",
                  required=False, default=""),
    ],
    handler=_handle_run_skill_script,
)
```

In `ALL_TOOLS`, add `RUN_SKILL_SCRIPT,` on the line after `USE_SKILL,`.

- [ ] **Step 4: Run the new tests and the full unit suite** (the tool list is used by MCP, routing and prompt tests)

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_run_skill_script.py -q`
Expected: 9 passed
Run: `env PYTHONPATH= .venv/bin/pytest tests/unit -q --ignore=tests/unit/test_document_attach.py`
Expected: all pass. If a test pins the tool count or list, update it to include `run_skill_script` and name it in the commit message.

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/extensions/skills.py freecad_ai/tools/freecad_tools.py tests/unit/test_run_skill_script.py
git commit -m "feat(skills): run_skill_script runs a skill's Python scripts in FreeCAD

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `extra_skill_dirs` setting and scan order

**Files:**
- Modify: `freecad_ai/config.py` (`AppConfig`, after `merge_agents_md`)
- Modify: `freecad_ai/extensions/skills.py` (`SkillsRegistry.__init__`, `_load_skills`, `_scan_skills_dir`, `get_skill_status`)
- Test: `tests/unit/test_skills.py` (append class `TestExtraSkillDirs`)

**Interfaces:**
- Produces: `AppConfig.extra_skill_dirs: list` (default `[]`); `SkillsRegistry(extra_dirs: list | None = None)` (None → read config); `SkillsRegistry.get_skill_status(extra_dirs: list | None = None)` with source `"external"` and entry key `"external_path"`; helper `_extra_skill_dirs(dirs) -> list[str]`.

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_skills.py`)

```python
class TestExtraSkillDirs:
    def _write(self, root, name, desc):
        sd = root / name
        sd.mkdir(parents=True)
        (sd / "SKILL.md").write_text(f"---\ndescription: {desc}\n---\n# {name}\n")

    def _dirs(self, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        builtin, extra, user = (tmp_path / "builtin", tmp_path / "extra", tmp_path / "user")
        self._write(builtin, "gear", "built-in gear")
        self._write(builtin, "lattice", "built-in lattice")
        self._write(extra, "gear", "external gear")
        self._write(extra, "lattice", "external lattice")
        self._write(user, "lattice", "user lattice")
        monkeypatch.setattr(skills_mod, "BUILTIN_SKILLS_DIR", str(builtin))
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(user))
        return extra

    def test_default_is_empty(self):
        from freecad_ai.config import AppConfig
        assert AppConfig().extra_skill_dirs == []
        assert AppConfig.from_dict({"extra_skill_dirs": ["~/x"]}).extra_skill_dirs == ["~/x"]

    def test_precedence_builtin_extra_user(self, tmp_path, monkeypatch):
        extra = self._dirs(tmp_path, monkeypatch)
        reg = SkillsRegistry(extra_dirs=[str(extra)])
        assert reg.get_skill("gear").description == "external gear"
        assert reg.get_skill("lattice").description == "user lattice"

    def test_reads_config_when_not_given(self, tmp_path, monkeypatch):
        import freecad_ai.config as config_mod
        extra = self._dirs(tmp_path, monkeypatch)
        cfg = config_mod.AppConfig()
        cfg.extra_skill_dirs = [str(extra)]
        monkeypatch.setattr(config_mod, "load_config", lambda: cfg)
        assert SkillsRegistry().get_skill("gear").description == "external gear"

    def test_tilde_expanded_and_missing_dir_skipped(self, tmp_path, monkeypatch):
        extra = self._dirs(tmp_path, monkeypatch)
        monkeypatch.setenv("HOME", str(tmp_path))
        reg = SkillsRegistry(extra_dirs=["~/extra", str(tmp_path / "missing")])
        assert reg.get_skill("gear").description == "external gear"

    def test_string_instead_of_list_is_one_dir(self, tmp_path, monkeypatch):
        extra = self._dirs(tmp_path, monkeypatch)
        reg = SkillsRegistry(extra_dirs=str(extra))
        assert reg.get_skill("gear").description == "external gear"

    def test_extra_dir_that_is_itself_a_skill(self, tmp_path, monkeypatch):
        extra = self._dirs(tmp_path, monkeypatch)
        self._write(tmp_path / "solo", "my-skill", "solo skill")
        reg = SkillsRegistry(extra_dirs=[str(tmp_path / "solo" / "my-skill")])
        assert reg.get_skill("my-skill").description == "solo skill"

    def test_status_marks_external(self, tmp_path, monkeypatch):
        extra = self._dirs(tmp_path, monkeypatch)
        status = {s["name"]: s for s in SkillsRegistry.get_skill_status(extra_dirs=[str(extra)])}
        assert status["gear"]["source"] == "external"
        assert status["gear"]["description"] == "external gear"
        assert status["gear"]["has_user_copy"] is False   # Reset stays disabled
        assert status["lattice"]["source"] == "modified"  # user copy still wins
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_skills.py -q -k TestExtraSkillDirs`
Expected: 7 failed (`AttributeError: 'AppConfig' object has no attribute 'extra_skill_dirs'`, `TypeError: ... unexpected keyword argument 'extra_dirs'`)

- [ ] **Step 3: Implement**

In `freecad_ai/config.py`, after `merge_agents_md: bool = False`:

```python
    # Extra skill folders, e.g. ~/.claude/skills (Agent Skills format). Empty
    # (the default) scans only the built-in and user dirs, as before. Stored
    # as typed; ~ is expanded when read. Scan order: built-in, these (in list
    # order), then the user dir; a later folder's skill wins on a name clash.
    extra_skill_dirs: list = field(default_factory=list)
```

In `freecad_ai/extensions/skills.py`, add next to `_describe`:

```python
def _extra_skill_dirs(dirs) -> list:
    """Normalise extra_skill_dirs: a hand-edited string is one dir, not chars."""
    if dirs is None:
        try:
            from ..config import load_config
            dirs = load_config().extra_skill_dirs
        except Exception:
            dirs = []
    if isinstance(dirs, str):
        dirs = [dirs]
    if not isinstance(dirs, (list, tuple)):
        return []
    return [os.path.expanduser(d.strip()) for d in dirs
            if isinstance(d, str) and d.strip()]


def _skill_dirs_in(root: str) -> list:
    """(name, path) of each skill under root; root itself if it is a skill."""
    if not os.path.isdir(root):
        return []
    if os.path.isfile(os.path.join(root, "SKILL.md")):
        return [(os.path.basename(os.path.normpath(root)), root)]
    return [(entry, os.path.join(root, entry)) for entry in sorted(os.listdir(root))
            if os.path.isfile(os.path.join(root, entry, "SKILL.md"))]
```

Change `__init__` and `_load_skills`:

```python
    def __init__(self, extra_dirs=None):
        self._skills: dict[str, Skill] = {}
        self._extra_dirs = _extra_skill_dirs(extra_dirs)
        self._load_skills()

    def _load_skills(self):
        """Scan skills directories and load skill definitions.

        Order: built-in, extra_skill_dirs (list order), user dir
        (~/.config/FreeCAD/FreeCADAI/skills/). A later folder's skill wins
        on a name clash, so the user dir always has the last word.
        """
        for skills_dir in (BUILTIN_SKILLS_DIR, *self._extra_dirs, SKILLS_DIR):
            self._scan_skills_dir(skills_dir)
```

In `_scan_skills_dir`, replace the loop header and its two-line guard

```python
        if not os.path.isdir(skills_dir):
            return

        for entry in os.listdir(skills_dir):
            skill_dir = os.path.join(skills_dir, entry)
            skill_file = os.path.join(skill_dir, "SKILL.md")
            if not os.path.isdir(skill_dir) or not os.path.isfile(skill_file):
                continue
```

with

```python
        for entry, skill_dir in _skill_dirs_in(skills_dir):
            skill_file = os.path.join(skill_dir, "SKILL.md")
```

In `get_skill_status`, change the signature to `def get_skill_status(extra_dirs=None) -> list[dict]:`, and replace the two scan blocks (`# Scan built-in` … `user_skills[entry] = skill_file`) with:

```python
        builtin_skills = {n: os.path.join(p, "SKILL.md")
                          for n, p in _skill_dirs_in(BUILTIN_SKILLS_DIR)}
        external_skills = {}
        for root in _extra_skill_dirs(extra_dirs):
            external_skills.update({n: os.path.join(p, "SKILL.md")
                                    for n, p in _skill_dirs_in(root)})
        user_skills = {n: os.path.join(p, "SKILL.md")
                       for n, p in _skill_dirs_in(SKILLS_DIR)}

        all_names = sorted(set(builtin_skills) | set(external_skills) | set(user_skills))
```

(remove the old `all_names = sorted(set(builtin_skills) | set(user_skills))` line), then inside the loop add `e_path = external_skills.get(name)`, change `active_path = u_path or b_path` to `active_path = u_path or e_path or b_path`, and replace the source decision with:

```python
            if b_path and u_path:
                is_modified = _file_hash(b_path) != _file_hash(u_path)
                source = "modified" if is_modified else "built-in"
            elif u_path:
                source = "user"
            elif e_path:
                source = "external"
            else:
                source = "built-in"
```

and add `"external_path": e_path or "",` to the appended dict.

- [ ] **Step 4: Run the skills tests**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_skills.py tests/unit/test_run_skill_script.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/config.py freecad_ai/extensions/skills.py tests/unit/test_skills.py
git commit -m "feat(skills): opt-in extra_skill_dirs for skills installed for other harnesses

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Settings UI for extra skill folders

**Files:**
- Modify: `freecad_ai/ui/settings_pages/tools_page.py` (Skills group, `_show`, `_values`, `_refresh_skills_list`)
- Test: `tests/unit/test_tools_page.py` (append)

**Interfaces:**
- Consumes: `AppConfig.extra_skill_dirs`, `SkillsRegistry.get_skill_status(extra_dirs=…)` (Task 6).
- Produces: `ToolsPage.extra_skill_dirs_edit` (`QPlainTextEdit`), `ToolsPage._extra_dirs_from_widget() -> list[str]`; `_values()` gains key `"extra_skill_dirs"`.

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_tools_page.py`)

```python
def test_extra_skill_dirs_round_trip(page):
    cfg = AppConfig()
    cfg.extra_skill_dirs = ["~/.claude/skills", "/opt/skills"]
    page.load(cfg)
    assert page.extra_skill_dirs_edit.toPlainText() == "~/.claude/skills\n/opt/skills"
    page.extra_skill_dirs_edit.setPlainText("~/.claude/skills\n\n  /new  \n")
    target = AppConfig()
    page.apply_to(target)
    assert target.extra_skill_dirs == ["~/.claude/skills", "/new"]


def test_untouched_extra_skill_dirs_are_not_written(page):
    page.load(AppConfig())
    target = AppConfig()
    target.extra_skill_dirs = ["/set/elsewhere"]
    page.apply_to(target)
    assert target.extra_skill_dirs == ["/set/elsewhere"]


def test_external_skill_listed_from_widget_dirs(page, tmp_path, monkeypatch):
    import freecad_ai.extensions.skills as skills_mod
    monkeypatch.setattr(skills_mod, "BUILTIN_SKILLS_DIR", str(tmp_path / "none"))
    monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(tmp_path / "none2"))
    sd = tmp_path / "ext" / "pdf"
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text("---\ndescription: PDF things\ncompatibility: Python 3\n---\n# P\n")
    page.load(AppConfig())
    page.extra_skill_dirs_edit.setPlainText(str(tmp_path / "ext"))
    page._refresh_skills_list()
    item = page.skills_list.item(0)
    assert "pdf (external)" in item.text()
    assert "Python 3" in item.toolTip()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_tools_page.py -q -k "extra_skill or external_skill"`
Expected: 3 failed (`AttributeError: 'ToolsPage' object has no attribute 'extra_skill_dirs_edit'`)

- [ ] **Step 3: Implement**

In `tools_page.py`, add `QPlainTextEdit = QtWidgets.QPlainTextEdit` to the alias block. In the Skills group, after `skills_layout.addLayout(skills_btn_layout)`:

```python
        skills_layout.addWidget(QLabel(translate(
            "SettingsDialog",
            "Extra skill folders (one per line), e.g. ~/.claude/skills. "
            "Skills there can run Python inside FreeCAD; only add folders you trust.")))
        extra_row = QHBoxLayout()
        self.extra_skill_dirs_edit = QPlainTextEdit()
        self.extra_skill_dirs_edit.setMaximumHeight(60)
        extra_row.addWidget(self.extra_skill_dirs_edit)
        extra_browse_btn = QPushButton(translate("SettingsDialog", "Browse…"))
        extra_browse_btn.clicked.connect(self._browse_extra_skill_dir)
        extra_row.addWidget(extra_browse_btn, 0, QtCore.Qt.AlignTop)
        skills_layout.addLayout(extra_row)
```

In `_show`, before `self._refresh_skills_list()`:

```python
        self.extra_skill_dirs_edit.setPlainText("\n".join(cfg.extra_skill_dirs))
```

In `_values`, add:

```python
            "extra_skill_dirs": self._extra_dirs_from_widget(),
```

Add methods next to `_refresh_skills_list`:

```python
    def _extra_dirs_from_widget(self):
        return [line.strip() for line in
                self.extra_skill_dirs_edit.toPlainText().splitlines() if line.strip()]

    def _browse_extra_skill_dir(self):
        path = QFileDialog.getExistingDirectory(
            self, translate("SettingsDialog", "Choose a skills folder"))
        if path:
            self.extra_skill_dirs_edit.setPlainText(
                "\n".join(self._extra_dirs_from_widget() + [path]))
            self._refresh_skills_list()
```

In `_refresh_skills_list`, change the status call and the label/tooltip:

```python
        self._skills_status = SkillsRegistry.get_skill_status(
            extra_dirs=self._extra_dirs_from_widget())
```

```python
            elif source == "user":
                icon = "☆"  # ☆
                tag = "user"
            elif source == "external":
                icon = "↗"  # ↗
                tag = "external"
            else:
```

```python
            label = f"{icon} {name} ({tag})"
            if desc:
                label += f" — {desc[:80]}"
            item = QListWidgetItem(label)
            if info.get("compatibility"):
                item.setToolTip(translate("SettingsDialog", "Compatibility: ")
                                + info["compatibility"])
            self.skills_list.addItem(item)
```

(replacing the old `if desc: label += …` and `self.skills_list.addItem(label)` lines).

- [ ] **Step 4: Run the page tests and the settings wiring tests**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_tools_page.py tests/unit/test_prefs_page.py tests/unit/test_settings_dialog_section_wiring.py tests/unit/test_settings_dialog_frame.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/ui/settings_pages/tools_page.py tests/unit/test_tools_page.py
git commit -m "feat(ui): extra skill folders field on the Tools page

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Built-in skills conform; conformance test; skill-creator

**Files:**
- Modify: `skills/enclosure/SKILL.md`, `skills/fastener-hole/SKILL.md`, `skills/gear/SKILL.md`, `skills/lattice/SKILL.md`, `skills/thread-insert/SKILL.md`, `skills/sketch-from-image/SKILL.md`, `skills/skill-creator/SKILL.md`
- Create: `tests/unit/test_skill_conformance.py`

**Interfaces:**
- Consumes: `parse_frontmatter` (Task 1), `SkillsRegistry`, `get_skill_resource` (Task 3).

- [ ] **Step 1: Write the failing conformance test**

```python
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
```

- [ ] **Step 2: Run tests to verify the built-in ones fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_skill_conformance.py -q`
Expected: `test_foreign_skill_links_all_resolve` passes; 6 parametrised failures (enclosure, fastener-hole, gear, lattice, sketch-from-image, thread-insert: `fm.get("name")` is None).

- [ ] **Step 3: Add frontmatter to the built-ins**

Prepend to each file (descriptions are each skill's current first content line; quoted so strict YAML accepts them):

`skills/enclosure/SKILL.md`:
```
---
name: enclosure
description: "Generate a parametric electronics enclosure with a base and lid."
---

```

`skills/fastener-hole/SKILL.md`:
```
---
name: fastener-hole
description: "Create standard fastener holes: clearance, counterbore, or countersink."
---

```

`skills/gear/SKILL.md`:
```
---
name: gear
description: "Create an involute spur gear using FreeCAD's Part module."
---

```

`skills/lattice/SKILL.md`:
```
---
name: lattice
description: "Generate a 3D lattice or infill pattern inside a bounding region. Useful for lightweight structural parts."
---

```

`skills/thread-insert/SKILL.md`:
```
---
name: thread-insert
description: "Create properly sized holes for heat-set threaded inserts (common in 3D printed parts)."
---

```

`skills/sketch-from-image/SKILL.md`: insert `name: sketch-from-image` as the first line inside the existing frontmatter.

- [ ] **Step 4: Update skill-creator to teach the standard layout**

In `skills/skill-creator/SKILL.md`, replace the "Anatomy of a skill" code block with:

````
```
skill-name/
├── SKILL.md          (required — frontmatter `name` + `description`, then instructions)
├── handler.py        (optional, FreeCAD AI only — deterministic execute(args) handler)
├── references/       (optional — docs the model reads on demand, subfolders allowed)
│   ├── dimensions.md
│   └── materials.md
├── scripts/          (optional — Python scripts run with run_skill_script)
└── assets/           (optional — templates/data files the scripts open via __file__)
```

This is the open Agent Skills layout, so the same folder also works in Claude Code,
Codex or Gemini CLI. Start SKILL.md with frontmatter whose `name` equals the folder name:

```
---
name: skill-name
description: "What it does and when to use it, in one or two sentences."
---
```

Quote the description: other harnesses parse it as strict YAML, where an unquoted `: ` breaks it.
Link files with relative Markdown links, e.g. `[thread table](references/thread-tables.md)`;
the model loads them with `use_skill(name=..., resource="references/thread-tables.md")`.
````

and in the "Reference files" section, after the example tree, add:

```
Reference files may live in subfolders (`references/metric/m3.md`). A script in `scripts/` runs inside FreeCAD like a command-line program: it sees `__name__ == "__main__"`, `sys.argv`, and its own `__file__`, so it can open `../assets/...` next to it. Use `sys.exit(1)` to report failure.
```

- [ ] **Step 5: Run the conformance and skills tests**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_skill_conformance.py tests/unit/test_skills.py -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add skills tests/unit/test_skill_conformance.py
git commit -m "feat(skills): built-in skills are valid Agent Skills, enforced by a test

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Integration test in real FreeCAD

**Files:**
- Create: `tests/integration/test_skill_script_integration.py`

**Interfaces:**
- Consumes: `build_script_wrapper` (Task 4); `_sandbox_test(code, timeout=15, document_path=None) -> (safe: bool, error: str)` and `_find_freecad_cmd()` from `freecad_ai/core/executor.py`.

- [ ] **Step 1: Write the test**

```python
"""A skill script runs under real FreeCAD through the runpy wrapper."""

import pytest

from freecad_ai.core.executor import _find_freecad_cmd, _sandbox_test
from freecad_ai.extensions.skill_scripts import build_script_wrapper

pytestmark = pytest.mark.integration

_BOX_SCRIPT = '''\
from __future__ import annotations
import sys
import FreeCAD as App

def main(length: float) -> None:
    doc = App.ActiveDocument
    box = doc.addObject("Part::Box", "SkillBox")
    box.Length = length
    doc.recompute()
    if abs(box.Shape.Volume - length * 100) > 1e-6:
        raise RuntimeError(f"unexpected volume {box.Shape.Volume}")

if __name__ == "__main__":
    main(float(sys.argv[1]))
'''


@pytest.fixture(scope="module")
def freecad_available():
    if not _find_freecad_cmd():
        pytest.skip("No FreeCAD binary available for sandbox tests")


def test_skill_script_creates_a_box(tmp_path, freecad_available):
    script = tmp_path / "scripts" / "box.py"
    script.parent.mkdir()
    script.write_text(_BOX_SCRIPT)
    safe, err = _sandbox_test(build_script_wrapper(str(script), ["20"]), timeout=60)
    assert safe, err


def test_nonzero_exit_fails_in_freecad(tmp_path, freecad_available):
    script = tmp_path / "scripts" / "fail.py"
    script.parent.mkdir()
    script.write_text("import sys\nsys.exit(3)\n")
    safe, err = _sandbox_test(build_script_wrapper(str(script), []), timeout=60)
    assert not safe and "status 3" in err
```

- [ ] **Step 2: Run it**

Run: `env PYTHONPATH= .venv/bin/pytest tests/integration/test_skill_script_integration.py -m integration -q`
Expected: 2 passed. If the AppImage is missing they are skipped; say so in the report rather than claiming they passed.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_skill_script_integration.py
git commit -m "test(skills): a skill script runs under real FreeCAD

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: CHANGELOG and wiki

**Files:**
- Modify: `CHANGELOG.md` (`[Unreleased]`, add `### Added` and `### Changed` above the existing `### Fixed`)
- Modify (separate repo): `../freecad-ai-wiki/Creating-Skills.md`, `../freecad-ai-wiki/Skills-Reference.md`

- [ ] **Step 1: CHANGELOG**

Insert under `## [Unreleased]`, before `### Fixed`:

```markdown
### Added

- **Skills written for other agents now work here (Agent Skills format).**
  A skill folder from Claude Code, Codex, Gemini CLI and others loads
  unchanged: full frontmatter `description` (up to 1024 characters; the
  skill list shows 300), files anywhere in the folder reachable by the
  relative path its `SKILL.md` links to (`references/tables/m3.md`,
  `forms.md`), and Python files in `scripts/` run inside FreeCAD with the
  new `run_skill_script` tool, through the same safety checks as
  `execute_code`. Old `resource='m3'` keys keep working.
- **Extra skill folders** (Settings → Tools → Skills, one path per line;
  `extra_skill_dirs` in `config.json`). Empty by default, so nothing
  changes until you add one. Skills there can run Python inside FreeCAD;
  only add folders you trust.
- The built-in skills are valid Agent Skills, so they also load in other
  harnesses (most useful there together with our MCP server).

### Changed

- **A skill's frontmatter is no longer sent to the model** when it is
  invoked; only the instructions below it are, as in other harnesses. The
  model already sees the name and description in the skill list.
```

- [ ] **Step 2: Wiki**

In `../freecad-ai-wiki/Creating-Skills.md`, replace the folder-layout section with the same tree and frontmatter rules as skill-creator (Task 8 Step 4), and add a "Scripts" section: `run_skill_script(skill, script, args)`, `__main__`/`__file__`/`sys.argv`, `sys.exit(n)` = failure, sibling imports, validation of every `.py` in the skill, Dangerous mode skips it. In `../freecad-ai-wiki/Skills-Reference.md`, add "Using skills from other agents": the `extra_skill_dirs` setting, precedence (built-in < extra < user), the trust warning, and that `allowed-tools` is ignored. Also mention that our skills work in Claude Code alongside the MCP server.

- [ ] **Step 3: Full verification**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit -q --ignore=tests/unit/test_document_attach.py`
Expected: all pass (report the exact count)

- [ ] **Step 4: Commit (both repos; do not push the wiki without the maintainer's go-ahead)**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): Agent Skills compatibility

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git -C ../freecad-ai-wiki add Creating-Skills.md Skills-Reference.md
git -C ../freecad-ai-wiki commit -m "Skills: Agent Skills format, scripts, extra skill folders

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```
