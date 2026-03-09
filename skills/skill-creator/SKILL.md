---
name: skill-creator
description: Create new FreeCAD AI skills, modify existing skills, and iteratively improve them. Use when users want to create a skill from scratch, update or optimize an existing skill, capture a workflow as a reusable skill, or improve an existing skill's instructions. Also trigger when the user says "turn this into a skill", "make a skill for X", "save this as a command", or similar.
---

# Skill Creator

A skill for creating new FreeCAD AI skills and iteratively improving them.

At a high level, the process goes like this:

- Understand what the user wants the skill to do
- Interview for details — parameters, edge cases, construction approach
- Write a draft of the skill (SKILL.md + optional handler.py)
- Test it by running the `/command` and evaluating the result
- Improve based on what worked and what didn't
- Repeat until the user is satisfied

Your job is to figure out where the user is in this process and help them move forward. Maybe they say "I want a skill for X" — help them scope it, write the draft, and test it. Or maybe they already have a skill that needs fixing — jump straight to the improvement loop.

Be flexible. If the user says "just write it, I'll test it myself", do that. If they want to iterate 5 times, do that too.

## Communicating with the user

FreeCAD AI users range from experienced CAD engineers to hobbyists who just discovered parametric modeling. Pay attention to context cues — if they use terms like "involute" and "datum plane", match that level. If they say "I want to make a box thing with holes", keep things simple.

---

## Creating a skill

### Step 1: Capture intent

Start by understanding what the user wants. The current conversation may already contain a workflow worth capturing (e.g., they say "turn this into a skill"). If so, extract what you can from the conversation — the tools used, the sequence of steps, corrections the user made, dimensions and parameters observed. The user may need to fill gaps, and should confirm before you proceed.

Ask (skip questions they already answered):

1. **What should the skill do?** — e.g., "generate a mounting bracket", "create a gear train"
2. **What parameters should the user provide?** — dimensions, counts, materials, tolerances
3. **When should someone use this?** — what would they type to invoke it?
4. **What's the construction approach?** — which FreeCAD operations, in what order?
5. **Are there edge cases?** — minimum wall thickness, maximum overhang angle, material constraints
6. **Should it have a Python handler?** — for skills that need deterministic logic (calculations, lookups) rather than just LLM instructions

### Step 2: Interview and research

Proactively ask about things the user might not think of:

- Standard dimensions — are there industry standards to reference? (bolt sizes, bearing bores, thread pitches)
- FreeCAD pitfalls — coplanar boolean failures, unclosed sketches, Revolution crashes with full-circle profiles
- Parameter validation — what ranges are reasonable? What breaks?
- Construction order — does the workflow depend on features being created in a specific sequence?

If the current document has relevant objects, inspect them with `get_document_state` and `measure` to understand the context.

### Step 3: Choose a name

Pick a short, hyphenated name based on what the skill does. Confirm with the user.
The skill will live at: `~/.config/FreeCAD/FreeCADAI/skills/<name>/`
The user invokes it with `/<name>`.

### Step 4: Write the SKILL.md

#### Anatomy of a skill

```
skill-name/
├── SKILL.md          (required — instructions injected into LLM prompt)
├── handler.py        (optional — Python handler with execute(args) function)
└── references/       (optional — additional docs loaded as needed)
    ├── dimensions.md
    └── materials.md
```

#### Progressive disclosure

Skills use a layered loading system:

1. **Name + first line** — always visible in the skills list (~10 words)
2. **SKILL.md body** — loaded when the skill is invoked (<200 lines ideal)
3. **References** — loaded on demand when the skill tells the LLM to read them

Keep SKILL.md under 200 lines. If you need more detail (dimension tables, material properties, multi-variant instructions), put it in `references/` and point to it from SKILL.md:

```markdown
For standard metric thread dimensions, read `references/thread-tables.md`.
```

#### SKILL.md structure

A good SKILL.md includes:

- **Title and one-line description**
- **Parameters** the user should provide (with sensible defaults)
- **Step-by-step construction instructions** using the tool calling system
- **Important notes** — gotchas, tolerances, material considerations
- **Reference data** — standard dimensions, lookup tables (or pointers to reference files)

#### Writing style

