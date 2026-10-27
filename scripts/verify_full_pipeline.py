"""Single-entry integration with a deterministic provider and actual Blender.

Deliberately fails the first visual-review contract once, then automatically repairs.
No real AI service or measured visual reconstruction quality is claimed.
"""
import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scene_pipeline.common import read_json, write_json
from scene_pipeline.launcher import project_template, preflight, run_project


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--backend", choices=["cli", "managed_mcp"], default="cli")
    parser.add_argument("--addon-dir")
    args = parser.parse_args()
    root = Path(args.output).resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError("Use a new integration output folder")
    root.mkdir(parents=True, exist_ok=True)
    video = root / "fixture_input.mp4"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 4, (160, 96))
    for i in range(8):
        image = np.full((96, 160, 3), 90 + i * 2, np.uint8)
        image[40:70, 40:120] = 45
        writer.write(image)
    writer.release()
    project = project_template()
    provider = Path(__file__).resolve().parents[1] / "tests" / "fixture_provider.py"
    project["models"] = {"fixture": {"provider": "SOFTWARE TEST ONLY", "model": "deterministic fixture, no AI", "transport": "command",
                                    "command": ["{python}", str(provider), "--request", "{request}", "--response", "{response}"],
                                    "capabilities": ["vision", "code", "image_generation"], "timeout_s": 60, "max_images": 80,
                                    "fail_first_review": True}}
    project["roles"] = {kind: "fixture" for kind in project["roles"]}
    project["input"]["videos"] = [str(video)]
    project["output"].update(directory=str(root / "output"), width=160, height=96, samples=2,
                             view_ids=["rear_center", "rear_left"], duration_s=2.0, fps=2, multimodal_every=2)
    project["blender"].update(backend=args.backend, addon_dir=str(Path(args.addon_dir).resolve()) if args.addon_dir else "")
    path = root / "project.json"
    write_json(path, project)
    result = run_project(preflight(path))
    if result["repair_rounds"] != 1 or not Path(result["files"]["delivery"]).is_file():
        raise AssertionError("Automatic repair or final packaging did not complete")
    if not read_json(result["files"]["validation"])["passed"]:
        raise AssertionError("Dataset validation failed")
    write_json(root / "integration_result.json", {"passed": True, "backend": args.backend,
               "automatic_repair_rounds": 1, "result": result, "scope": "mock model contracts + actual Blender; not real AI reconstruction"})
    print("INTEGRATION PASSED", flush=True)


if __name__ == "__main__":
    main()
