"""The single public entry: configure models once, then run all stages and bounded repairs."""
import copy
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse

import cv2

from .blender_bridge import executable
from .configuration import config_template
from .common import load_config, read_json, resolve, sha256, write_json
from .errors import PendingWork
from .revisions import revise
from .workflow import execute, stages_for

ROLES = {"analysis": "builder", "blockout": "builder", "connections": "builder", "materials": "builder",
         "repair": "builder", "reference": "reference", "reference_review": "reviewer", "review": "reviewer", "dataset": "builder"}


def project_template():
    chat = {"provider": "REPLACE_PROVIDER", "model": "REPLACE_VISION_CODE_MODEL", "transport": "chat_completions",
            "endpoint": "https://REPLACE_HOST/v1/chat/completions", "api_key_env": "SCENE_MODEL_API_KEY",
            "capabilities": ["vision", "code"], "timeout_s": 600, "max_images": 80,
            "image_max_side": 1280, "max_context_chars": 600000, "parameters": {"response_format": {"type": "json_object"}}}
    return {"schema_version": "1.0", "models": {
        "builder": chat,
        "reviewer": {**copy.deepcopy(chat), "model": "REPLACE_VISION_REVIEW_MODEL", "capabilities": ["vision"]},
        "reference": {"provider": "REPLACE_IMAGE_PROVIDER", "model": "REPLACE_ACTUAL_IMAGE_MODEL_ID", "stage_label": "image2.5",
                      "transport": "image_edits", "endpoint": "https://REPLACE_HOST/v1/images/edits", "api_key_env": "SCENE_IMAGE_API_KEY",
                      "capabilities": ["image_generation"], "timeout_s": 600, "max_images": 80,
                      "parameters": {"n": 1, "size": "1536x1024"}}},
        "roles": dict(ROLES), "input": {"videos": ["REPLACE_VIDEO_01.mp4", "REPLACE_VIDEO_02.mp4"]},
        "blender": {"executable": "blender", "backend": "managed_mcp", "addon_dir": "vendor", "timeout_s": 3600},
        "output": {"directory": "outputs/scene", "width": 640, "height": 360, "samples": 16,
                   "view_ids": ["rear_center", "rear_left", "rear_right"], "duration_s": 3.0, "fps": 24,
                   "travel": 1.2, "multimodal_every": 6, "point_stride": 2},
        "ingest": {"interval_s": 1.0, "max_frames_per_video": 80, "extra_times_s": {}},
        "policy": {"video_coverage": "sampled_frames"},
        "limits": {"provider_attempts": 2, "validation_attempts": 2, "max_repairs": 3}}


