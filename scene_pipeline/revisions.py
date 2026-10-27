"""Immutable repair runs: preserve old checkpoints, make the next attempt reviewable."""
from pathlib import Path
import copy
import shutil

from .common import read_json, resolve, sha256, write_json
from .errors import PendingWork


def revise(cfg, output, scripts):
    root = Path(cfg["_root"])
    if cfg["schema_version"] != "1.0":
        raise ValueError("revise requires a current run")
    report = read_json(root / "model" / "report.json")
    source = Path(report.get("file", root / "model" / "scene_editable.blend"))
    if sha256(source) != report["sha256"]:
        raise ValueError("Base model changed; inspect source before revising")
    round_number = cfg.get("revision", {}).get("round", 0) + 1
    if round_number > cfg["quality"]["max_revisions"]:
        raise PendingWork("Revision limit reached; inspect failed parts and request specific additional evidence")
    if not scripts:
        raise ValueError("Provide one or more explicit --script build(context) repair plugins")
    script_paths = [resolve(Path.cwd(), file) for file in scripts]
    for file in script_paths:
        if not file.is_file():
            raise FileNotFoundError(file)
    target = Path(output).resolve()
    if target.exists() and any(target.iterdir()):
        raise FileExistsError("Use an empty revision output folder")
    target.mkdir(parents=True, exist_ok=True)
    child = {k: copy.deepcopy(v) for k, v in cfg.items() if not k.startswith("_")}
    child["videos"] = [str(resolve(root, file)) for file in cfg["videos"]]
    child["model"] = {"mode": "existing", "source": str(source), "scene": report["scene"],
                      "scripts": [str(file) for file in script_paths], "review": None}
    ref = read_json(root / "references" / "manifest.json")
    child["references"].update(provider="import", source=str(root / ref["images"][0]["file"]), review=None)
    child["revision"] = {"round": round_number, "parent_config": cfg["_config_path"],
                         "base_model_sha256": report["sha256"], "reason": "explicit repair plugins; require fresh audit/render/review"}
    # Copy prior observations as an authored draft, preserving provenance, never as a new acceptance.
    prior = resolve(root, cfg["quality"]["observations"])
    child["quality"]["observations"] = "analysis/observations.json"
    child["quality"]["review"] = "quality/review.json"
    if prior.is_file():
        data = read_json(prior)
        data.update(status="draft", request_id="rebind_to_new_analysis_request",
                    provenance={"imported_from": str(prior), "sha256": sha256(prior)})
        write_json(target / "analysis" / "observations.json", data)
    write_json(target / "config.json", child)
    write_json(target / "handoff.json", {"schema_version": "1.0", "parent": str(root), "revision": child["revision"],
               "next": "run pipeline, rebind/recheck the analysis draft, then review new renders; never reuse parent pass records"})
    return target / "config.json"
