"""Automatic model-authored code, reference review, and explicitly bounded repair plans."""
from pathlib import Path

from .agent_tasks import dispatch, task_packet
from .common import read_json, sha256, write_json
from .providers import call_model, materialize_files


def code_template(path):
    return {"request_id": "copy_from_request", "status": "ready",
            "files": [{"path": path, "content": "def build(context):\n    # Implement this phase using bpy.\n    return {}\n"}], "notes": []}


def generate_model(cfg):
    root = Path(cfg["_root"])
    if cfg.get("revision"):
        return  # Explicit repair script was already generated for this new run.
    manifest_file = root / "generated" / "manifest.json"
    if manifest_file.is_file():
        for path, digest in read_json(manifest_file)["files"].items():
            if sha256(root / path) != digest:
                raise ValueError("Generated modeling code changed after generation")
        if read_json(manifest_file).get("complete"):
            return
    previous = read_json(manifest_file) if manifest_file.exists() else {}
    records = previous.get("files", {})
    completed = previous.get("phases", [])
    for kind in ("blockout", "connections", "materials"):
        if kind in completed:
            continue
        target = f"generated/{kind}.py"
        output = root / "generated" / f"{kind}.response.json"
        for trial in range(cfg["automation"]["limits"]["validation_attempts"]):
            packet = task_packet(cfg, kind, output, code_template(target))
            try:
                data = dispatch(cfg, kind, packet)
                files = materialize_files(root, data, target)
                break
            except (ValueError, SyntaxError) as exc:
                if trial + 1 == cfg["automation"]["limits"]["validation_attempts"]:
                    raise
                for path in (output, root / "calls" / kind / "response.json"):
                    if path.exists():
                        path.unlink()
                write_json(root / "tasks" / kind / "validation_feedback.json", {"error": str(exc), "attempt": trial + 1})
        records.update({p.relative_to(root).as_posix(): sha256(p) for p in files})
        completed.append(kind)
        write_json(manifest_file, {"files": records, "phases": completed, "complete": len(completed) == 3})


def reference_review(cfg, review_path, digest):
    from .workflow import review_template
    root = Path(cfg["_root"])
    template = review_template("reference_manifest_sha256", digest)
    packet = task_packet(cfg, "reference_review", review_path, template)
    packet_path = root / "tasks" / "reference_review" / "request.json"
    # The request template already binds the image manifest and original evidence.
    data = dispatch(cfg, "reference_review", packet)
    if data.get("request_id") != packet["request_id"]:
        raise ValueError(f"Reference review request_id mismatch: {packet_path}")
    return data


def repair_code(cfg):
    root = Path(cfg["_root"])
    report = read_json(root / "model" / "report.json")
    target = "generated/repair.py"
    template = {**code_template(target), "base_model_sha256": report["sha256"]}
    packet = task_packet(cfg, "repair", root / "generated" / "repair.response.json", template)
    data = dispatch(cfg, "repair", packet)
    if data.get("base_model_sha256") != report["sha256"]:
        raise ValueError("Repair response refers to a different base model")
    files = materialize_files(root, data, target)
    return [str(p) for p in files if p.suffix == ".py"]
