# FEM analysis with CalculiX (#113)

**Goal:** a `run_fem_analysis` tool (chat and MCP) that solves an existing
FEM analysis with CalculiX in a separate FreeCAD process, returns a short
stress/displacement summary for the LLM, and imports the results into the
open document so the user can colour-plot them.
**Depends on:** #114 (`run_headless`, `run_on_main`, `main_thread=False`),
PR #117, not yet merged. This branch (`feat/113-fem-analysis`) starts from
`feat/114-headless-exec`; its PR targets `master` after #117 is merged.

## Decisions (maintainer, 2026-10-03)

1. **Summary + imported results.** The solve runs headless on a copy of the
   document. On success the `.frd` results are imported into the open
   document's analysis. The text summary goes to the LLM.
2. **Remesh in the child.** A Gmsh mesh object in the analysis is
   regenerated with its own settings before every solve, so a mesh made
   before a geometry edit can never give stale results. With no mesh, a
   Gmsh mesh is created. A Netgen mesh is used as it is (Netgen is not in
   the AppImage).
3. **Approach A: a dedicated tool on the headless runner.** Rejected: B (a
   skill telling the LLM to write the solve script for
   `execute_code_headless`; local models do not follow multi-step
   instructions reliably) and C (FreeCAD's `femsolver.run` task machine
   in-process; GUI-oriented and bypasses #114).
4. **Re-runs replace results.** Before importing, the analysis's existing
   result objects and post-processing pipelines are removed, the same
   purge FreeCAD's own solver performs.
5. **Static analyses are summarised.** Other analysis types (frequency,
   thermal) still solve and import, but the summary gives only mesh and
   solver data with a note.

## Probe findings (FreeCAD 1.1.1 AppImage, console mode)

- `ccx` and `gmsh` ship in the AppImage (`usr/bin`). FreeCAD finds them
  with `femsolver.settings.get_binary("Calculix")` (respects the FEM
  preference for a custom path) and `GmshTools.get_gmsh_command()`.
- The full pipeline runs in a console process with no GUI: `ObjectsFem`
  makers, `femmesh.gmshtools.GmshTools(mesh).create_mesh()`, then
  `femtools.ccxtools.FemToolsCcx(analysis, solver)` with
  `update_objects`, `setup_working_dir`, `check_prerequisites`,
  `purge_results`, `write_inp_file`, `ccx_run`, `load_results`.
  A 100×10×10 mm steel cantilever (1000 N, 5 mm mesh) meshed in 0.97 s
  (198 nodes, 499 tetrahedra) and solved in 0.38 s.
- The result object (`Fem::FemResultObjectPython`) carries per-node
  `vonMises`, `DisplacementLengths`, `NodeNumbers` and more.
  `fea.ccx_stdout` holds the solver output.
- `feminout.importCcxFrdResults.importFrd(frd, analysis, "CCX_")` imports
  into an analysis that has no mesh: it creates `CCX_Results`,
  `CCX_Results_Mesh` (its own mesh) and `Pipeline_CCX_Results`, with
  identical values.
- **Coarse meshes are far too stiff.** Beam theory gives 600 MPa and
  1.9 mm for that cantilever; the 5 mm mesh gave 325 MPa and 1.17 mm. The
  summary must show the mesh size and node count and say that peak stress
  depends on the mesh.

## Design

### Tool

`run_fem_analysis(analysis="", mesh_size="", timeout=600)`, category
`general`, `main_thread=False`, available in chat and over MCP.

- `analysis`: object name or label. Empty means the only analysis in the
  active document.
- `mesh_size`: a length such as `"3 mm"`. It is used only when a Gmsh mesh
  is created because the analysis has none, or to override
  `CharacteristicLengthMax` of an existing Gmsh mesh. Empty means: keep an
  existing Gmsh mesh's settings; for a new mesh, 1/20 of the largest
  bounding-box dimension of the meshed shape.
- `timeout`: seconds, as in `execute_code_headless`.

The description tells the LLM: set up material, constraints and loads with
`execute_code` first; results are a mesh-dependent estimate, so refine
`mesh_size` to check convergence; the model is not changed, only result
objects are added.

### Units

- **`freecad_ai/core/fem.py`** (no Qt):
  - `build_solve_code(analysis_name: str, mesh_size: str) -> str`: the
    child's code. Values are embedded with `repr`.
  - `format_summary(summary: dict) -> str`: the text the LLM sees.
  - `import_results(analysis, frd_path: str) -> None`: purges result
    objects and pipelines from the analysis, then `importFrd(frd_path,
    analysis, "CCX_")`.
- **`freecad_ai/tools/fem_tools.py`**: `_handle_run_fem_analysis` and
  `RUN_FEM_ANALYSIS`, registered in `tools/setup.py` next to `ALL_TOOLS`.
- The "find active document and save a copy into the run folder" step of
  `execute_code_headless` moves into a shared helper used by both tools.

### Flow

1. **On the GUI thread** (`run_on_main`): find the active document and the
   analysis (`Fem::FemAnalysis`), resolve it by name or label, save a copy
   to `<run>/input.FCStd`.
2. **Child** (`run_headless(build_solve_code(...), ...)`; our own code, so
   `_validate_code` is not applied). In the copy:
   - find the analysis by name;
   - if no CalculiX solver is in it, add `makeSolverCalculiXCcxTools`
     (`summary.solver_created = True`);
   - mesh: a Gmsh mesh is regenerated (with `mesh_size` if given); with no
     mesh object, a Gmsh mesh on the analysis's single solid is created
     (several or no candidate solids is an error naming them); a Netgen
     or other mesh is used as it is; an empty mesh after this is an error;
   - `FemToolsCcx`: `update_objects`, `setup_working_dir(<run>/ccx)`,
     `check_prerequisites` (a non-empty message is returned as the error),
     `purge_results`, `write_inp_file`, `ccx_run`, `load_results`;
   - write `<run>/summary.json`.
3. **On the GUI thread** after success: `import_results`, then
   `doc.recompute()`.

`summary.json`:

```json
{"analysis": "Analysis", "solver_created": false,
 "mesher": "gmsh", "mesh_size": "5 mm", "nodes": 198, "elements": 499,
 "max_von_mises": 324.9, "max_von_mises_at": [100.0, 0.0, 10.0],
 "max_displacement": 1.168, "frd": "<run>/ccx/Mesh.frd",
 "ccx_warnings": ["*WARNING ..."], "elapsed_s": 1.4}
```

Stress in MPa, displacement in mm (FreeCAD's result units). For a
non-static result the stress/displacement keys are absent and
`analysis_type` says what it was.

### Result

`success=True` text, for example:

```
FEM analysis 'Analysis' solved with CalculiX in 1.4 s.
Mesh: gmsh, 5 mm, 198 nodes, 499 elements.
Max von Mises stress: 324.9 MPa at (100.0, 0.0, 10.0) mm
Max displacement: 1.168 mm
Results imported as CCX_Results (colour plot via Pipeline_CCX_Results).
Note: a coarse mesh underestimates peak stress. Re-run with a smaller
mesh_size and compare to check convergence.
```

plus `Solver created: SolverCcxTools` when one was added, and up to 20
CalculiX warnings. `data` = `{"run_dir", "summary"}`.

## Error handling

| Situation | Result |
|---|---|
| No active document | `success=False`, `No active document` |
| No analysis / several and none named / name not found | `success=False`, lists the analyses |
| `ccx` not found | `success=False`, `CalculiX (ccx) not found. Install it or set its path in Preferences → FEM → CalculiX` |
| `gmsh` not found and a Gmsh mesh is needed | `success=False`, says so; suggests creating a Netgen mesh in FreeCAD |
| No or several solids to mesh | `success=False`, names the candidates |
| Meshing fails or gives an empty mesh | `success=False`, gmsh's error text |
| `check_prerequisites` fails | `success=False`, its message (e.g. no material, no constraint) |
| CalculiX fails (no results) | `success=False`, last 20 lines of the ccx output |
| Timeout / Stop / crash / no result file | As in #114 |
| Importing results fails | `success=True`, the summary plus `Results not imported: <e>` and the `.frd` path |

## Testing

**Unit:**
- `build_solve_code`: compiles; an analysis name with quotes is embedded
  safely.
- `format_summary`: static result (MPa, mm, mesh counts, note); a summary
  without stress keys (non-static) gives mesh data and the note; warnings
  capped at 20.
- Handler with `run_headless` and GUI steps faked: analysis selection
  (named, label, only one, ambiguous, none, no document); success imports;
  each error row; failed import still `success=True`; registered with
  `main_thread=False`; listed over MCP.
- `import_results` with a fake analysis: old results and pipelines are
  removed, then the import runs.

**Integration (`-m integration`, real AppImage):**
- Cantilever (box, material, fixed face, force, no mesh): non-zero stress
  and displacement, nodes > 0, `.frd` exists, importing it into a fresh
  document gives `CCX_Results`.
- Same without material: the `check_prerequisites` message comes back.

**Live (Xvfb GUI over MCP):** run on the cantilever; the result objects
appear in the open analysis; a second run replaces them instead of adding a
second set; `get_document_state` answers during the solve.

## Docs

- CHANGELOG `Added` entry.
- Wiki `Tool-Reference.md` section (local until the release).

## Out of scope

- Tools for setting up analyses (material, constraints, loads, mesh).
- Solvers other than CalculiX (Elmer, Z88, Mystran).
- Summaries for frequency and thermal analyses.
- Bringing the regenerated mesh back into the analysis's own mesh object
  (the imported results carry their own mesh).
- macOS and Windows: `get_binary` covers them, but the paths ship untested.