def preflight(path):
    path = Path(path).resolve()
    settings = read_json(path)
    base = path.parent
    errors = []
    if settings.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    settings.setdefault("roles", dict(ROLES))
    models = settings.get("models", {})
    for role in ROLES:
        profile_name = settings["roles"].get(role)
        if profile_name not in models:
            errors.append(f"roles.{role}: select a model profile")
    for name in set(settings["roles"].values()):
        profile = models.get(name, {})
        for field in ("provider", "model"):
            value = profile.get(field, "")
            if not value or "REPLACE" in value:
                errors.append(f"models.{name}.{field}: fill actual service information")
        transport = profile.get("transport")
        if transport not in {"chat_completions", "image_edits", "json_http", "command"}:
            errors.append(f"models.{name}.transport: unsupported")
        if "api_key" in profile:
            errors.append(f"models.{name}: use api_key_env instead of an inline secret")
        if transport == "command":
            argv = profile.get("command", [])
            if not argv or "{request}" not in " ".join(argv) or "{response}" not in " ".join(argv):
                errors.append(f"models.{name}.command: require argv with request/response placeholders")
            elif argv[0] != "{python}" and not (shutil.which(argv[0]) or resolve(base, argv[0]).is_file()):
                errors.append(f"models.{name}.command: executable not found")
        else:
            url = urlparse(profile.get("endpoint", ""))
            if not url.hostname or "REPLACE" in url.netloc or url.scheme not in {"https", "http"}:
                errors.append(f"models.{name}.endpoint: fill actual endpoint")
            if url.username or url.password or url.query:
                errors.append(f"models.{name}.endpoint: do not put credentials in URL")
            if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
                errors.append(f"models.{name}.endpoint: use HTTPS for remote services")
            key_env = profile.get("api_key_env")
            if key_env and not os.environ.get(key_env):
                errors.append(f"models.{name}: environment variable {key_env} is missing")
            if not key_env and not profile.get("allow_anonymous", False):
                errors.append(f"models.{name}: specify api_key_env or explicitly allow_anonymous for a local service")
            if set(profile.get("parameters", {})) & {"model", "messages", "stream", "prompt", "image"}:
                errors.append(f"models.{name}.parameters: core request fields cannot be overridden")
    for role, need in (("analysis", "vision"), ("review", "vision"), ("reference_review", "vision"),
                       ("blockout", "code"), ("connections", "code"), ("materials", "code"), ("repair", "code"), ("reference", "image_generation")):
        profile = models.get(settings["roles"].get(role), {})
        if need not in profile.get("capabilities", []):
            errors.append(f"roles.{role}: selected profile must declare {need} capability")
        if role == "reference" and profile.get("transport") == "chat_completions":
            errors.append("reference role needs an image generation transport, not text-only chat output")
    blender = settings.get("blender", {})
    try:
        requested = blender.get("executable", "blender")
        if "/" in requested or "\\" in requested:
            requested = str(resolve(base, requested))
        blender["executable"] = executable(requested)
    except (OSError, ValueError) as exc:
        errors.append(str(exc))
    if blender.get("backend") not in {"cli", "managed_mcp"}:
        errors.append("blender.backend must be cli or managed_mcp")
    if blender.get("backend") == "managed_mcp":
        addon = resolve(base, blender.get("addon_dir", ""))
        if not (addon / "blender_mcp_addon" / "__init__.py").is_file():
            errors.append("blender.addon_dir must contain the official blender_mcp_addon package")
        blender["addon_dir"] = str(addon)
    settings["blender"] = blender
    videos = settings.get("input", {}).get("videos", [])
    if not videos:
        errors.append("input.videos must contain at least one source video")
    absolute_videos = []
    total_frames = 0
    interval = settings.get("ingest", {}).get("interval_s", 1)
    limit = settings.get("ingest", {}).get("max_frames_per_video", 80)
    if not isinstance(interval, (int, float)) or isinstance(interval, bool) or interval <= 0:
        errors.append("ingest.interval_s must be positive")
        interval = 1
    for value in videos:
        file = resolve(base, value)
        absolute_videos.append(str(file))
        if not file.is_file():
            errors.append(f"Video not found: {file}")
            continue
        cap = cv2.VideoCapture(str(file))
        try:
            ok, _ = cap.read()
            fps, count = cap.get(cv2.CAP_PROP_FPS), cap.get(cv2.CAP_PROP_FRAME_COUNT)
            if not ok or fps <= 0 or count < 2:
                errors.append(f"Video cannot be decoded: {file}")
                continue
            estimate = min(int(count), int((count - 1) / fps / interval) + 2)
            total_frames += estimate
            if estimate > limit:
                errors.append(f"{file.name}: extraction estimate {estimate} exceeds max_frames_per_video")
        finally:
            cap.release()
    coverage = settings.get("policy", {}).get("video_coverage")
    if coverage not in {"sampled_frames", "full_video"}:
        errors.append("policy.video_coverage must be sampled_frames or full_video")
    if coverage == "full_video":
        profile = models.get(settings["roles"].get("analysis"), {})
        if profile.get("transport") != "command" or "video" not in profile.get("capabilities", []):
            errors.append("full_video requires a command adapter with native video capability; HTTP image inputs are sampled_frames")
    limits = settings.get("limits", {})
    for key in ("provider_attempts", "validation_attempts", "max_repairs"):
        value = limits.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < (0 if key == "max_repairs" else 1):
            errors.append(f"limits.{key} must be an integer in range")
    out = settings.get("output", {})
    if not out.get("directory"):
        errors.append("output.directory is required")
    settings["input"]["videos"] = absolute_videos
    settings["_launch_path"] = str(path)
    settings["_output"] = str(resolve(base, out.get("directory", "outputs/scene")))
    if errors:
        raise ValueError("Startup configuration incomplete:\n- " + "\n- ".join(errors))
    # Reuse the actual pipeline's numeric/camera validation before any paid model call.
    test_cfg = pipeline_config(settings, Path(settings["_output"]) / "attempt_000")
    import tempfile
    with tempfile.TemporaryDirectory() as temp:
        candidate = Path(temp) / "config.json"
        write_json(candidate, test_cfg)
        load_config(candidate)
    if total_frames > models[settings["roles"]["analysis"]].get("max_images", 80):
        raise ValueError("Total extracted frames exceed analysis profile max_images; adjust extraction or model limit")
    return settings


def pipeline_config(settings, run_root):
    cfg = config_template(Path(settings["_launch_path"]).parent)
    out = settings["output"]
    cfg["case"] = "automatic_video_scene"
    cfg["videos"] = settings["input"]["videos"]
    cfg["tools"].update(blender=settings["blender"]["executable"], backend=settings["blender"]["backend"],
                        addon_dir=settings["blender"].get("addon_dir"), timeout_s=settings["blender"].get("timeout_s", 3600))
    cfg["ingest"] = settings["ingest"]
    cfg["references"].update(provider="configured", source=None, command=[])
    cfg["model"] = {"mode": "scripts", "source": None, "scene": None,
                    "scripts": [f"generated/{kind}.py" for kind in ("blockout", "connections", "materials")], "review": None}
    cfg["render"].update(width=out["width"], height=out["height"], samples=out["samples"])
    cfg["trajectories"] = [{"id": name, "camera": "DATA_" + name, "duration_s": out["duration_s"], "fps": out["fps"],
                            "travel": out["travel"], "direction": "approach", "multimodal_every": out["multimodal_every"]} for name in out["view_ids"]]
    cfg["export"]["point_stride"] = out["point_stride"]
    cfg["quality"]["max_revisions"] = settings["limits"]["max_repairs"]
    cfg["automation"] = {"enabled": True, "models": settings["models"], "roles": settings["roles"],
                         "policy": settings["policy"], "limits": settings["limits"]}
    return cfg


