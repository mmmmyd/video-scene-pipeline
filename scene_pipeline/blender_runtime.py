"""Executed inside Blender, either by CLI or by ANY agent's MCP execute tool.

Entry contract: result = main('/absolute/path/jobs/model.json').
No cloud credentials, LLM SDK or conversational state are used here.
"""
import importlib.util
import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector, Matrix
from mathutils.bvhtree import BVHTree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scene_pipeline.common import read_json, write_json, sha256
from scene_pipeline.geometry import CV_FROM_BLENDER, trajectory_positions


def activate(scene):
    bpy.context.window.scene = scene
    bpy.context.view_layer.update()


def model(job):
    cfg = job["config"]
    root = Path(cfg["_root"])
    out = root / "model" / "scene_editable.blend"
    if out.exists():
        raise FileExistsError(f"Model already exists; use a new run: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    if cfg["model"]["mode"] == "scripts":
        scene = bpy.data.scenes.new("PIPELINE_" + cfg["case"])
        activate(scene)
        scene.unit_settings.system = "METRIC"
        scene.unit_settings.scale_length = 1
    else:
        name = cfg["model"].get("scene")
        scene = bpy.data.scenes[name] if name else bpy.context.scene
        activate(scene)
    # Establish a new working file before invoking plugins; source file is never saved over.
    bpy.ops.wm.save_as_mainfile(filepath=str(out))
    context = {"run_root": str(root), "scene_name": scene.name, "config": cfg,
               "reference_manifest": str(root / "references" / "manifest.json")}
    checkpoints = []
    # A failed plugin still leaves a known last-good checkpoint for automatic repair.
    def checkpoint_report(file, partial):
        write_json(root / "model" / "report.json", {"file": str(file), "sha256": sha256(file), "scene": scene.name,
                   "objects": len(scene.objects), "cameras": [o.name for o in scene.objects if o.type == "CAMERA"],
                   "checkpoints": checkpoints, "partial": partial, "blender_version": bpy.app.version_string})
    checkpoint_report(out, True)
    for i, script in enumerate(job.get("scripts", [])):
        spec = importlib.util.spec_from_file_location(f"pipeline_stage_{i}", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not callable(getattr(module, "build", None)):
            raise ValueError(f"Stage plugin must expose build(context): {script}")
        report = module.build(context)
        json.dumps(report, allow_nan=False)  # Enforce provider-independent JSON result contract.
        checkpoint = root / "model" / f"checkpoint_{i:02d}.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(checkpoint))
        checkpoints.append({"script": script, "sha256": sha256(script), "result": report, "file": str(checkpoint)})
        checkpoint_report(checkpoint, True)
    activate(scene)
    missing = []
    for image in bpy.data.images:
        if image.source == "FILE" and not image.packed_file:
            file = Path(bpy.path.abspath(image.filepath, library=image.library))
            if not file.is_file():
                missing.append(str(file))
    if missing:
        raise FileNotFoundError(f"Missing image dependencies: {missing}")
    bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=str(out))
    report = {"file": str(out), "sha256": sha256(out), "scene": scene.name,
              "objects": len(scene.objects), "cameras": [o.name for o in scene.objects if o.type == "CAMERA"],
              "checkpoints": checkpoints, "blender_version": bpy.app.version_string}
    write_json(root / "model" / "report.json", report)
    return report


def render_visible_objects(scene):
    visible = set()
    def walk(collection):
        if collection.hide_render:
            return
        visible.update(o.name for o in collection.objects if not o.hide_render)
        for child in collection.children:
            walk(child)
    walk(scene.collection)
    return visible


def freeze(scene, cfg):
    """Freeze modifiers/instances/curves. RGB and BVH use these SAME meshes."""
    activate(scene)
    scene.frame_set(cfg["render"]["frame"])
    visible = render_visible_objects(scene)
    for ob in scene.objects:
        if ob.name not in visible:
            continue
        if ob.type in {"VOLUME", "POINTCLOUD"} or ob.particle_systems:
            raise ValueError(f"Unsupported volume/particle geometry: {ob.name}; bake into meshes first")
        if ob.hide_viewport or ob.hide_get():
            raise ValueError(f"Render-visible object hidden in viewport: {ob.name}; normalize visibility first")
        for mod in ob.modifiers:
            mod.show_viewport = mod.show_render
            if mod.type == "SUBSURF":
                mod.levels = mod.render_levels
        for slot in ob.material_slots:
            mat = slot.material
            if not mat or not mat.use_nodes:
                continue
            for node in mat.node_tree.nodes:
                if node.type == "OUTPUT_MATERIAL" and node.inputs["Displacement"].is_linked:
                    raise ValueError(f"Bake shader displacement into mesh first: {ob.name}")
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    payload = []
    seen = set()
    for instance in dg.object_instances:
        evaluated = instance.object
        original = evaluated.original
        if not instance.show_self or original.hide_render or not getattr(original, "visible_camera", True):
            continue
        if instance.parent:
            if instance.parent.original.name not in visible:
                continue
        elif original.name not in visible:
            continue
        if evaluated.type not in {"MESH", "CURVE", "SURFACE", "FONT", "META"}:
            continue
        matrix = instance.matrix_world.copy()
        key = (original.name, np.asarray(matrix).tobytes())
        if key in seen:
            continue
        seen.add(key)
        mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=dg)
        if mesh and len(mesh.vertices):
            # Keep no iterator-scoped evaluated pointers across linking operations.
            payload.append((original.name, mesh, matrix, dict(original.items())))
    camera_payload = []
    light_payload = []
    for ob in scene.objects:
        if ob.type == "CAMERA" or (ob.type == "LIGHT" and ob.name in visible):
            pose = ob.evaluated_get(dg).matrix_world.copy()
            clone = ob.copy()
            clone.data = ob.data.copy()
            clone.animation_data_clear()
            clone.data.animation_data_clear()
            clone.constraints.clear()
            clone.parent = None
            clone.matrix_world = pose
            (camera_payload if ob.type == "CAMERA" else light_payload).append((ob.name, clone))
    dst = bpy.data.scenes.new("PIPELINE_FROZEN")
    dst.unit_settings.system = "METRIC"
    dst.unit_settings.scale_length = 1
    dst.world = scene.world.copy() if scene.world else None
    if dst.world:
        dst.world.animation_data_clear()
    dst.view_settings.view_transform = scene.view_settings.view_transform
    dst.view_settings.look = scene.view_settings.look
    dst.view_settings.exposure = scene.view_settings.exposure
    dst.view_settings.gamma = scene.view_settings.gamma
    for name, mesh, matrix, properties in payload:
        ob = bpy.data.objects.new("FROZEN_" + name, mesh)
        dst.collection.objects.link(ob)
        ob.matrix_world = matrix
        ob["source_object"] = name
        for key in ("semantic_part", "negative_obstacle_roi"):
            if key in properties:
                ob[key] = properties[key]
    cameras = {}
    for name, clone in camera_payload + light_payload:
        dst.collection.objects.link(clone)
        if clone.type == "CAMERA":
            cameras[name] = clone
    activate(dst)
    return dst, cameras


