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