def run_project(settings):
    root = Path(settings["_output"])
    lock = root / "launcher_state.json"
    digest = sha256(settings["_launch_path"])
    if lock.is_file():
        journal = read_json(lock)
        if journal["launch_sha256"] != digest:
            raise ValueError("Project configuration changed; select a new output.directory")
        active = Path(journal["active_config"])
    else:
        active = root / "attempt_000" / "config.json"
        if active.parent.exists() and any(active.parent.iterdir()):
            raise FileExistsError("Output directory already contains an untracked attempt")
        write_json(active, pipeline_config(settings, active.parent))
        journal = {"launch_sha256": digest, "active_config": str(active), "status": "running", "attempts": []}
        write_json(lock, journal)
    while True:
        cfg = load_config(active)
        root_cfg = Path(cfg["_root"])
        try:
            for stage in stages_for(cfg):
                journal.update(status="running", stage=stage, active_config=str(active))
                write_json(lock, journal)
                for trial in range(settings["limits"]["validation_attempts"]):
                    try:
                        execute(cfg, stage)
                        break
                    except (PendingWork, ValueError) as exc:
                        if stage in {"model", "quality"} and (root_cfg / "model" / "report.json").exists():
                            raise
                        if trial + 1 == settings["limits"]["validation_attempts"] or stage not in {"analyze", "references"}:
                            raise
                        # Remove only current failed adapter responses. Source/model files stay immutable.
                        kind = "analysis" if stage == "analyze" else "reference_review"
                        output = root_cfg / ("analysis/observations.json" if stage == "analyze" else "references/review.json")
                        if output.exists():
                            output.unlink()
                        response = root_cfg / "calls" / kind / "response.json"
                        if response.exists():
                            response.unlink()
                        write_json(root_cfg / "tasks" / kind / "validation_feedback.json", {"error": str(exc), "attempt": trial + 1})
            break
        except Exception as exc:
            model_error = journal.get("stage") == "model"
            quality_error = journal.get("stage") == "quality" and any(
                path.is_file() and read_json(path).get("passed") is False
                for path in (root_cfg / "quality" / "audit.json", root_cfg / "quality" / "gate.json"))
            can_repair = (model_error or quality_error) and (root_cfg / "model" / "report.json").is_file()
            count = cfg.get("revision", {}).get("round", 0)
            if not can_repair or count >= settings["limits"]["max_repairs"]:
                journal.update(status="failed", error=str(exc))
                write_json(lock, journal)
                raise RuntimeError(f"Pipeline stopped at {journal.get('stage')}: {exc}. See {lock}") from exc
            from .automatic import repair_code
            print("automatic repair: generating a new checkpoint revision", flush=True)
            write_json(root_cfg / "quality" / "execution_error.json", {"stage": journal.get("stage"), "error": str(exc)})
            try:
                scripts = repair_code(cfg)
            except Exception as repair_exc:
                journal.update(status="failed", error=f"Repair generation failed: {repair_exc}")
                write_json(lock, journal)
                raise
            child = root / f"attempt_{count + 1:03d}"
            active = revise(cfg, child, scripts)
            journal["attempts"].append({"previous_config": cfg["_config_path"], "reason": str(exc), "new_config": str(active)})
            journal.update(active_config=str(active), status="repairing")
            write_json(lock, journal)
    final = Path(active).parent
    delivery = root / "DELIVERY.zip"
    shutil.copy2(final / "delivery.zip", delivery)
    result = {"status": "complete", "active_config": str(active), "run_root": str(final),
              "files": {"model": str(final / "model" / "scene_editable.blend"), "glb": str(final / "dataset" / "model.glb"),
                        "dataset_manifest": str(final / "dataset" / "manifest.json"), "data_directory": str(final / "dataset"),
                        "quality": str(final / "quality" / "gate.json"), "validation": str(final / "dataset" / "validation.json"),
                        "delivery": str(delivery)}, "repair_rounds": len(journal["attempts"]),
              "video_coverage": settings["policy"]["video_coverage"]}
    write_json(root / "result.json", result)
    journal.update(status="complete", result=str(root / "result.json"))
    write_json(lock, journal)
    print(f"COMPLETE: {root / 'result.json'}", flush=True)
    return result
