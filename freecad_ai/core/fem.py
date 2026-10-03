"""run_fem_analysis building blocks (#113).

The solve runs in a headless FreeCAD child (core.headless, #114) on a copy of
the document; the GUI-thread steps find the analysis, save the copy and
import the results afterwards.
"""

# Runs in the child after "ANALYSIS = ...\nMESH_SIZE = ...\n". The headless
# harness provides App and WORK_DIR. Known problems end in {"error": msg};
# anything unexpected raises and reaches the tool as the child's traceback.
_SOLVE_CODE = r'''
import glob
import json
import os
import time

import ObjectsFem


def _fail(msg):
    return {"error": msg}


def _solve():
    t0 = time.time()
    doc = App.ActiveDocument
    analysis = doc.getObject(ANALYSIS) if doc else None
    if analysis is None:
        return _fail("Analysis {!r} not found in the document copy".format(ANALYSIS))
    summary = {"analysis": analysis.Label, "solver_created": False}

    solvers = [o for o in analysis.Group
               if getattr(getattr(o, "Proxy", None), "Type", "") == "Fem::SolverCcxTools"]
    if solvers:
        solver = solvers[0]
    else:
        solver = ObjectsFem.makeSolverCalculiXCcxTools(doc)
        analysis.addObject(solver)
        summary["solver_created"] = True
    summary["analysis_type"] = getattr(solver, "AnalysisType", "static")

    meshes = [o for o in analysis.Group if o.isDerivedFrom("Fem::FemMeshObject")
              and getattr(getattr(o, "Proxy", None), "Type", "") != "Fem::MeshResult"]
    if len(meshes) > 1:
        return _fail("The analysis has several meshes: {}. Keep one.".format(
            ", ".join(m.Label for m in meshes)))
    mesh = meshes[0] if meshes else None
    is_gmsh = mesh is None or getattr(getattr(mesh, "Proxy", None), "Type", "") == "Fem::FemMeshGmsh"
    if mesh is None:
        parts = []
        for o in analysis.Group:
            for ref in getattr(o, "References", None) or []:
                obj = ref[0]
                if obj not in parts and getattr(obj, "Shape", None) is not None \
                        and obj.Shape.Solids:
                    parts.append(obj)
        if len(parts) != 1:
            names = ", ".join(p.Label for p in parts) or "none"
            return _fail("Cannot pick the part to mesh: the constraints reference "
                         "{} solid object(s) ({}). Add a mesh to the analysis.".format(
                             len(parts), names))
        mesh = ObjectsFem.makeMeshGmsh(doc, "FEMMeshGmsh")
        mesh.Shape = parts[0]
        analysis.addObject(mesh)
        if not MESH_SIZE:
            bb = parts[0].Shape.BoundBox
            mesh.CharacteristicLengthMax = "{:.3g} mm".format(
                max(bb.XLength, bb.YLength, bb.ZLength) / 20.0)
    if is_gmsh:
        if MESH_SIZE:
            mesh.CharacteristicLengthMax = MESH_SIZE
        from femmesh.gmshtools import GmshTools
        try:
            err = GmshTools(mesh).create_mesh()
        except Exception as e:
            if type(e).__name__ == "GmshError":
                return _fail("Gmsh not found ({}). Install it, set its path in "
                             "Preferences → FEM → Gmsh, or add a Netgen mesh to "
                             "the analysis in FreeCAD.".format(str(e).strip()))
            return _fail("Meshing failed: {}".format(str(e).strip()))
        if err:
            return _fail("Meshing failed: {}".format(str(err).strip()))
        summary["mesher"] = "gmsh"
        summary["mesh_size"] = "{:g} mm".format(mesh.CharacteristicLengthMax.Value)
    else:
        summary["mesher"] = getattr(getattr(mesh, "Proxy", None), "Type", mesh.TypeId)
    fem_mesh = mesh.FemMesh
    summary["nodes"] = fem_mesh.NodeCount
    summary["elements"] = fem_mesh.VolumeCount or fem_mesh.FaceCount or fem_mesh.EdgeCount
    if not fem_mesh.NodeCount:
        return _fail("The mesh is empty after meshing")

    from femtools import ccxtools
    fea = ccxtools.FemToolsCcx(analysis, solver)
    try:
        fea.setup_ccx()
    except FileNotFoundError:
        return _fail("CalculiX (ccx) not found. Install it or set its path in "
                     "Preferences → FEM → CalculiX")
    fea.update_objects()
    work = os.path.join(WORK_DIR, "ccx")
    os.makedirs(work, exist_ok=True)
    fea.setup_working_dir(work)
    msg = fea.check_prerequisites()
    if msg:
        return _fail("The analysis is not ready: " + msg.strip())
    fea.purge_results()
    fea.write_inp_file()
    fea.ccx_run()
    stdout = getattr(fea, "ccx_stdout", "") or ""
    summary["ccx_warnings"] = [ln.strip() for ln in stdout.splitlines()
                               if "*WARNING" in ln.upper()][:20]
    frds = glob.glob(os.path.join(work, "*.frd"))
    if not frds:
        return _fail("CalculiX produced no results:\n"
                     + "\n".join(stdout.splitlines()[-20:]))
    summary["frd"] = frds[0]
    fea.load_results()
    results = [o for o in analysis.Group
               if o.isDerivedFrom("Fem::FemResultObjectPython")]
    if results and summary["analysis_type"] == "static":
        r = results[0]
        vm = list(r.vonMises)
        if vm:
            i = max(range(len(vm)), key=vm.__getitem__)
            summary["max_von_mises"] = vm[i]
            p = r.Mesh.FemMesh.Nodes[r.NodeNumbers[i]]
            summary["max_von_mises_at"] = [p.x, p.y, p.z]
        if len(r.DisplacementLengths):
            summary["max_displacement"] = max(r.DisplacementLengths)
    summary["elapsed_s"] = round(time.time() - t0, 2)
    return summary


with open(os.path.join(WORK_DIR, "summary.json"), "w", encoding="utf-8") as _f:
    json.dump(_solve(), _f)
'''


