# Shorter Act prompt (#159): design

**Why:** many lines in `TOOL_GUIDANCE` only map a task to a tool its own description already covers. Removing them saves about 500 tokens per request, and stops the text naming tools that reranking left out.

**Approaches**
- **A (chosen by the maintainer):** remove pure mapping lines and duplicates; everything kept stays word for word.
- B: also rewrite the rules tersely. Rejected: changes rules that fix known failures.
- C: move the enclosure recipe to the skill. Rejected: only helps models that load the skill.

**Kept verbatim:** the four **Important** rules; the bullets for `create_primitive`, the sketch workflow, sketch on a selected face, cutting a single solid, booleans, VarSet/Spreadsheet, `export_model`, `modify_property` (only place naming label and visibility; found in review); the enclosure pattern; "call multiple tools", "`get_document_state` before making changes" and "explain what was done".

**Removed:** 22 mapping bullets in `TOOL_GUIDANCE` and four lines in `ACT_MODE_TOOLS` ("Use the available tools", `measure`, `select_geometry`, `execute_code` fallback).

**Goal:** tool choice no worse on a small local model. **Non-goals:** tool schemas, Plan mode (own issue), saved custom prompts (left as they are).

**Constraint:** the MCP instructions share this text and change with it (#144 order stays). One prompt-cache miss per user.
