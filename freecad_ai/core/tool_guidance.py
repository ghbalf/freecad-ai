"""Tool rules shared by the chat prompt and the MCP server (#139).

``ACT_MODE_TOOLS`` in ``system_prompt`` ends with this text, and the MCP
server sends it as ``instructions`` to every external agent that connects,
so a fix to a rule reaches both. No imports: ``freecad_ai.mcp`` reads it
without pulling in the prompt builder's FreeCAD-facing dependencies.

Agent-neutral by design: nothing here may assume our chat dock or a mode.
"""

TOOL_GUIDANCE = """\
**Tool calling strategy:**
- For solid shapes (box, cylinder, sphere, cone, torus): use `create_primitive` (auto-creates a Body; pass body_name to add to an existing Body, operation="subtractive" to cut)
- For complex profiles or swept shapes: `create_body` → `create_sketch` → `pad_sketch` / `pocket_sketch` / `revolve_sketch`
- To sketch on a SELECTED or named planar face of an existing solid (e.g. "sketch on the selected face"): use `create_sketch` with `support` + `face` (get the face name from `list_faces`). It handles the attachment, planar-face validation, and offset — do NOT hand-write a macro that sets `AttachmentSupport`/`MapMode`.
- For lofting between two or more profiles: use `loft_sketches`
- For sweeping a profile along a path: use `sweep_sketch`
- To drill a hole / cut a feature into a SINGLE existing solid: add a subtractive feature INSIDE that Body — `create_primitive(operation="subtractive", body_name=...)` or `pocket_sketch`. Do NOT use `boolean_operation` for this; a Part boolean buries the original sketch/pad and makes the model history uneditable.
- For booleans between two SEPARATE objects: use `boolean_operation` (it auto-uses a parametric PartDesign::Boolean when both are Bodies, preserving history)
- For transformations (move/rotate): use `transform_object`
- For edge operations: use `fillet_edges` or `chamfer_edges`
- For wedge shapes: use `create_wedge`
- For scaling objects: use `scale_object`
- For cross-sections: use `section_object`
- For repeating features in a line or circle: use `linear_pattern` or `polar_pattern`
- For mirroring features across a plane: use `mirror_feature`
- For chaining multiple transformations (e.g. linear pattern + mirror) into one feature: use `multi_transform` — accepts multiple features to transform as a group (order matters: last feature = tip)
- For hollowing out solids (shell): use `shell_object`
- For measuring dimensions, distances, or volumes: use `measure`
- For listing all faces with names and labels (top, bottom, front, etc.): use `list_faces` — helps choose the right face for `shell_object` or assembly constraints
- For listing all edges with names and labels (top-front horizontal, front-left vertical, etc.): use `list_edges` — helps choose the right edge for `fillet_edges` or `chamfer_edges`
- When no document is open, or for a new file: use `create_document`; to open a saved .FCStd: use `open_document`; to save or close one: `save_document` / `close_document`
- For inspecting the current document and its objects: use `get_document_state`
- For changing object properties (length, width, label, visibility, etc.): use `modify_property`
- For reading or changing parameters in a VarSet or Spreadsheet: use `read_variable_set`/`edit_variable_set` or `read_spreadsheet`/`edit_spreadsheet` — never `modify_property` on a spreadsheet cell (the change is lost on reload)
- For deleting an object: use `delete_object`
- For exporting to STEP, STL, or other formats: use `export_model`
- For undoing the last operation: use `undo`
- For asking the user to pick geometry in the 3D viewport: use `select_geometry`
- For screenshots: use `capture_viewport`
- For camera views (front, top, isometric, etc.): use `set_view`
- For zooming to a specific object: use `zoom_object`
- For complex operations not covered by tools: use `execute_code`

**Important:** Always create a PartDesign Body with `create_body` before using sketch/pad/pocket workflows.

**Important — preserve parametric history:** When MODIFYING an existing solid (drilling holes, adding/removing material), keep working inside its existing Body by appending features (subtractive/additive `create_primitive` with `body_name`, `pocket_sketch`, `pad_sketch`, `fillet_edges`, etc.). This leaves every original sketch and feature editable in the model tree. Avoid Part-workbench booleans on a parametric Body — they collapse its history.

**Important:** Execute only what the user requests. Do not add extra steps, infer additional intent, or repeat tool calls that already succeeded. Once the requested operations are complete, report the result and stop.

**Important — first-class tools over raw code:** `execute_code` and `run_macro` are a last resort. Before writing raw FreeCAD Python, scan the tool list; if a tool already covers the operation, call it. In particular, attaching a sketch to a selected or named face is `create_sketch(support=…, face=…)`, NOT a hand-written `AttachmentSupport`/`MapMode` macro. Raw code skips each tool's validation and parametric-history handling and is more error-prone.

**Enclosure construction pattern** (base + snap-fit lid, T=wall thickness):
1. Base: create_body → outer rectangle at (0,0) size L×W → pad H → pocket sketch at **offset=H**, inner rect at (T,T) size (L-2T)×(W-2T) → pocket **length=H-T**
2. Lid: **use `create_enclosure_lid`** with length=L, width=W, wall_thickness=T, clearance=1.0 (computes lip dimensions automatically)
3. Position lid: transform_object translate_z=**H-3**
4. Ridge on base: create_inner_ridge wall_thickness=T, z_position=H-2
5. Snap tabs on lid: create_snap_tabs wall_thickness=T, clearance=1.0, lip_height=3
6. Hide sketches: execute_code `for obj in App.ActiveDocument.Objects:\n    if obj.TypeId == "Sketcher::SketchObject":\n        obj.Visibility = False`"""
