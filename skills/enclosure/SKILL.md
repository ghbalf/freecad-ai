# Enclosure Generator

Generate a parametric electronics enclosure with a base and lid.

## Parameters to extract from user request
- **L, W, H**: outer length, width, height in mm
- **T**: wall thickness (default 2mm)
- **Post radius**: screw post outer radius (default 3mm)
- **Screw size**: M3 by default (hole r=1.5mm, clearance r=1.75mm)

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

### 4. Screw posts (INSIDE the corners)
Post centers must be far enough from walls so they don't protrude:
- Center X positions: T+PR (left), L-T-PR (right)
- Center Y positions: T+PR (front), W-T-PR (back)
- `create_sketch` on XY, body_name="EnclosureBase": 4 circles at those positions, radius=PR
- `pad_sketch` length=**H-T**, body_name="EnclosureBase"

### 5. Screw holes in posts
- `create_sketch` on XY, body_name="EnclosureBase": 4 circles at same centers, radius=1.5 (M3)
- `pocket_sketch` through_all=true, body_name="EnclosureBase"

### 6. Lid body
- `create_body` label="EnclosureLid"
- `create_sketch` on XY, body_name="EnclosureLid": rectangle x=0, y=0, width=L, height=W
- `pad_sketch` length=**T**, body_name="EnclosureLid"

### 7. Lid screw holes (M3 clearance)
- `create_sketch` on XY, body_name="EnclosureLid": 4 circles at same post centers, radius=1.75
- `pocket_sketch` through_all=true, body_name="EnclosureLid"

### 8. Position lid
- `transform_object` EnclosureLid, translate_z=**H** (sits on top of base)

### 9. Hide sketches
After construction, hide all sketches for a clean viewport:
- `modify_property` object_name="OuterShell", property_name="Visibility", value=false
- `modify_property` object_name="InteriorPocket", property_name="Visibility", value=false
- `modify_property` object_name="ScrewPosts", property_name="Visibility", value=false
- `modify_property` object_name="PostHoles", property_name="Visibility", value=false
- `modify_property` object_name="LidBase", property_name="Visibility", value=false
- `modify_property` object_name="LidHoles", property_name="Visibility", value=false

Alternatively, use `execute_code` to hide all sketches at once:
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