def geometry(scene):
    vertices, triangles, face_objects = [], [], []
    import hashlib
    digest = hashlib.sha256()
    for ob in sorted(scene.objects, key=lambda x: x.name):
        if ob.type != "MESH":
            continue
        mesh = ob.data
        mesh.calc_loop_triangles()
        coords = np.asarray([ob.matrix_world @ v.co for v in mesh.vertices], dtype="<f4")
        indices = np.asarray([t.vertices[:] for t in mesh.loop_triangles], dtype="<i4")
        digest.update(coords.tobytes())
        digest.update(indices.tobytes())
        offset = len(vertices)
        vertices.extend(coords.tolist())
        triangles.extend((indices + offset).tolist())
        face_objects.extend([ob.name] * len(indices))
    if not triangles:
        raise ValueError("No renderable mesh triangles")
    tree = BVHTree.FromPolygons(vertices, triangles, all_triangles=True, epsilon=0)
    return tree, digest.hexdigest(), face_objects


def camera_metadata(scene, camera, width, height):
    if camera.data.type != "PERSP":
        raise ValueError("Dataset cameras must be perspective pinhole cameras")
    camera.data.dof.use_dof = False
    dg = bpy.context.evaluated_depsgraph_get()
    mw = np.asarray(camera.matrix_world, dtype=float)
    if np.max(np.abs(mw[:3, :3].T @ mw[:3, :3] - np.eye(3))) > 1e-5:
        raise ValueError("Camera world transform must be rigid; remove inherited scaling")
    tw = mw @ CV_FROM_BLENDER
    p = np.asarray(camera.calc_matrix_camera(dg, x=width, y=height, scale_x=1, scale_y=1), dtype=float)
    q = p @ CV_FROM_BLENDER
    q /= q[3, 2]
    k = np.stack([width / 2 * q[0, :3] + (width / 2 - .5) * q[3, :3],
                  -height / 2 * q[1, :3] + (height / 2 - .5) * q[3, :3], q[3, :3]])
    max_error = 0
    for u, v in ((0, 0), (width - 1, 0), (width / 2, height / 2), (width - 1, height - 1)):
        cam = np.linalg.solve(k, [u, v, 1]) * 3
        world = tw[:3, :3] @ cam + tw[:3, 3]
        ndc = world_to_camera_view(scene, camera, Vector(world))
        pixel = np.array([ndc.x * width - .5, (1 - ndc.y) * height - .5])
        max_error = max(max_error, float(np.linalg.norm(pixel - [u, v])))
    if max_error > .002:
        raise ValueError(f"Native Blender camera projection mismatch: {max_error}px")
    return {"K": k.tolist(), "T_world_from_cam_cv": tw.tolist(),
            "T_cam_from_world_cv": np.linalg.inv(tw).tolist(), "matrix_world_blender": mw.tolist(),
            "resolution": [width, height], "native_projection_error_px": max_error,
            "lens_mm": camera.data.lens, "clip": [camera.data.clip_start, camera.data.clip_end]}


