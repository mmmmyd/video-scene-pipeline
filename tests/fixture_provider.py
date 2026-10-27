"""Deterministic software-test provider. It is NOT a model or real-video fidelity test."""
import argparse
import json
from pathlib import Path

from PIL import Image


def respond(packet):
    root = Path(packet["run_root"])
    kind = packet["kind"]
    template = packet.get("response_template", {})
    out = dict(template)
    out["request_id"] = packet["request_id"]
    manifest = json.loads((root / "inputs" / "manifest.json").read_text(encoding="utf-8"))
    evidence = manifest["videos"][0]["frames"][0]["file"]
    if kind == "analysis":
        out.update(schema_version="1.0", status="ready", author="SOFTWARE TEST FIXTURE - not a real model",
                   video_coverage=[{"video": v["path"], "watched_full": False, "mode": "sampled_frames", "notes": "Software fixture generated from indexed sample images; no semantic accuracy assertion"} for v in manifest["videos"]],
                   parts=[{"id": name, "object": name, "roi": True, "source_evidence_ids": [evidence],
                           "material": {"description": "Fixture gray stone", "source_evidence_ids": [evidence]}} for name in ("ground", "step")],
                   constraints=[{"id": "down", "type": "height_delta", "a": "ground", "b": "step", "range": [-.3, -.2], "basis": "Fixture declared dimensions", "severity": "blocker", "source_evidence_ids": [evidence]},
                                {"id": "width", "type": "ratio", "a": "ground", "b": "step", "axis_a": "x", "axis_b": "x", "range": [.9, 1.1], "basis": "Fixture equal width", "severity": "major", "source_evidence_ids": [evidence]}],
                   review_targets=[{"id": focus, "focus": focus, "parts": ["ground", "step"], "source_evidence_id": evidence,
                                    "source_roi": [.1, .1, .9, .9], "camera_role": role, "description": "Software fixture contract check"}
                                   for focus, role in (("connections", "detail"), ("relative_proportions", "top"), ("negative_obstacle_materials", "detail"), ("multiview", "reverse"))], unknowns=[])
    elif kind == "reference":
        file = root / "calls" / "reference" / "fixture_reference.png"
        file.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (128, 128), "gray").save(file)
        out.update(images=[str(file)], provider="software fixture", actual_model="no model", notes=["Software test only"])
    elif kind == "reference_review":
        out.update(status="accepted", reviewer="software fixture", evidence=[evidence, "references/reference_00.png"],
                   checks={name: True for name in out["checks"]})
    elif kind in {"blockout", "connections", "materials", "repair"}:
        path = out["files"][0]["path"]
        if kind == "blockout":
            code = '''def build(context):
    import bpy
    from mathutils import Vector
    scene=bpy.data.scenes[context['scene_name']]
    bpy.context.window.scene=scene
    mat=bpy.data.materials.new('fixture_stone');mat.use_nodes=True
    mat.node_tree.nodes.get('Principled BSDF').inputs['Roughness'].default_value=.7
    for name,loc,dims in [('ground',(0,-1.5,-.1),(4,3,.2)),('step',(0,.5,-.5),(4,1,.5))]:
        bpy.ops.mesh.primitive_cube_add(size=1,location=loc)
        obj=bpy.context.object;obj.name=name;obj.dimensions=dims
        bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
        obj.data.materials.append(mat);obj['negative_obstacle_roi']=True
    for i,t in enumerate(context['config']['trajectories']):
        d=bpy.data.cameras.new(t['camera']);cam=bpy.data.objects.new(t['camera'],d);scene.collection.objects.link(cam)
        cam.location=(i*.4,-2,1.6);cam.rotation_euler=(Vector((0,.7,-.25))-cam.location).to_track_quat('-Z','Y').to_euler();d.lens=24
    light=bpy.data.lights.new('sun','SUN');light.energy=3;ob=bpy.data.objects.new('sun',light);scene.collection.objects.link(ob)
    scene.world=bpy.data.worlds.new('world');scene.world.use_nodes=True
    return {'created':['ground','step'],'fixture_only':True}
'''
        elif kind == "repair":
            code = "def build(context):\n    import bpy\n    scene=bpy.data.scenes[context['scene_name']]\n    scene.objects['step'].data.materials[0].node_tree.nodes.get('Principled BSDF').inputs['Roughness'].default_value=.65\n    return {'fixture_repair':True}\n"
        else:
            code = "def build(context):\n    return {'fixture_phase':True}\n"
        out.update(status="ready", files=[{"path": path, "content": code}])
    elif kind == "review":
        cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))
        fail_once = cfg["automation"]["models"]["fixture"].get("fail_first_review", False) and not cfg.get("revision")
        out.update(status="complete", reviewer={"name": "software fixture", "provider": "mock", "model": "none"}, issues=[])
        for item in out["items"]:
            item.update(verdict="pass", inspected_source=True, inspected_render=True,
                        findings="Mock contract response; this is not a real semantic assessment", issue_ids=[])
        if fail_once:
            out["items"][0].update(verdict="fail", issue_ids=["roughness"])
            out["issues"] = [{"id": "roughness", "severity": "major", "status": "open", "part_ids": ["step"],
                              "description": "Deliberate software fixture failure", "repair_instruction": "Set step roughness to .65"}]
    else:
        raise ValueError(kind)
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--response", required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    response = Path(args.response)
    response.parent.mkdir(parents=True, exist_ok=True)
    response.write_text(json.dumps(respond(request), ensure_ascii=False, indent=2), encoding="utf-8")
