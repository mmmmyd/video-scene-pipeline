import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .blender_bridge import run_blender, model_via_mcp
from .common import input_fingerprint, read_json, resolve, sha256, write_json
from .geometry import backproject, reprojection_error, trajectory_positions, write_ply
from .media import ingest, encode
from .errors import PendingWork
from .agent_tasks import prompt_text, task_packet


STAGES = ["ingest", "analyze", "references", "model", "quality", "render", "points", "encode", "validate", "package"]
FOCUS_CHECKS = ["connections", "relative_proportions", "negative_obstacle_materials", "multiview"]


def stages_for(cfg):
    return STAGES


def review_template(artifact_key, artifact_sha):
    return {"status": "pending", "reviewer": "", artifact_key: artifact_sha,
            "checks": {name: False for name in FOCUS_CHECKS},
            "evidence": [], "issues": [],
            "policy": "连接、相对比例、负向障碍物材质优先；绝对尺寸允许估算。必须实际查看原始影像和模型检查图。"}


def require_review(path, key, digest, root):
    if not path.is_file():
        raise PendingWork(f"Review required: copy {path.with_name('review.template.json')} to {path}, inspect artifacts and fill it")
    data = read_json(path)
    if data.get("status") != "accepted" or data.get(key) != digest or not data.get("reviewer"):
        raise PendingWork(f"Review is pending or belongs to a different artifact: {path}")
    if not all(data.get("checks", {}).get(name) is True for name in FOCUS_CHECKS):
        raise PendingWork(f"Focus checks are incomplete: {path}")
    if not data.get("evidence"):
        raise PendingWork(f"Review must name inspected image/video evidence: {path}")
    for evidence in data["evidence"]:
        if not resolve(root, evidence).is_file():
            raise FileNotFoundError(f"Review evidence missing: {evidence}")


def references(cfg):
    root = Path(cfg["_root"])
    out = root / "references"
    out.mkdir(exist_ok=True)
    source_manifest = read_json(root / "inputs" / "manifest.json")
    request = {
        "schema_version": "1.0", "task": "multiview_reference_generation",
        "model_label": cfg["references"]["model_label"],
        "frames": [dict(frame, absolute_file=str(root / frame["file"])) for v in source_manifest["videos"] for frame in v["frames"]],
        "brief": "重点复现负向障碍物区域：边缘、内壁、底面、踏步、平台、相邻铺装的材质和纹理；部件连接正确，相对比例一致。绝对尺度可估算。生成主视、反向、俯视和关键连接近景；原视频优先，不可编造开口或将下行梯井补成平台。",
        "output_dir": str(out / "provider_output"),
        "required_response": {"request_id": "copy_from_request", "images": ["absolute/path/to/generated.png"], "provider": "actual service", "actual_model": "actual model or unknown", "notes": []},
    }
    import hashlib
    if cfg["schema_version"] == "1.0":
        from .accuracy import verified_observations
        request["observations"] = verified_observations(cfg)
        request["prompt"] = prompt_text("reference")
    request["request_id"] = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    write_json(out / "request.json", request)
    provider = cfg["references"]["provider"]
    result_file = out / "response.json"
    if provider == "import":
        source = resolve(root, cfg["references"]["source"])
        result = {"request_id": request["request_id"], "images": [str(source)],
                  "provider": "historical_import", "actual_model": "unknown", "notes": ["历史参考图，未宣称本轮重新调用image2.5"]}
        write_json(result_file, result)
    elif provider == "command" and not result_file.exists():
        command = cfg["references"].get("command", [])
        if not command:
            raise ValueError("references.command must be a nonempty argv list")
        command = [arg.replace("{request}", str(out / "request.json")).replace("{response}", str(result_file)) for arg in command]
        with (out / "provider.log").open("w", encoding="utf-8") as stream:
            subprocess.run(command, cwd=root, stdout=stream, stderr=subprocess.STDOUT,
                           check=True, timeout=cfg["tools"]["timeout_s"])
    elif provider == "configured" and not result_file.exists():
        from .providers import call_model
        request.update(kind="reference", run_root=str(root), artifacts=[
            {"file": str(root / "inputs" / "manifest.json"), "sha256": sha256(root / "inputs" / "manifest.json")},
            {"file": str(resolve(root, cfg["quality"]["observations"])), "sha256": sha256(resolve(root, cfg["quality"]["observations"]))}],
            response_template=request["required_response"])
        # Persist exactly the request sent to the configured image service.
        write_json(out / "request.json", request)
        write_json(result_file, call_model(cfg, "reference", request))
    elif provider not in {"handoff", "command", "configured"}:
        raise ValueError(f"Unknown reference provider: {provider}")
    if not result_file.is_file():
        raise PendingWork(f"Reference generation handoff ready: {out / 'request.json'}; provider must write {result_file}")
    response = read_json(result_file)
    if response.get("request_id") != request["request_id"]:
        raise ValueError("Reference response request_id mismatch")
    if not response.get("images"):
        raise ValueError("Reference provider returned no images")
    images = []
    for i, path in enumerate(response["images"]):
        source = resolve(result_file.parent, path)
        with Image.open(source) as image:
            image.verify()
        destination = out / f"reference_{i:02d}{source.suffix.lower()}"
        if source != destination:
            shutil.copy2(source, destination)
        images.append({"file": destination.relative_to(root).as_posix(), "sha256": sha256(destination)})
    manifest = {"request_id": request["request_id"], "provider": response.get("provider", "unknown"),
                "actual_model": response.get("actual_model", "unknown"), "images": images,
                "notes": response.get("notes", [])}
    write_json(out / "manifest.json", manifest)
    digest = sha256(out / "manifest.json")
    write_json(out / "review.template.json", review_template("reference_manifest_sha256", digest))
    review = resolve(root, cfg["references"].get("review") or "references/review.json")
    if cfg.get("automation", {}).get("enabled"):
        from .automatic import reference_review
        reference_review(cfg, review, digest)
    require_review(review, "reference_manifest_sha256", digest, root)
    return manifest