def depth_raycast(scene, tree, meta, root, stem, snapshot_sha, geometry_sha, unit):
    width, height = meta["resolution"]
    k = np.asarray(meta["K"])
    tw = np.asarray(meta["T_world_from_cam_cv"])
    tc = np.asarray(meta["T_cam_from_world_cv"])
    inverse = np.linalg.inv(k)
    near, far = meta["clip"]
    depth = np.full((height, width), np.nan, dtype=np.float32)
    face_index = np.full((height, width), -1, dtype=np.int32)
    for v in range(height):
        pixels = np.c_[np.arange(width), np.full(width, v), np.ones(width)]
        rays = pixels @ inverse.T
        norms = np.linalg.norm(rays, axis=1)
        directions = rays @ tw[:3, :3].T
        origins = tw[:3, 3] + directions * near
        directions /= norms[:, None]
        for u in range(width):
            hit, _, face, _ = tree.ray_cast(Vector(origins[u]), Vector(directions[u]), float((far - near) * norms[u]))
            if hit is not None:
                z = float(tc[2, :3] @ np.asarray(hit) + tc[2, 3])
                if near - 1e-5 <= z <= far + 1e-5:
                    depth[v, u] = z
                    face_index[v, u] = face
    valid = np.isfinite(depth)
    samples = np.flatnonzero(valid)
    samples = samples[np.linspace(0, len(samples) - 1, min(48, len(samples)), dtype=int)] if len(samples) else []
    errors = []
    dg = bpy.context.evaluated_depsgraph_get()
    for flat in samples:
        v, u = divmod(int(flat), width)
        ray = inverse @ [u, v, 1]
        direction = tw[:3, :3] @ ray
        norm = np.linalg.norm(direction)
        origin = tw[:3, 3] + direction * near
        hit, location, *_ = scene.ray_cast(dg, Vector(origin), Vector(direction / norm), distance=float((far - near) * norm))
        if not hit:
            raise ValueError("Native raycast disagrees with frozen BVH")
        z = float(tc[2, :3] @ np.asarray(location) + tc[2, 3])
        errors.append(abs(z - float(depth[v, u])))
    max_error = max(errors, default=None)
    if max_error is not None and max_error > .002:
        raise ValueError(f"Native raycast depth mismatch: {max_error}")
    np.savez_compressed(root / (stem + ".npz"), depth_z=depth, valid_mask=valid, face_index=face_index,
                        K=k, T_world_from_cam_cv=tw, T_cam_from_world_cv=tc,
                        unit=np.array(unit), synthetic=np.array(True),
                        snapshot_sha256=np.array(snapshot_sha), geometry_sha256=np.array(geometry_sha))
    return {"valid_fraction": float(valid.mean()), "raycast_samples": len(errors), "native_depth_error_max": max_error}


