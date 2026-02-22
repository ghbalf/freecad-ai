# Enclosure Generator

Generate a parametric electronics enclosure with a base and lid.

## Parameters to extract from user request
- **L, W, H**: outer length, width, height in mm
- **T**: wall thickness (default 2mm)
- **Lid type**: "screw" (default), "press-fit" (lip only), or "snap-fit" (lip + ridge + snap tabs)
- **Post radius**: screw post outer radius (default 3mm) — only for screw lid
- **Screw size**: M3 by default (hole r=1.5mm, clearance r=1.75mm) — only for screw lid

## Construction steps (use these exact dimensions)

Variables: L=length, W=width, H=height, T=wall_thickness, PR=post_radius (3mm)

### 1. Base body
- `create_body` label="EnclosureBase"

### 2. Outer shell
- `create_sketch` on XY, body_name="EnclosureBase": rectangle x=0, y=0, width=L, height=W
- `pad_sketch` length=**H**, body_name="EnclosureBase"

### 3. Interior pocket (from the TOP face)
- `create_sketch` on XY, **offset=H**, body_name="EnclosureBase": rectangle x=T, y=T, width=L-2*T, height=W-2*T
- `pocket_sketch` length=**H-T**, body_name="EnclosureBase"
- This creates the hollow from z=T to z=H (floor thickness=T at bottom, open at top)

### 4–5. Screw posts and holes (SCREW LID ONLY — skip for snap-fit)
Post centers must be far enough from walls so they don't protrude:
- Center X positions: T+PR (left), L-T-PR (right)
- Center Y positions: T+PR (front), W-T-PR (back)

**Step 4: Screw posts**
- `create_sketch` on XY, body_name="EnclosureBase": 4 circles at those positions, radius=PR
- `pad_sketch` length=**H-T**, body_name="EnclosureBase"

**Step 5: Screw holes in posts**
- `create_sketch` on XY, body_name="EnclosureBase": 4 circles at same centers, radius=1.5 (M3)
- `pocket_sketch` through_all=true, body_name="EnclosureBase"

### 6. Lid body

**For SCREW lid:**
- `create_body` label="EnclosureLid"
- `create_sketch` on XY, body_name="EnclosureLid": rectangle x=0, y=0, width=L, height=W
- `pad_sketch` length=**T**, body_name="EnclosureLid"

**For PRESS-FIT or SNAP-FIT lid** (build lip first, then slab on top so lip points downward):
- `create_body` label="EnclosureLid"
- `create_sketch` on XY, body_name="EnclosureLid": rectangle x=T+0.2, y=T+0.2, width=L-2*T-0.4, height=W-2*T-0.4
- `pad_sketch` length=**3** (3mm lip), body_name="EnclosureLid"
- `create_sketch` on XY, **offset=3**, body_name="EnclosureLid": rectangle x=0, y=0, width=L, height=W
- `pad_sketch` length=**T**, body_name="EnclosureLid"
- The 0.2mm gap on each side provides clearance
- Lip: z=0→3, Slab: z=3→3+T. After positioning, the lip hangs into the base.

### 7. Lid holes (SCREW LID ONLY — skip for press-fit and snap-fit)
- `create_sketch` on XY, body_name="EnclosureLid": 4 circles at same post centers, radius=1.75
- `pocket_sketch` through_all=true, body_name="EnclosureLid"

### 8. Position lid (BEFORE snap tabs so the shape is in the right place)
- **Screw lid**: `transform_object` EnclosureLid, translate_z=**H**
- **Press-fit lid**: `transform_object` EnclosureLid, translate_z=**H-3**
- **Snap-fit lid**: `transform_object` EnclosureLid, translate_z=**H-3**

### 9. Snap-fit ridge and tabs (SNAP-FIT ONLY — skip for screw and press-fit)
Add a ridge on the base interior and snap tabs on the lid lip.
The snap tabs tool copies the lid's shape (including its position), so the lid MUST be positioned first in step 8.
- `create_inner_ridge` body_name="EnclosureBase", length=L, width=W, wall_thickness=T, ridge_width=0.8, ridge_height=0.5, z_position=**H-2**
- `create_snap_tabs` body_name="EnclosureLid", length=L, width=W, wall_thickness=T, clearance=0.2, lip_height=3

### 10. Hide sketches
Use `execute_code` to hide all sketches at once:
```python
for obj in App.ActiveDocument.Objects:
    if obj.TypeId == "Sketcher::SketchObject":
        obj.Visibility = False
```

## Critical rules
- ALWAYS pass explicit `length` to pad_sketch — never rely on the 10mm default
- ALWAYS pass `body_name` to pad_sketch and pocket_sketch — this ensures features go into the correct body
- Screw posts go INSIDE the enclosure at positions (T+PR, T+PR) from each corner — never at the wall edge
- All sketches on XY plane, attached to the correct body_name
- The base height is H, pocket depth is H-T, post height is H-T
- Use **offset=H** for the pocket sketch so it is placed at the top face (creates correct floor-at-bottom orientation)
- **Screw lid** (default): do steps 4, 5, 7. Skip step 8. Use screw lid variant in step 6.
- **Press-fit lid**: skip steps 4, 5, 7, 8. Use press-fit/snap-fit lid variant in step 6 (lip only, no tabs).
- **Snap-fit lid**: skip steps 4, 5, 7. Use press-fit/snap-fit lid variant in step 6, then do step 8 (ridge + tabs).