def model(cfg):
    root = Path(cfg["_root"])
    item = cfg["model"]
    if cfg.get("automation", {}).get("enabled"):
        from .automatic import generate_model
        generate_model(cfg)
    if cfg["schema_version"] == "1.0":
        from .accuracy import verified_observations
        verified_observations(cfg)
        if not cfg.get("automation", {}).get("enabled"):
            for phase in ("blockout", "connections", "materials"):
                task_packet(cfg, phase)
    job = {"action": "model", "config": cfg,
           "scripts": [str(resolve(root, p)) for p in item.get("scripts", [])]}
    source = resolve(root, item["source"]) if item["mode"] == "existing" else None
    if cfg["tools"].get("backend") == "managed_mcp":
        from .blender_bridge import managed_model
        managed_model(cfg, job, source)
    elif cfg["tools"].get("backend") == "mcp":
        if source is None:
            raise ValueError("Use managed_mcp or CLI for new scenes; an existing MCP connection requires a matching saved source")
        model_via_mcp(cfg, job, source)
    else:
        run_blender(cfg, job, source)
    report = read_json(root / "model" / "report.json")
    inputs = list(job.get("scripts", []))
    report["model_inputs"] = {p: sha256(p) for p in inputs}
    write_json(root / "model" / "report.json", report)
    run_blender(cfg, {"action": "preview", "config": cfg}, root / "model" / "scene_editable.blend")
    template = review_template("model_sha256", report["sha256"])
    write_json(root / "model" / "review.template.json", template)
    return report


def render(cfg):
    root = Path(cfg["_root"])
    report = read_json(root / "model" / "report.json")
    file = root / "model" / "scene_editable.blend"
    if sha256(file) != report["sha256"]:
        raise ValueError("Editable model changed after checkpoint; start a new run from the revised source")
    from .accuracy import verify_gate
    verify_gate(cfg)
    run_blender(cfg, {"action": "export", "config": cfg}, file)
    return {"manifest": "dataset/manifest.json"}


def points(cfg):
    root = Path(cfg["_root"]) / "dataset"
    manifest = read_json(root / "manifest.json")
    stride = cfg["export"]["point_stride"]
    if not isinstance(stride, int) or stride < 1:
        raise ValueError("point_stride must be a positive integer")
    reports = []
    for track in manifest["tracks"]:
        for frame in track["frames"]:
            if "depth" not in frame:
                continue
            file = root / frame["depth"]
            with np.load(file, allow_pickle=False) as payload:
                depth, k, tw = payload["depth_z"], payload["K"], payload["T_world_from_cam_cv"]
                cam, world, uv = backproject(depth, k, tw, stride)
            with Image.open(root / frame["rgb"]) as image:
                rgb = np.asarray(image.convert("RGB"))
            colors = rgb[uv[:, 1], uv[:, 0]]
            write_ply(file.with_suffix(".camera.ply"), cam, colors)
            write_ply(file.with_suffix(".world.ply"), world, colors)
            reports.append({"depth": frame["depth"], "point_count": len(cam),
                            "reprojection_error_px": reprojection_error(world, uv, k, tw),
                            "point_stride": stride, "colors": "same-pixel rendered RGB, not surface albedo"})
    write_json(root / "pointclouds.json", reports)
    return {"pointcloud_pairs": len(reports)}


