# Skill Creator

Create new FreeCAD AI skills from the chat interface.

## When invoked

Guide the user through creating a new skill by asking questions, then generate the files and save them.

## Step 1: Gather requirements

Ask the user (skip questions they already answered in their invocation):

1. **What should the skill do?** — e.g. "generate a mounting bracket", "create a gear train"
2. **What parameters should the user provide?** — dimensions, counts, materials, etc.
3. **What's the construction approach?** — which FreeCAD operations, workflow steps
4. **Should it have a Python handler?** — for skills that need deterministic logic (calculations, lookups, file generation) rather than just LLM instructions

## Step 2: Choose a name

Pick a short, hyphenated name based on what the skill does. Confirm with the user.
The skill will live at: `~/.config/FreeCAD/FreeCADAI/skills/<name>/`

## Step 3: Write the SKILL.md

Write clear, specific instructions that will be injected into the LLM's prompt when the skill is invoked. A good SKILL.md includes:

- **Title and one-line description**
- **Parameters** the user should provide (with sensible defaults)
- **Step-by-step construction instructions** using FreeCAD concepts (PartDesign bodies, sketches, pads, pockets, booleans, etc.)
- **Important notes** — gotchas, tolerances, material considerations
- **Reference data** — standard dimensions, lookup tables (bolt sizes, thread pitches, etc.)

Keep instructions practical and specific to FreeCAD. Avoid vague language — tell the LLM exactly what operations to perform and in what order.

## Step 4: Write handler.py (optional)

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

## Step 5: Save the files

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

## Tips for writing good skills

- **Be specific about FreeCAD operations** — name the exact workbench, feature type, and property names
- **Include default values** — so the user can invoke with minimal arguments
- **Add dimensional reference tables** — standard sizes (M3 screws, bearing bores, etc.) save the user from looking things up
- **Warn about FreeCAD pitfalls** — coplanar booleans, unclosed sketches, Revolution crashes
- **Keep it under 200 lines** — long skills dilute the LLM's attention
