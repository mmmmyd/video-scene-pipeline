"""File-based task packets and argv adapters for any model or human executor."""
import hashlib
import json
import subprocess
from pathlib import Path

from .common import read_json, sha256, write_json
from .errors import PendingWork

PROMPTS = {
    "analysis": "01_analysis.md", "reference": "02_reference.md", "blockout": "03_blockout.md",
    "connections": "04_connections.md", "materials": "05_materials.md", "review": "06_review.md",
    "repair": "07_repair.md", "dataset": "08_dataset.md",
    "reference_review": "02_reference.md",
}


def prompt_text(kind):
    folder = Path(__file__).with_name("prompts")
    return (folder / "00_shared.md").read_text(encoding="utf-8") + "\n\n" + (folder / PROMPTS[kind]).read_text(encoding="utf-8")


def task_packet(cfg, kind, response_path=None, response_template=None):
    root = Path(cfg["_root"])
    folder = root / "tasks" / kind
    folder.mkdir(parents=True, exist_ok=True)
    paths = ["config.json", "inputs/manifest.json", "analysis/source_inventory.json", "analysis/observations.json", "analysis/validated.json", "analysis/roi_crops/manifest.json",
             "references/manifest.json", "model/report.json", "model/review_views/manifest.json",
             "quality/audit.json", "quality/comparisons/manifest.json", "quality/review.json", "quality/execution_error.json"]
    paths.append(f"tasks/{kind}/validation_feedback.json")
    if kind in {"connections", "materials"}:
        paths.append("generated/blockout.py")
    if kind == "materials":
        paths.append("generated/connections.py")
    if kind == "repair":
        paths.extend(["logs/blender_model.log", "quality/repair_plan.json"])
    artifacts = []
    for value in paths:
        path = root / value
        # The output itself is never a request dependency (avoids self-invalidating retries).
        if path.is_file() and path != response_path:
            artifacts.append({"file": str(path), "sha256": sha256(path)})
    if response_path is None:
        response_path = folder / "response.json"
    prompt = prompt_text(kind)
    packet = {"schema_version": "1.0", "kind": kind, "prompt": prompt,
              "run_root": str(root), "artifacts": artifacts, "response_path": str(response_path),
              "response_template": response_template or {"request_id": "copy_from_request", "status": "pending", "artifacts": [], "issues": []},
              "priority": ["connections", "relative_proportions", "negative_obstacle_materials", "dataset_consistency"]}
    if cfg.get("automation", {}).get("enabled"):
        from .providers import role_profile
        name, profile = role_profile(cfg, kind)
        packet["model_profile"] = {"name": name, "provider": profile["provider"], "model": profile["model"], "capabilities": profile.get("capabilities", [])}
        packet["coverage_policy"] = cfg["automation"]["policy"]["video_coverage"]
        packet["execution_rules"] = (
            "Files returned in files[] are written under generated/ and executed in Blender. "
            "Use the exact scene context['scene_name'], object names in observations, and all camera names in config.trajectories. "
            "Blockout must initialize geometry, every dataset camera, world and lights. Later phases preserve those objects. "
            "Only sampled images are available to HTTP vision profiles; record watched_full=false and mode=sampled_frames. "
            "Do not change accepted observations/constraints, write files outside run_root, or read credential environment variables.")
        if kind == "reference_review":
            packet["execution_rules"] += " This is reference-image REVIEW, not generation: inspect original frames and generated references, then return the supplied checks/evidence review JSON with status=accepted only when all checks pass."
    packet["request_id"] = hashlib.sha256(json.dumps(packet, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    write_json(folder / "request.json", packet)
    (folder / "prompt.md").write_text(prompt, encoding="utf-8")
    template = dict(packet["response_template"], request_id=packet["request_id"])
    write_json(folder / "response.template.json", template)
    return packet


def dispatch(cfg, kind, packet):
    """Run an optional external CLI; no assumed model, SDK, tool name or shell."""
    output = Path(packet["response_path"])
    automatic = cfg.get("automation", {}).get("enabled")
    if output.is_file():
        existing = read_json(output)
        if not automatic or existing.get("request_id") == packet["request_id"]:
            return existing
    if automatic:
        from .providers import call_model
        data = call_model(cfg, kind, packet)
        write_json(output, data)
        return data
    command = cfg.get("agents", {}).get(kind, {}).get("command", [])
    if not command:
        raise PendingWork(f"{kind} task ready: {Path(cfg['_root']) / 'tasks' / kind / 'request.json'}; write {output}")
    request = str(Path(cfg["_root"]) / "tasks" / kind / "request.json")
    argv = [arg.replace("{request}", request).replace("{response}", str(output)) for arg in command]
    output.parent.mkdir(parents=True, exist_ok=True)
    with (Path(cfg["_root"]) / "tasks" / kind / "provider.log").open("w", encoding="utf-8") as log:
        subprocess.run(argv, cwd=cfg["_root"], stdout=log, stderr=subprocess.STDOUT,
                       timeout=cfg["tools"]["timeout_s"], check=True)
    if not output.is_file():
        raise PendingWork(f"{kind} adapter returned without required response: {output}")
    return read_json(output)