def export(job):
    cfg = job["config"]
    root = Path(cfg["_root"])
    folder = root / "dataset"
    folder.mkdir(parents=True, exist_ok=True)
    source = bpy.data.filepath
    scene_name = read_json(root / "model" / "report.json")["scene"]
    src = bpy.data.scenes[scene_name]
    if abs(src.unit_settings.scale_length - 1) > 1e-8:
        raise ValueError("Normalize model to one Blender unit per (estimated) meter before export")
    if not cfg["render"].get("static_scene", True):
        raise ValueError("Exporter freezes one geometry frame; dynamic scene datasets require per-frame freezing")
    scene, cameras = freeze(src, cfg)
    settings = cfg["render"]
    width, height = settings["width"], settings["height"]
    scene.render.engine = settings["engine"]
    scene.render.resolution_x, scene.render.resolution_y = width, height
    scene.render.resolution_percentage = 100
    scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1
    scene.render.use_border = False
    scene.render.use_motion_blur = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.color_depth = "8"
    scene.render.film_transparent = False
    scene.use_nodes = False
    scene.cycles.samples = settings["samples"]
    scene.cycles.device = "CPU"
    scene.cycles.seed = settings["seed"]
    scene.cycles.use_animated_seed = False
    scene.cycles.use_denoising = True
    for camera in cameras.values():
        camera.data.dof.use_dof = False
    tree, geometry_sha, faces = geometry(scene)
    snapshot = folder / "render_snapshot.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(snapshot))
    snapshot_sha = sha256(snapshot)
    write_json(folder / "face_objects.json", faces)
    manifest = {"schema_version": "1.0", "synthetic": True, "unit": cfg["scale"]["unit"],
                "source_model_sha256": sha256(source), "snapshot_sha256": snapshot_sha,
                "geometry_sha256": geometry_sha, "blender_version": bpy.app.version_string,
                "depth_policy": "nearest opaque geometry surface, positive camera Z; glass/water opaque for depth, physical shaders for RGB",
                "pixel_convention": "integer pixel centers, top-left=(0,0); zero lens distortion",
                "world_axes": "right-handed; Z up; X/Y inherited from source model",
                "motion_scope": "camera-only horizontal translation; fixed rotation/lens; no vehicle dynamics or swept-volume certification",
                "tracks": []}
    for track in cfg["trajectories"]:
        name = track.get("camera")
        if name not in cameras:
            raise ValueError(f"Camera {name!r} not in scene. Available: {list(cameras)}")
        camera = cameras[name]
        original = camera.matrix_world.copy()
        n = round(track["duration_s"] * track["fps"])
        positions = trajectory_positions(np.asarray(original), track["direction"], track["travel"], n)
        rec = {**track, "frame_count": n, "encoded_duration_s": n / track["fps"],
               "frame_center_span_s": (n - 1) / track["fps"],
               "speed": track["travel"] / ((n - 1) / track["fps"]), "frames": []}
        target = folder / track["id"]
        target.mkdir(exist_ok=True)
        every = track.get("multimodal_every", 1)
        if not isinstance(every, int) or every < 1:
            raise ValueError("multimodal_every must be positive integer")
        for index, position in enumerate(positions):
            stem = f"{index:06d}"
            camera.matrix_world = original.copy()
            camera.matrix_world.translation = Vector(position)
            scene.camera = camera
            bpy.context.view_layer.update()
            meta = camera_metadata(scene, camera, width, height)
            meta.update({"frame": index, "time_s": index / track["fps"], "synthetic": True,
                         "snapshot_sha256": snapshot_sha, "geometry_sha256": geometry_sha,
                         "unit": cfg["scale"]["unit"], "camera_source": name})
            scene.render.filepath = str(target / (stem + ".png"))
            bpy.ops.render.render(write_still=True)
            write_json(target / (stem + ".camera.json"), meta)
            item = {"index": index, "time_s": index / track["fps"],
                    "rgb": f"{track['id']}/{stem}.png", "rgb_sha256": sha256(target / (stem + ".png")),
                    "camera": f"{track['id']}/{stem}.camera.json"}
            if index % every == 0 or index == n - 1:
                quality = depth_raycast(scene, tree, meta, target, stem, snapshot_sha, geometry_sha, cfg["scale"]["unit"])
                item.update({"depth": f"{track['id']}/{stem}.npz", "quality": quality})
            rec["frames"].append(item)
            write_json(folder / "progress.json", {"track": track["id"], "frame": index, "count": n})
        camera.matrix_world = original
        write_json(target / "trajectory.json", rec)
        manifest["tracks"].append(rec)
    _, after_sha, _ = geometry(scene)
    if after_sha != geometry_sha:
        raise ValueError("Geometry changed during RGB/depth render")
    write_json(folder / "manifest.json", manifest)
    if cfg["export"].get("model_glb"):
        # GLB is a convenience geometry export. Blender remains material ground truth.
        bpy.ops.export_scene.gltf(filepath=str(folder / "model.glb"), export_format="GLB",
                                  use_active_scene=True, use_renderable=True, export_animations=False)
    return {"tracks": len(manifest["tracks"]), "snapshot_sha256": snapshot_sha}