def validate(cfg):
    root = Path(cfg["_root"]) / "dataset"
    manifest = read_json(root / "manifest.json")
    errors = []
    checks = []
    if sha256(root / "render_snapshot.blend") != manifest["snapshot_sha256"]:
        errors.append("Snapshot hash differs")
    source = root.parent / "model" / "scene_editable.blend"
    if sha256(source) != manifest["source_model_sha256"]:
        errors.append("Editable source hash differs")
    for track in manifest["tracks"]:
        frames = track["frames"]
        poses = []
        rotations = []
        lenses = []
        for frame in frames:
            meta = read_json(root / frame["camera"])
            tw = np.asarray(meta["T_world_from_cam_cv"])
            poses.append(tw[:3, 3])
            rotations.append(tw[:3, :3])
            lenses.append(meta["lens_mm"])
            if sha256(root / frame["rgb"]) != frame["rgb_sha256"]:
                errors.append(f"RGB hash differs: {frame['rgb']}")
            if meta["snapshot_sha256"] != manifest["snapshot_sha256"]:
                errors.append(f"Camera snapshot mismatch: {frame['camera']}")
            if "depth" not in frame:
                continue
            with np.load(root / frame["depth"], allow_pickle=False) as data:
                z, valid = data["depth_z"], data["valid_mask"]
                if z.dtype != np.float32 or not np.array_equal(np.isfinite(z), valid) or np.any(z[valid] <= 0):
                    errors.append(f"Depth validity mismatch: {frame['depth']}")
                if valid.mean() < cfg["export"]["min_valid_fraction"]:
                    errors.append(f"Insufficient visible surfaces: {frame['depth']}")
                if str(data["snapshot_sha256"]) != manifest["snapshot_sha256"] or str(data["geometry_sha256"]) != manifest["geometry_sha256"]:
                    errors.append(f"Depth provenance mismatch: {frame['depth']}")
                k = np.asarray(meta["K"])
                if not np.allclose(k, data["K"]) or not np.allclose(tw, data["T_world_from_cam_cv"]):
                    errors.append(f"Depth camera mismatch: {frame['depth']}")
                with Image.open(root / frame["rgb"]) as im:
                    if im.size != (z.shape[1], z.shape[0]):
                        errors.append(f"Depth/RGB resolution mismatch: {frame['depth']}")
                cam, world, uv = backproject(z, k, tw, max(1, cfg["export"]["point_stride"]))
                error = reprojection_error(world, uv, k, tw)
                if error is None or error > .001:
                    errors.append(f"Reprojection failed: {frame['depth']}")
                for suffix, expected in ((".camera.ply", cam), (".world.ply", world)):
                    ply = (root / frame["depth"]).with_suffix(suffix)
                    with ply.open("rb") as stream:
                        header = []
                        while True:
                            line = stream.readline()
                            if not line:
                                raise ValueError(f"Truncated PLY header: {ply}")
                            header.append(line)
                            if line.strip() == b"end_header":
                                break
                        rows = np.fromfile(stream, dtype=np.dtype([("xyz", "<f4", (3,)), ("rgb", "u1", (3,))]))
                    if len(rows) != len(expected) or not np.allclose(rows["xyz"], expected, atol=2e-5):
                        errors.append(f"PLY readback differs: {ply.name}")
                checks.append({"frame": frame["depth"], "valid_fraction": float(valid.mean()), "reprojection_error_px": error})
        poses = np.asarray(poses)
        if len(poses) != track["frame_count"] or np.ptp(poses[:, 2]) > 1e-5:
            errors.append(f"Pose count or constant-height failure: {track['id']}")
        expected_poses = trajectory_positions(np.asarray(read_json(root / frames[-1 if track["direction"] == "approach" else 0]["camera"])["matrix_world_blender"]),
                                             track["direction"], track["travel"], len(frames))
        if not np.allclose(poses, expected_poses, atol=1e-5):
            errors.append(f"Trajectory direction/distance failure: {track['id']}")
        if np.max(np.abs(np.asarray(rotations) - rotations[0])) > 1e-5 or np.ptp(lenses) > 1e-5:
            errors.append(f"Rotation/lens changed: {track['id']}")
        cap = cv2.VideoCapture(str(root / track["id"] / "reverse.mp4"))
        count = 0
        try:
            fps = cap.get(cv2.CAP_PROP_FPS)
            while True:
                ok, _ = cap.read()
                if not ok:
                    break
                count += 1
        finally:
            cap.release()
        if count != len(frames) or abs(fps - track["fps"]) > .01:
            errors.append(f"Video decode count/fps differs: {track['id']}")
    result = {"passed": not errors, "scope": "synthetic data consistency; visual fidelity requires artifact-bound model review",
              "errors": errors, "depth_frames": checks}
    write_json(root / "validation.json", result)
    if errors:
        raise ValueError(f"Dataset validation failed: {errors}")
    return result


