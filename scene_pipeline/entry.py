"""Public entry used by start.py, python -m scene_pipeline and scene-pipeline."""
import argparse
import json
from pathlib import Path

from .common import write_json
from .launcher import preflight, project_template, run_project


def main():
    parser = argparse.ArgumentParser(description="视频→大模型分析/建模→Blender→多模态文件，统一入口")
    parser.add_argument("--config", default=str(Path(__file__).resolve().parents[1] / "project.json"))
    parser.add_argument("--check", action="store_true", help="只检查配置、密钥变量、视频和本地依赖，不调用模型")
    parser.add_argument("--init-config", action="store_true", help="创建配置模板，不覆盖现有配置")
    args = parser.parse_args()
    try:
        if args.init_config:
            if Path(args.config).exists():
                raise FileExistsError("配置已存在，请直接编辑")
            write_json(args.config, project_template())
            print(args.config)
            return
        settings = preflight(args.config)
        print(json.dumps({"models": {kind: {"profile": name, "provider": settings["models"][name]["provider"], "model": settings["models"][name]["model"]}
                                    for kind, name in settings["roles"].items()}, "videos": settings["input"]["videos"],
                          "output": settings["_output"], "coverage": settings["policy"]["video_coverage"]}, ensure_ascii=False, indent=2), flush=True)
        if args.check:
            print("配置检查通过；未调用模型。")
            return
        run_project(settings)
    except Exception as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)
