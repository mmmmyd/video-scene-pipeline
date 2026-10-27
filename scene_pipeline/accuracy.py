"""Evidence/constraint contracts, measured geometry checks and explicit visual gates.

Numerical geometry checks do not claim visual similarity or measured site accuracy.
"""
import math
from pathlib import Path

import numpy as np

from .agent_tasks import dispatch, task_packet
from .common import read_json, resolve, sha256, write_json
from .errors import PendingWork

FOCUS = {"connections", "relative_proportions", "negative_obstacle_materials", "multiview"}
TYPES = {"height_delta", "ratio", "touching_bounds", "opening_clear"}


def observations_template():
    return {"schema_version": "1.0", "request_id": "copy_from_request", "status": "draft", "author": "",
            "video_coverage": [{"video": "absolute/source.mp4", "watched_full": False, "notes": "实际覆盖范围"}],
            "parts": [{"id": "edge", "object": "edge_mesh", "roi": True, "role": "negative_edge",
                       "source_evidence_ids": ["inputs/video_00/frame_000000.png"],
                       "material": {"description": "颜色/颗粒/接缝/粗糙度/磨损/反射", "source_evidence_ids": ["inputs/video_00/frame_000000.png"]}}],
            "constraints": [{"id": "downward", "type": "height_delta", "a": "edge", "b": "lower_surface",
                             "range": [-.4, -.1], "basis": "视频高差方向与比例估算", "severity": "blocker", "source_evidence_ids": ["inputs/video_00/frame_000000.png"]}],
            "review_targets": [{"id": "edge_material", "focus": "negative_obstacle_materials", "parts": ["edge"],
                                "source_evidence_id": "inputs/video_00/frame_000000.png", "camera_role": "detail",
                                "source_roi": [0.1, 0.2, 0.8, 0.7], "description": "核对石材纹理尺度和接缝"}], "unknowns": []}


def validate_roi(box):
    if not isinstance(box, list) or len(box) != 4 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in box):
        raise ValueError("ROI must be normalized [x0,y0,x1,y1]")
    if not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
        raise ValueError("ROI coordinates must lie within [0,1] with positive area")


def crop_image(image, box):
    if box is None:
        return image.copy()
    validate_roi(box)
    width, height = image.size
    pixel = [round(box[0] * width), round(box[1] * height), round(box[2] * width), round(box[3] * height)]
    if pixel[0] >= pixel[2] or pixel[1] >= pixel[3]:
        raise ValueError("ROI is smaller than one source pixel")
    return image.crop(pixel)


def material_crops(root, observations):
    from PIL import Image
    out = root / "analysis" / "roi_crops"
    out.mkdir(parents=True, exist_ok=True)
    records = []
    for target in observations["review_targets"]:
        if target["focus"] != "negative_obstacle_materials":
            continue
        source = root / target["source_evidence_id"]
        destination = out / f"roi_{len(records):03d}.png"
        with Image.open(source) as image:
            crop_image(image.convert("RGB"), target["source_roi"]).save(destination)
        records.append({"target_id": target["id"], "parts": target["parts"], "source": target["source_evidence_id"],
                        "source_sha256": sha256(source), "source_roi": target["source_roi"],
                        "file": destination.relative_to(root).as_posix(), "sha256": sha256(destination),
                        "scope": "raw video appearance crop; perspective/lighting not removed; not certified PBR texture"})
    write_json(out / "manifest.json", {"crops": records})


def evidence_index(root):
    manifest = read_json(root / "inputs" / "manifest.json")
    result = {}
    for video in manifest["videos"]:
        for frame in video["frames"]:
            file = root / frame["file"]
            if sha256(file) != frame["sha256"]:
                raise ValueError(f"Source frame changed: {file}")
            result[frame["file"]] = frame
    return result