def package(cfg):
    root = Path(cfg["_root"])
    if not read_json(root / "dataset" / "validation.json")["passed"]:
        raise ValueError("Validate before packaging")
    target = root / "delivery.zip"
    files = [p for p in root.rglob("*") if p.is_file() and p != target and not p.name.endswith((".tmp", ".blend1"))]
    write_json(root / "checksums.json", {p.relative_to(root).as_posix(): sha256(p) for p in files if p.name not in {"state.json", "checksums.json"}})
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in files:
            if file.name not in {"checksums.json", "state.json"}:
                archive.write(file, file.relative_to(root))
        archive.write(root / "checksums.json", "checksums.json")
    return {"file": str(target), "sha256": sha256(target)}


FUNCTIONS = {"ingest": ingest, "references": references, "model": model, "render": render,
             "points": points, "encode": encode, "validate": validate, "package": package}


def execute(cfg, selected=None):
    root = Path(cfg["_root"])
    state_file = root / "state.json"
    all_stages = stages_for(cfg)
    if cfg["schema_version"] == "1.0":
        from .accuracy import analyze, quality, verified_observations, verify_gate
        FUNCTIONS.update(analyze=analyze, quality=quality)
    fingerprint = input_fingerprint(cfg)
    state = read_json(state_file) if state_file.exists() else {"fingerprint": fingerprint, "stages": {}}
    if state["fingerprint"] != fingerprint:
        raise ValueError("Run inputs/config changed. Initialize a new run to prevent mixing model/data versions")
    if cfg["schema_version"] == "1.0":
        if state["stages"].get("analyze", {}).get("status") == "complete":
            verified_observations(cfg)
        if state["stages"].get("quality", {}).get("status") == "complete":
            verify_gate(cfg)
    if state["stages"].get("model", {}).get("status") == "complete":
        report = read_json(root / "model" / "report.json")
        for file, digest in report.get("model_inputs", {}).items():
            if sha256(file) != digest:
                raise ValueError(f"Model-stage input changed after completion; start a new run: {file}")
    stages = [selected] if selected else all_stages
    for stage in stages:
        if state["stages"].get(stage, {}).get("status") == "complete":
            print(f"{stage}: already complete", flush=True)
            continue
        if stage not in all_stages:
            raise ValueError(f"Stage {stage} is unavailable for schema {cfg['schema_version']}")
        index = all_stages.index(stage)
        for prerequisite in all_stages[:index]:
            if state["stages"].get(prerequisite, {}).get("status") != "complete":
                raise PendingWork(f"Complete prerequisite stage first: {prerequisite}")
        state["stages"][stage] = {"status": "running"}
        write_json(state_file, state)
        print(f"{stage}: running", flush=True)
        try:
            detail = FUNCTIONS[stage](cfg)
        except PendingWork as exc:
            state["stages"][stage] = {"status": "pending", "reason": str(exc)}
            write_json(state_file, state)
            raise
        except Exception as exc:
            state["stages"][stage] = {"status": "failed", "reason": str(exc)}
            write_json(state_file, state)
            raise
        state["stages"][stage] = {"status": "complete", "result": detail}
        write_json(state_file, state)
        print(f"{stage}: complete", flush=True)
    return state