def build_solve_code(analysis_name, mesh_size):
    """The child code that solves ``analysis_name`` and writes summary.json."""
    return "ANALYSIS = {!r}\nMESH_SIZE = {!r}\n".format(
        analysis_name, mesh_size or "") + _SOLVE_CODE


CONVERGENCE_NOTE = (
    "Note: a coarse mesh underestimates peak stress. Re-run with a smaller "
    "mesh_size and compare to check convergence.")


class FemError(Exception):
    """A problem the user can fix; the message is the tool's error text."""


def prepare_copy(analysis, copy_path):
    """Find the analysis and save a copy of its document. GUI thread only.

    Returns (document name, analysis object name).
    """
    from .active_document import resolve_active_document
    doc = resolve_active_document()
    if doc is None:
        raise FemError("No active document")
    analyses = [o for o in doc.Objects if o.isDerivedFrom("Fem::FemAnalysis")]
    if not analyses:
        raise FemError("The active document has no FEM analysis. Set one up "
                       "(analysis, material, constraints, loads) with execute_code first.")
    labels = ", ".join(a.Label for a in analyses)
    if analysis:
        found = [a for a in analyses if analysis in (a.Name, a.Label)]
        if not found:
            raise FemError("No analysis named {!r}. Analyses: {}".format(analysis, labels))
        chosen = found[0]
    elif len(analyses) > 1:
        raise FemError("Several analyses ({}): pass analysis to pick one.".format(labels))
    else:
        chosen = analyses[0]
    doc.saveCopy(copy_path)
    return doc.Name, chosen.Name


def import_results(analysis, frd_path):
    """Replace the analysis's results with those in ``frd_path``."""
    from femresult import resulttools
    from feminout import importCcxFrdResults
    resulttools.purge_results(analysis)
    importCcxFrdResults.importFrd(frd_path, analysis, "CCX_")


def import_into(doc_name, analysis_name, frd_path):
    """Import results into the document the solve started from. GUI thread only.

    Returns the labels of the result object and the pipeline (None if there
    is none). In the GUI a re-imported CCX_Results keeps its Name but is
    labelled CCX_Results001; labels are what the tree and get_document_state
    show, and the tools resolve labels too.
    """
    import FreeCAD as App
    doc = App.getDocument(doc_name)
    analysis = doc.getObject(analysis_name)
    if analysis is None:
        raise FemError("analysis {!r} no longer exists".format(analysis_name))
    import_results(analysis, frd_path)
    doc.recompute()

    def newest(type_id):
        found = [o.Label for o in analysis.Group if o.isDerivedFrom(type_id)]
        return found[-1] if found else None
    return newest("Fem::FemResultObjectPython"), newest("Fem::FemPostPipeline")


def format_summary(summary, import_note=""):
    """The text the LLM sees for a solved analysis."""
    lines = ["FEM analysis {!r} solved with CalculiX in {:g} s.".format(
        summary["analysis"], summary.get("elapsed_s", 0))]
    if summary.get("solver_created"):
        lines.append("The analysis had no CalculiX solver; a default CalculiX "
                     "solver (SolverCcxTools) was used for this run.")
    mesh = [summary["mesher"]]
    if summary.get("mesh_size"):
        mesh.append(summary["mesh_size"])
    mesh += ["{} nodes".format(summary["nodes"]), "{} elements".format(summary["elements"])]
    lines.append("Mesh: {}.".format(", ".join(mesh)))
    static = "max_von_mises" in summary
    if static:
        at = ", ".join("{:.4g}".format(c) for c in summary["max_von_mises_at"])
        lines.append("Max von Mises stress: {:.4g} MPa at ({}) mm".format(
            summary["max_von_mises"], at))
    if "max_displacement" in summary:
        lines.append("Max displacement: {:.4g} mm".format(summary["max_displacement"]))
    if not static:
        lines.append("Analysis type {!r}: only static analyses are summarised.".format(
            summary.get("analysis_type", "")))
    if summary.get("ccx_warnings"):
        lines.append("CalculiX warnings:")
        lines += summary["ccx_warnings"][:20]
    if import_note:
        lines.append(import_note)
    if static:
        lines.append(CONVERGENCE_NOTE)
    return "\n".join(lines)