def validate_observations(data, evidence, required_roles):
    if data.get("schema_version") != "1.0" or data.get("status") != "ready" or not data.get("author"):
        raise PendingWork("Analysis requires schema_version=0.2, status=ready and author")
    if not data.get("parts") or not data.get("constraints"):
        raise PendingWork("Analysis must enumerate parts and explicit geometry constraints")
    def evidence_refs(items):
        if not items or any(value not in evidence for value in items):
            raise ValueError(f"Unknown/missing source evidence IDs: {items}")
    parts = {}
    for part in data["parts"]:
        if not part.get("id") or part["id"] in parts or not part.get("object"):
            raise ValueError("Part IDs must be unique and map to concrete Blender objects")
        parts[part["id"]] = part
        evidence_refs(part.get("source_evidence_ids"))
        if part.get("roi"):
            if not part.get("material", {}).get("description", "").strip():
                raise PendingWork(f"Missing ROI material observation: {part['id']}")
            evidence_refs(part["material"].get("source_evidence_ids"))
    if not any(p.get("roi") for p in parts.values()):
        raise PendingWork("At least one negative-obstacle ROI part is required")
    seen = set()
    for constraint in data["constraints"]:
        if not constraint.get("id") or constraint["id"] in seen or constraint.get("type") not in TYPES:
            raise ValueError("Constraints require unique IDs and a supported type")
        seen.add(constraint["id"])
        names = constraint.get("parts", []) if constraint["type"] == "opening_clear" else [constraint.get("a"), constraint.get("b")]
        if not names or any(name not in parts for name in names):
            raise ValueError(f"Constraint references unknown parts: {constraint['id']}")
        evidence_refs(constraint.get("source_evidence_ids"))
        if not constraint.get("basis", "").strip():
            raise PendingWork("Each numerical constraint needs an evidence-based tolerance/range rationale")
        if constraint.get("severity") not in {"blocker", "major", "minor"}:
            raise ValueError("Constraint must declare severity")
        if constraint["type"] in {"height_delta", "ratio"}:
            bounds = constraint.get("range", [])
            if len(bounds) != 2 or not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in bounds) or bounds[0] > bounds[1]:
                raise ValueError("Constraint range must be finite [min,max]")
        if constraint["type"] == "ratio" and (constraint.get("axis_a") not in {"x", "y", "z"} or constraint.get("axis_b") not in {"x", "y", "z"}):
            raise ValueError("Ratio axes must be x/y/z")
    kinds = {constraint["type"] for constraint in data["constraints"]}
    if "ratio" not in kinds or not kinds.intersection({"height_delta", "opening_clear"}):
        raise PendingWork("Constraints must cover relative proportions and negative height/opening structure")
    targets = data.get("review_targets", [])
    if not targets or {t.get("focus") for t in targets} != FOCUS:
        raise PendingWork("Review targets must cover all four focus categories")
    target_ids = set()
    material_parts = set()
    for target in targets:
        if not target.get("id") or target["id"] in target_ids:
            raise ValueError("Review target IDs must be unique")
        target_ids.add(target["id"])
        evidence_refs([target.get("source_evidence_id")])
        if target.get("camera_role") not in required_roles or not target.get("description", "").strip():
            raise ValueError("Review target needs an available camera role and a concrete description")
        if not target.get("parts") or any(p not in parts for p in target["parts"]):
            raise ValueError("Review target must reference known parts")
        if target["focus"] == "negative_obstacle_materials":
            validate_roi(target.get("source_roi"))
            material_parts.update(target["parts"])
        if target.get("render_roi") is not None:
            validate_roi(target["render_roi"])
    missing = {p["id"] for p in parts.values() if p.get("roi")} - material_parts
    if missing:
        raise PendingWork(f"ROI parts without material inspection targets: {sorted(missing)}")
    if any(x.get("blocking") for x in data.get("unknowns", [])):
        raise PendingWork("Blocking topology/connection unknowns require additional evidence")