def preview(job):
    """Real model renders available before the model-review gate."""
    cfg = job["config"]
    root = Path(cfg["_root"])
    report = read_json(root / "model" / "report.json")
    scene = bpy.data.scenes[report["scene"]]
    activate(scene)
    scene.frame_set(cfg["render"]["frame"])
    scene.render.engine = cfg["render"]["engine"]
    scene.render.resolution_x = cfg["render"]["width"]
    scene.render.resolution_y = cfg["render"]["height"]
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.cycles.device = "CPU"
    scene.cycles.samples = min(16, cfg["render"]["samples"])
    scene.cycles.use_denoising = True
    roles = {}
    if cfg["schema_version"] == "1.0":
        names = make_review_cameras(scene, cfg)
        roles = {name: role for role, name in names.items()}
        names = list(roles)
    else:
        names = cfg["model"].get("review_cameras") or list(dict.fromkeys(t["camera"] for t in cfg["trajectories"]))
    images = []
    out = root / "model" / "review_views"
    out.mkdir(exist_ok=True)
    for i, name in enumerate(names):
        if name not in scene.objects or scene.objects[name].type != "CAMERA":
            raise ValueError(f"Missing review camera {name!r}; inspect source scene before configuring")
        scene.camera = scene.objects[name]
        file = out / f"review_{i:02d}.png"
        scene.render.filepath = str(file)
        bpy.ops.render.render(write_still=True)
        record = {"camera": name, "file": file.relative_to(root).as_posix(), "sha256": sha256(file)}
        if roles:
            record.update(role=roles[name], matrix_world_blender=[list(row) for row in scene.camera.matrix_world])
        images.append(record)
    write_json(out / "manifest.json", {"model_sha256": report["sha256"], "images": images})
    return {"images": len(images)}


def make_review_cameras(scene, cfg):
    """Explicit review views, or a documented automatic initial layout around mapped ROI."""
    obs = read_json(Path(cfg["_root"]) / cfg["quality"]["observations"])
    roi_names = {p["object"] for p in obs["parts"] if p.get("roi")}
    points = [o.matrix_world @ Vector(corner) for o in scene.objects if o.type == "MESH" and o.name in roi_names for corner in o.bound_box]
    if not points:
        raise ValueError("No mapped ROI geometry for review cameras")
    xyz = np.asarray(points)
    center = (xyz.min(axis=0) + xyz.max(axis=0)) / 2
    extent = max(float(np.max(xyz.max(axis=0) - xyz.min(axis=0))), 1)
    main_name = cfg["trajectories"][0].get("camera")
    main = scene.objects.get(main_name) if main_name else None
    if main is None or main.type != "CAMERA":
        raise ValueError("Set trajectories[0].camera to a verified source camera")
    direction = np.asarray(main.matrix_world.translation) - center
    direction[2] = 0
    if np.linalg.norm(direction) < 1e-6:
        direction = np.array([0., -1., 0.])
    direction /= np.linalg.norm(direction)
    views = cfg["quality"].get("views") or [
        {"role": "main", "camera": main_name},
        {"role": "top", "position": (center + [0, 0, extent * 1.5]).tolist(), "target": center.tolist()},
        {"role": "reverse", "position": (center - direction * extent + [0, 0, extent * .7]).tolist(), "target": center.tolist()},
        {"role": "detail", "position": (center + direction * extent * .5 + [0, 0, extent * .45]).tolist(), "target": center.tolist()},
    ]
    roles = {}
    for view in views:
        role = view["role"]
        if role in roles:
            raise ValueError("Review view roles must be unique")
        if view.get("camera"):
            original = scene.objects[view["camera"]]
            cam = original.copy()
            cam.data = original.data.copy()
            pose = original.evaluated_get(bpy.context.evaluated_depsgraph_get()).matrix_world.copy()
            cam.animation_data_clear()
            cam.constraints.clear()
            cam.parent = None
            cam.matrix_world = pose
        else:
            data = bpy.data.cameras.new("QA_" + role)
            cam = bpy.data.objects.new("QA_" + role, data)
            cam.location = view["position"]
            cam.rotation_euler = (Vector(view["target"]) - cam.location).to_track_quat("-Z", "Y").to_euler()
            data.lens = view.get("lens_mm", 28)
            data.clip_end = max(1000, extent * 10)
        scene.collection.objects.link(cam)
        cam.data.dof.use_dof = False
        roles[role] = cam.name
    if set(roles) != set(cfg["quality"]["required_view_roles"]):
        raise ValueError("Review views must cover main/top/reverse/detail")
    return roles


