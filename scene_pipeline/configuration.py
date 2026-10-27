"""Current internal run configuration; user-facing settings live in project.json."""


def config_template(workspace=None):
    return {
        "schema_version": "1.0", "case": "automatic_video_scene",
        "tools": {"blender": "blender", "backend": "cli", "mcp_host": "127.0.0.1", "mcp_port": 9876, "timeout_s": 3600},
        "videos": [],
        "ingest": {"interval_s": 1.0, "extra_times_s": {}, "max_frames_per_video": 80},
        "scale": {"unit": "estimated_m", "source": "视频比例估算，最终数据以模型尺度为准", "measurement_evidence": None},
        "references": {"provider": "handoff", "model_label": "image2.5", "source": None, "command": [], "review": None},
        "model": {"mode": "scripts", "source": None, "scene": None, "scripts": [], "review": None},
        "render": {"width": 640, "height": 360, "engine": "CYCLES", "samples": 16, "frame": 1, "seed": 41, "static_scene": True},
        "trajectories": [{"id": "rear_center", "camera": "DATA_rear_center", "duration_s": 3.0, "fps": 24, "travel": 1.2, "direction": "approach", "multimodal_every": 6}],
        "export": {"model_glb": True, "point_stride": 2, "video_codec": "mp4v", "min_valid_fraction": 0.01},
        "agents": {"analysis": {"command": []}, "review": {"command": []}},
        "quality": {"observations": "analysis/observations.json", "review": "quality/review.json",
                    "required_view_roles": ["main", "top", "reverse", "detail"], "max_revisions": 3, "views": []},
    }