def analyze(cfg):
    root = Path(cfg["_root"])
    path = resolve(root, cfg["quality"]["observations"])
    if cfg["model"]["mode"] == "existing" and not (root / "analysis" / "source_inventory.json").exists():
        from .blender_bridge import run_blender
        run_blender(cfg, {"action": "inspect", "config": cfg}, resolve(root, cfg["model"]["source"]))
    packet = task_packet(cfg, "analysis", path, observations_template())
    data = dispatch(cfg, "analysis", packet)
    if data.get("request_id") != packet["request_id"]:
        raise PendingWork("Analysis response belongs to a different request; inspect current task packet")
    validate_observations(data, evidence_index(root), cfg["quality"]["required_view_roles"])
    videos = read_json(root / "inputs" / "manifest.json")["videos"]
    coverage = {str(resolve(root, v["video"])): v for v in data.get("video_coverage", [])}
    sampled = cfg.get("automation", {}).get("policy", {}).get("video_coverage") == "sampled_frames"
    if sampled:
        missing = [v["path"] for v in videos if v["path"] not in coverage or
                   not ((coverage[v["path"]].get("watched_full") is True) or
                        (coverage[v["path"]].get("mode") == "sampled_frames" and coverage[v["path"]].get("notes")))]
    else:
        missing = [v["path"] for v in videos if v["path"] not in coverage or coverage[v["path"]].get("watched_full") is not True]
    if not videos or missing:
        raise PendingWork("Analysis must record full-video coverage; incomplete viewing remains pending")
    material_crops(root, data)
    result = {"observations_sha256": sha256(path), "input_manifest_sha256": sha256(root / "inputs" / "manifest.json"),
              "fixture_only": False}
    write_json(root / "analysis" / "validated.json", result)
    return result


def verified_observations(cfg):
    root = Path(cfg["_root"])
    stamp = read_json(root / "analysis" / "validated.json")
    path = resolve(root, cfg["quality"]["observations"])
    if sha256(path) != stamp["observations_sha256"] or sha256(root / "inputs" / "manifest.json") != stamp["input_manifest_sha256"]:
        raise ValueError("Evidence/observations changed after analysis; create a new run")
    validate_observations(read_json(path), evidence_index(root), cfg["quality"]["required_view_roles"])
    return read_json(path)


def evaluate_constraints(observations, inventory, raycast):
    """Use evaluated mesh world bounds and explicit BVH probes, never assumed dimensions."""
    mapped = {part["id"]: inventory.get(part["object"]) for part in observations["parts"]}
    results = []
    for item in observations["constraints"]:
        kind = item["type"]
        record = {"id": item["id"], "type": kind, "severity": item["severity"], "passed": False}
        try:
            if kind in {"height_delta", "ratio", "touching_bounds"}:
                a, b = mapped[item["a"]], mapped[item["b"]]
                if a is None or b is None:
                    raise ValueError("Required evaluated object is missing")
                amin, amax = np.asarray(a["min"]), np.asarray(a["max"])
                bmin, bmax = np.asarray(b["min"]), np.asarray(b["max"])
                if kind == "height_delta":
                    value = float(bmax[2] - amax[2])
                elif kind == "ratio":
                    numerator = (amax - amin)["xyz".index(item["axis_a"])]
                    denominator = (bmax - bmin)["xyz".index(item["axis_b"])]
                    if denominator <= 1e-9:
                        raise ValueError("Ratio denominator is zero")
                    value = float(numerator / denominator)
                else:
                    gap = np.maximum(0, np.maximum(amin - bmax, bmin - amax))
                    value = float(np.linalg.norm(gap))
                allowed = item.get("range", [0, item.get("tolerance", .005)])
                record.update(value=value, allowed_range=allowed, passed=allowed[0] - 1e-6 <= value <= allowed[1] + 1e-6)
                if kind == "touching_bounds":
                    record["scope"] = "AABB proximity only; overlap/true surface connectivity requires visual or dedicated surface checks"
            elif kind == "opening_clear":
                probes = item.get("probes", [])
                if not probes:
                    raise ValueError("opening_clear requires explicit sample probes")
                hits = []
                for probe in probes:
                    hit = raycast(probe["origin"], [0, 0, -1], probe.get("distance", 20))
                    valid = hit is not None and hit[2] <= probe["max_hit_z"] + 1e-6
                    hits.append({"hit": hit, "max_hit_z": probe["max_hit_z"], "passed": valid})
                record.update(probes=hits, passed=all(h["passed"] for h in hits), scope="only the declared sample rays")
        except (KeyError, TypeError, ValueError) as exc:
            record["error"] = str(exc)
        results.append(record)
    return {"passed": bool(results) and all(r["passed"] for r in results), "checks": results,
            "scope": "model parameter/geometry checks; no assertion of measured site scale or material fidelity"}