def audit(job):
    from scene_pipeline.accuracy import evaluate_constraints
    cfg = job["config"]
    root = Path(cfg["_root"])
    report = read_json(root / "model" / "report.json")
    observations = read_json(root / cfg["quality"]["observations"])
    scene, _ = freeze(bpy.data.scenes[report["scene"]], cfg)
    tree, geometry_sha, _ = geometry(scene)
    inventory = {}
    for obj in scene.objects:
        if obj.type != "MESH":
            continue
        xyz = np.asarray([obj.matrix_world @ vertex.co for vertex in obj.data.vertices])
        inventory[obj["source_object"]] = {"min": xyz.min(axis=0).tolist(), "max": xyz.max(axis=0).tolist(),
                                           "materials": [m.name for m in obj.data.materials if m]}
    def cast(origin, direction, distance):
        hit, *_ = tree.ray_cast(Vector(origin), Vector(direction), distance)
        return list(hit) if hit is not None else None
    result = evaluate_constraints(observations, inventory, cast)
    missing = [p["object"] for p in observations["parts"] if p["object"] not in inventory]
    unshaded = [p["object"] for p in observations["parts"] if p.get("roi") and not inventory.get(p["object"], {}).get("materials")]
    result.update(model_sha256=report["sha256"], geometry_sha256=geometry_sha, inventory=inventory,
                  missing_objects=missing, roi_without_material=unshaded)
    result["passed"] = result["passed"] and not missing and not unshaded
    write_json(root / "quality" / "audit.json", result)
    return {"passed": result["passed"], "checks": len(result["checks"])}


def inspect_source(job):
    cfg = job["config"]
    name = cfg["model"].get("scene")
    scene = bpy.data.scenes[name] if name else bpy.context.scene
    activate(scene)
    objects = []
    for obj in sorted(scene.objects, key=lambda x: x.name):
        row = {"name": obj.name, "type": obj.type, "collections": [c.name for c in obj.users_collection],
               "hide_render": obj.hide_render, "materials": [s.material.name for s in obj.material_slots if s.material]}
        if obj.type == "MESH":
            points = np.asarray([obj.matrix_world @ Vector(p) for p in obj.bound_box])
            row.update(min=points.min(axis=0).tolist(), max=points.max(axis=0).tolist())
        objects.append(row)
    report = {"source": bpy.data.filepath, "source_sha256": sha256(bpy.data.filepath), "scene": scene.name,
              "unit_scale_length": scene.unit_settings.scale_length, "objects": objects,
              "scope": "read-only source inventory; bounds are current object bounds, not the later evaluated geometry audit"}
    write_json(Path(cfg["_root"]) / "analysis" / "source_inventory.json", report)
    return {"objects": len(objects), "scene": scene.name}


def main(job_path):
    job = read_json(job_path)
    if job["action"] == "model":
        return model(job)
    if job["action"] == "export":
        return export(job)
    if job["action"] == "preview":
        return preview(job)
    if job["action"] == "audit":
        return audit(job)
    if job["action"] == "inspect":
        return inspect_source(job)
    raise ValueError(f"Unknown Blender action: {job['action']}")


if __name__ == "__main__":
    result = main(sys.argv[sys.argv.index("--") + 1])
    print(json.dumps(result, ensure_ascii=False), flush=True)
