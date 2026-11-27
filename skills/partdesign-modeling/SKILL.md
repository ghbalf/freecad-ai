---
name: partdesign-modeling
description: "Model like a CAD professional: meaningful origin, named parameters in a VarSet, fully constrained sketches."
---

# PartDesign Modeling Practice

Contributed by s-light in #163. Use these rules when the user asks for a parametric, editable or "proper" model.

Model the way a seasoned CAD engineer would, not just whatever gets a solid to appear:

- **Meaningful origin:** place each model's local origin (0,0,0) at a physically meaningful reference point of the real object — the point it will actually be positioned/mounted/attached by (e.g. a light fixture's hanging/clamp point, not an arbitrary bounding-box corner). Ask "where will this be placed from?" before sketching.
- **Master-sketch / named parameters:** don't repeat raw numbers across sketches. Hold the model's key dimensions in one place — an `App::VarSet` or spreadsheet — and bind sketch constraints and feature properties (extrusion length, Radius, ...) to it via expressions (`obj.setExpression('Property', 'Variables.foo')`, or `sketch.setExpression('Constraints[i]', 'Variables.foo')` for a specific constraint). Changing one variable should update the whole model.
- **Fully constrain sketches:** every profile sketch should end with `sketch.FullyConstrained == True`. Add Horizontal/Vertical constraints on axis-aligned edges, Coincident constraints to close the wire, and Distance/DistanceX/DistanceY/Symmetric constraints (bound to named parameters where possible) to pin the remaining geometry — not just enough geometry for the profile to close and extrude.
- **Prefer Equal/Symmetric constraints over repeating the same dimension twice.** If two edges are meant to have the same length, or two features are meant to mirror each other about a centerline, express that relationship directly with an `Equal` or `Symmetric` constraint instead of pinning both with identical (or negated) `DistanceX`/`DistanceY` values — a duplicated hardcoded number can drift out of sync on a later edit, a constraint that states the relationship cannot. Example: a bracket's left/right outer edges tied with one `Symmetric` constraint about the centerline plus an `Equal` constraint on their lengths, rather than two independent `DistanceX` pins at `+width/2` and `-width/2`.
- Favor an extrude/pocket feature's `Midplane=True` option when a feature is naturally symmetric about a meaningful plane (e.g. centered depth) instead of hand-computing an offset.