Explain the *why* behind instructions, not just the *what*. The LLM is smart — if it understands the reasoning, it can adapt to situations the instructions don't cover explicitly.

Instead of:
```markdown
ALWAYS use offset=H on the pocket sketch. NEVER pocket from z=0.
```

Write:
```markdown
Place the pocket sketch at offset=H (top face of the solid). Pocketing from z=0
creates a hollow that opens upward with no floor — the pocket cuts from the sketch
plane downward into the solid, so starting from the top gives you a proper floor
at the bottom.
```

More guidance:
- **Be specific about FreeCAD operations** — name the exact tool, feature type, and property names
- **Include default values** — so the user can invoke with minimal arguments
- **Use the tool names** — `create_sketch`, `pad_sketch`, `pocket_sketch`, etc. The LLM knows all 33 tools
- **Warn about pitfalls** — but explain why they're pitfalls, not just "don't do this"

### Step 5: Write handler.py (optional)

If the skill benefits from a Python handler, write one with an `execute(args)` function:

```python
def execute(args):
    """
    Args:
        args: string with the user's arguments after the /command

    Returns:
        dict with one of:
          {"inject_prompt": "text"} — inject into LLM prompt
          {"output": "text"} — display directly to user
          {"error": "text"} — show error
    """
```

Use a handler when the skill needs:
- Calculations (gear tooth profiles, thread geometry, stress analysis)
- Lookup tables that are easier in Python than in prose
- File I/O (reading templates, writing config)
- Validation of user parameters before sending to the LLM

### Step 6: Save the files

Use the `execute_code` tool to create the skill directory and write the files:

```python
import os
skill_dir = os.path.expanduser("~/.config/FreeCAD/FreeCADAI/skills/<name>")
os.makedirs(skill_dir, exist_ok=True)

with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
    f.write(skill_md_content)

# Optional:
with open(os.path.join(skill_dir, "handler.py"), "w") as f:
    f.write(handler_content)
```

Tell the user the skill is ready and they can invoke it with `/<name>`.

---

## Testing and improving

After writing the draft, test it. Come up with 2–3 realistic invocations — the kind of thing a real user would type:

```
/bracket 80x40mm, 4 mounting holes M4, 3mm thick aluminum
/bracket 30x20mm, 2 holes M3
/bracket — just use defaults
```

Share them with the user: "Here are a few test cases I'd like to try. Do these look right, or would you change any?"

Then run them one at a time. After each run:
- Check the result with `get_document_state` and `measure`
- Note what worked and what didn't
- Ask the user for feedback

### How to think about improvements

1. **Generalize from the feedback.** The skill will be used many times with different parameters. Don't overfit to the test cases — if a fix only works for one specific set of dimensions, it's probably too narrow. Think about what principle the fix represents and express that in the instructions.

2. **Keep the prompt lean.** Remove instructions that aren't pulling their weight. If the LLM is spending time on unnecessary steps, cut them. Read the actual tool call sequence to see where time is wasted.

3. **Explain the why.** If you find yourself writing ALWAYS or NEVER in all caps, that's a sign the instruction needs a reason, not more emphasis. Explain why the thing matters and the LLM will follow through more reliably.

4. **Look for repeated patterns.** If every test run independently arrives at the same multi-step workaround, that's a signal the skill should include that approach explicitly — or bundle it in a handler.

### The iteration loop

1. Improve the skill based on feedback
2. Re-run the test cases
3. Check results, ask user for feedback
4. Repeat until the user is happy or improvements plateau

---

## Improving an existing skill

If the user already has a skill they want to improve:

1. Read the current SKILL.md
2. Ask what's not working — specific failures, edge cases, quality issues
3. Run it on a few test cases to reproduce the problems
4. Apply improvements following the same principles above
5. Re-test and iterate

---

## Reference files

For skills that need reference data (dimension tables, material properties, standard specifications), create a `references/` directory alongside SKILL.md. Keep each reference file focused on one topic and under 300 lines. Include a brief table of contents at the top of long files.

Example structure for a fastener skill:
```
fastener/
├── SKILL.md
└── references/
    ├── metric-bolts.md      # M2–M24 dimensions
    ├── imperial-bolts.md    # #2–1" dimensions
    └── materials.md         # Strength grades, torque specs
```