def comparisons(cfg, observations):
    from PIL import Image, ImageDraw, ImageOps
    root = Path(cfg["_root"])
    views = read_json(root / "model" / "review_views" / "manifest.json")
    by_role = {view["role"]: view for view in views["images"]}
    if not set(cfg["quality"]["required_view_roles"]) <= set(by_role):
        raise ValueError("Missing required model review views")
    out = root / "quality" / "comparisons"
    out.mkdir(parents=True, exist_ok=True)
    records = []
    for index, target in enumerate(observations["review_targets"]):
        source = root / target["source_evidence_id"]
        view = by_role[target["camera_role"]]
        render = root / view["file"]
        if sha256(render) != view["sha256"]:
            raise ValueError("Model review render changed")
        crops = []
        for path, key in ((source, "source_roi"), (render, "render_roi")):
            with Image.open(path) as image:
                crops.append(crop_image(image.convert("RGB"), target.get(key)))
        board = Image.new("RGB", (1280, 440), "#20242a")
        draw = ImageDraw.Draw(board)
        for col, (image, label) in enumerate(zip(crops, ("SOURCE VIDEO ROI", "MODEL RENDER - UNREGISTERED"))):
            fitted = ImageOps.contain(image, (630, 380))
            board.paste(fitted, (col * 640 + (640 - fitted.width) // 2, 42 + (380 - fitted.height) // 2))
            draw.text((col * 640 + 10, 10), label, fill="white")
        file = out / f"comparison_{index:03d}.png"
        board.save(file)
        records.append({"id": f"comparison_{index:03d}", "target_id": target["id"], "focus": target["focus"],
                        "description": target["description"], "source": target["source_evidence_id"], "source_sha256": sha256(source),
                        "render": view["file"], "render_sha256": sha256(render), "camera_role": view["role"],
                        "source_roi": target.get("source_roi"), "render_roi": target.get("render_roi"),
                        "board": file.relative_to(root).as_posix(), "board_sha256": sha256(file), "alignment": "unregistered"})
    manifest = {"model_sha256": views["model_sha256"], "observations_sha256": sha256(resolve(root, cfg["quality"]["observations"])),
                "comparisons": records}
    write_json(out / "manifest.json", manifest)
    return manifest


def review_contract(root, comparison_manifest, packet):
    return {"schema_version": "1.0", "request_id": packet.get("request_id", "copy_from_request"), "status": "pending",
            "reviewer": {"name": "", "provider": "", "model": "unknown"},
            "model_sha256": comparison_manifest["model_sha256"], "observations_sha256": comparison_manifest["observations_sha256"],
            "audit_sha256": sha256(root / "quality" / "audit.json"),
            "comparisons_sha256": sha256(root / "quality" / "comparisons" / "manifest.json"),
            "items": [{"target_id": r["target_id"], "comparison_id": r["id"], "verdict": "uncertain",
                       "inspected_source": False, "inspected_render": False, "findings": "", "issue_ids": []}
                      for r in comparison_manifest["comparisons"]], "issues": []}


def check_visual_review(data, expected):
    for key in ("schema_version", "request_id", "model_sha256", "observations_sha256", "audit_sha256", "comparisons_sha256"):
        if data.get(key) != expected[key]:
            raise PendingWork(f"Review version/request mismatch: {key}")
    if data.get("status") != "complete" or not data.get("reviewer", {}).get("name"):
        raise PendingWork("Visual review needs status=complete and reviewer identity")
    required = {row["target_id"]: row for row in expected["items"]}
    rows = data.get("items", [])
    if len(rows) != len(required) or {row.get("target_id") for row in rows} != set(required):
        raise PendingWork("Visual review must cover every target exactly once")
    issues = data.get("issues", [])
    issue_ids = {item.get("id") for item in issues}
    if None in issue_ids or len(issue_ids) != len(issues):
        raise ValueError("Review issue IDs must be unique and present")
    for issue in issues:
        if issue.get("severity") not in {"blocker", "major", "minor"} or issue.get("status") not in {"open", "resolved"}:
            raise ValueError("Each issue needs severity and explicit status")
        if not issue.get("part_ids") or not issue.get("description", "").strip() or not issue.get("repair_instruction", "").strip():
            raise ValueError("Each issue needs affected parts, concrete difference and repair instruction")
    failures = []
    for row in rows:
        if row.get("comparison_id") != required[row["target_id"]]["comparison_id"]:
            raise PendingWork("Review comparison ID does not match its target")
        if row.get("inspected_source") is not True or row.get("inspected_render") is not True or not row.get("findings", "").strip():
            raise PendingWork("Each review item must record actual source/render inspection and concrete findings")
        if row.get("verdict") not in {"pass", "fail", "uncertain"}:
            raise ValueError("Unknown visual verdict")
        if any(value not in issue_ids for value in row.get("issue_ids", [])):
            raise ValueError("Review item references undefined issue")
        if row["verdict"] != "pass":
            if not row.get("issue_ids"):
                raise PendingWork("Failed/uncertain targets must reference concrete repair issues")
            failures.append(row["target_id"])
    if any(i.get("status") != "resolved" for i in issues):
        failures.append("open_issues")
    return {"passed": not failures, "failed_targets": failures, "scope": "declared visual judgment; image semantics are not programmatically proven"}


def quality(cfg):
    from .blender_bridge import run_blender
    root = Path(cfg["_root"])
    observations = verified_observations(cfg)
    model = root / "model" / "scene_editable.blend"
    report = read_json(root / "model" / "report.json")
    if sha256(model) != report["sha256"]:
        raise ValueError("Model changed after checkpoint; create a revision run")
    run_blender(cfg, {"action": "audit", "config": cfg}, model)
    audit = read_json(root / "quality" / "audit.json")
    if not audit["passed"]:
        task_packet(cfg, "repair", root / "quality" / "repair_plan.json",
                    {"request_id": "copy_from_request", "base_model_sha256": report["sha256"], "status": "draft", "issues": audit["checks"], "scripts": []})
        raise PendingWork("Geometry checks failed; inspect quality/audit.json and tasks/repair/request.json")
    comp = comparisons(cfg, observations)
    path = resolve(root, cfg["quality"]["review"])
    template = review_contract(root, comp, {})
    attempts = cfg.get("automation", {}).get("limits", {}).get("validation_attempts", 1)
    for trial in range(attempts):
        packet = task_packet(cfg, "review", path, template)
        data = dispatch(cfg, "review", packet)
        expected = review_contract(root, comp, packet)
        try:
            result = check_visual_review(data, expected)
            break
        except (PendingWork, ValueError) as exc:
            if trial + 1 == attempts:
                raise
            for file in (path, root / "calls" / "review" / "response.json"):
                if file.exists():
                    file.unlink()
            write_json(root / "tasks" / "review" / "validation_feedback.json", {"error": str(exc), "attempt": trial + 1})
    if not result["passed"]:
        write_json(root / "quality" / "gate.json", result)
        task_packet(cfg, "repair", root / "quality" / "repair_plan.json",
                    {"request_id": "copy_from_request", "base_model_sha256": report["sha256"],
                     "status": "draft", "issues": data["issues"], "failed_targets": result["failed_targets"], "scripts": []})
        raise PendingWork("Visual review failed/uncertain; repair task generated. Create a revision run and re-render")
    result.update(model_sha256=report["sha256"], observations_sha256=comp["observations_sha256"],
                  audit_sha256=expected["audit_sha256"], comparisons_sha256=expected["comparisons_sha256"],
                  review_sha256=sha256(path), fixture_only=False)
    write_json(root / "quality" / "gate.json", result)
    return result


def verify_gate(cfg):
    root = Path(cfg["_root"])
    verified_observations(cfg)
    gate = read_json(root / "quality" / "gate.json")
    if gate.get("passed") is not True or gate["model_sha256"] != sha256(root / "model" / "scene_editable.blend"):
        raise PendingWork("Current model has no passing quality gate")
    files = {"observations_sha256": resolve(root, cfg["quality"]["observations"]),
             "audit_sha256": root / "quality" / "audit.json"}
    files.update(review_sha256=resolve(root, cfg["quality"]["review"]),
                 comparisons_sha256=root / "quality" / "comparisons" / "manifest.json")
    comp = read_json(files["comparisons_sha256"])
    for row in comp["comparisons"]:
        for path_key, hash_key in (("source", "source_sha256"), ("render", "render_sha256"), ("board", "board_sha256")):
            if sha256(root / row[path_key]) != row[hash_key]:
                raise ValueError(f"Quality evidence changed after review: {row[path_key]}")
    for key, path in files.items():
        if sha256(path) != gate[key]:
            raise ValueError(f"Quality gate artifact changed: {key}")
    return gate
