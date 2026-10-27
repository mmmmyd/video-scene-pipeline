import copy
import tempfile
import unittest
from pathlib import Path

from scene_pipeline.accuracy import validate_observations, evaluate_constraints, check_visual_review, validate_roi, material_crops, comparisons
from scene_pipeline.agent_tasks import PROMPTS, prompt_text, task_packet
from scene_pipeline.configuration import config_template
from scene_pipeline.common import write_json, load_config
from scene_pipeline.errors import PendingWork
from scene_pipeline.revisions import revise


def observation():
    parts = [{"id": name, "object": name, "roi": True, "source_evidence_ids": ["frame"],
              "material": {"description": "灰石细颗粒、磨损边缘", "source_evidence_ids": ["frame"]}} for name in ("edge", "tread")]
    constraints = [
        {"id": "down", "type": "height_delta", "a": "edge", "b": "tread", "range": [-.31, -.19], "basis": "可见向下，高度估算", "severity": "blocker", "source_evidence_ids": ["frame"]},
        {"id": "width", "type": "ratio", "a": "edge", "b": "tread", "axis_a": "x", "axis_b": "x", "range": [.8, 1.2], "basis": "可见同宽，容差20%", "severity": "major", "source_evidence_ids": ["frame"]},
    ]
    targets = [{"id": focus, "focus": focus, "parts": ["edge", "tread"], "source_evidence_id": "frame",
                "camera_role": "detail", "source_roi": [0.1, 0.1, 0.9, 0.9], "description": "逐项检查连接、相对比例或石材接缝"}
               for focus in ("connections", "relative_proportions", "negative_obstacle_materials", "multiview")]
    return {"schema_version": "1.0", "status": "ready", "author": "test", "parts": parts,
            "constraints": constraints, "review_targets": targets, "unknowns": []}


def review_pair():
    expected = {key: "bound_value" for key in ("request_id", "model_sha256", "observations_sha256", "audit_sha256", "comparisons_sha256")}
    expected.update(schema_version="1.0", items=[{"target_id": "edge_material", "comparison_id": "comparison_000"}])
    review = copy.deepcopy(expected)
    review.update(status="complete", reviewer={"name": "external_model"}, issues=[])
    review["items"][0].update(verdict="pass", inspected_source=True, inspected_render=True,
                              findings="已查看两张图；可见石材颗粒方向与接缝关系相符", issue_ids=[])
    return review, expected


class Quality(unittest.TestCase):
    def test_complete_observation_contract(self):
        validate_observations(observation(), {"frame": {}}, {"main", "top", "reverse", "detail"})

    def test_missing_material_target_for_one_roi_is_blocked(self):
        data = observation()
        data["review_targets"][2]["parts"] = ["edge"]
        with self.assertRaises(PendingWork):
            validate_observations(data, {"frame": {}}, {"detail"})

    def test_unknown_evidence_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_observations(observation(), {}, {"detail"})

    def test_blocking_topology_unknown_stays_pending(self):
        data = observation()
        data["unknowns"] = [{"description": "入口后是否下行不明确", "blocking": True}]
        with self.assertRaises(PendingWork):
            validate_observations(data, {"frame": {}}, {"detail"})

    def test_model_height_estimate_passes_without_site_exact_scale(self):
        inventory = {"edge": {"min": [0, 0, -.1], "max": [2, 1, 0]},
                     "tread": {"min": [0, 1, -.5], "max": [2, 2, -.25]}}
        self.assertTrue(evaluate_constraints(observation(), inventory, None)["passed"])
        inventory["tread"]["max"][2] = .25
        self.assertFalse(evaluate_constraints(observation(), inventory, None)["passed"])

    def test_proportion_error_blocks_geometry(self):
        inventory = {"edge": {"min": [0, 0, -.1], "max": [2, 1, 0]},
                     "tread": {"min": [0, 1, -.5], "max": [.5, 2, -.25]}}
        result = evaluate_constraints(observation(), inventory, None)
        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"][1]["passed"])

    def test_opening_probe_catches_covering_ground(self):
        data = observation()
        data["constraints"] = [{"id": "hole", "type": "opening_clear", "severity": "blocker",
                                "probes": [{"origin": [0, 0, .1], "max_hit_z": -.1}]}]
        result = evaluate_constraints(data, {}, lambda *_: [0, 0, 0])
        self.assertFalse(result["passed"])
        self.assertTrue(evaluate_constraints(data, {}, lambda *_: [0, 0, -.25])["passed"])

    def test_bbox_contact_is_labeled_as_coarse(self):
        data = observation()
        data["constraints"] = [{"id": "join", "type": "touching_bounds", "a": "edge", "b": "tread", "severity": "major"}]
        inventory = {"edge": {"min": [0, 0, 0], "max": [1, 1, 1]}, "tread": {"min": [1, 0, 0], "max": [2, 1, 1]}}
        result = evaluate_constraints(data, inventory, None)
        self.assertTrue(result["passed"])
        self.assertIn("AABB", result["checks"][0]["scope"])

    def test_complete_visual_review_passes(self):
        review, expected = review_pair()
        self.assertTrue(check_visual_review(review, expected)["passed"])

    def test_all_true_booleans_cannot_replace_per_target_rows(self):
        review, expected = review_pair()
        review["items"] = []
        review["checks"] = {"connections": True, "negative_obstacle_materials": True}
        with self.assertRaises(PendingWork):
            check_visual_review(review, expected)

    def test_missing_source_inspection_is_blocked(self):
        review, expected = review_pair()
        review["items"][0]["inspected_source"] = False
        with self.assertRaises(PendingWork):
            check_visual_review(review, expected)

    def test_stale_model_review_is_blocked(self):
        review, expected = review_pair()
        review["model_sha256"] = "old_model"
        with self.assertRaises(PendingWork):
            check_visual_review(review, expected)

    def test_uncertain_visual_target_fails_with_repair_details(self):
        review, expected = review_pair()
        review["items"][0].update(verdict="uncertain", issue_ids=["uv"])
        review["issues"] = [{"id": "uv", "severity": "major", "status": "open", "part_ids": ["edge"],
                             "description": "石材接缝方向尚不一致", "repair_instruction": "固定相机后调整顶面UV并重渲染"}]
        self.assertFalse(check_visual_review(review, expected)["passed"])

    def test_request_includes_prompts_and_stable_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg = config_template(temp)
            cfg["_root"] = temp
            write_json(Path(temp) / "config.json", {"input": "same"})
            output = Path(temp) / "quality" / "review.json"
            first = task_packet(cfg, "review", output)
            write_json(output, {"status": "pending"})
            second = task_packet(cfg, "review", output)
            self.assertEqual(first["request_id"], second["request_id"])
            self.assertIn("request_id", first["prompt"])
            self.assertIn("reference_review", PROMPTS)

    def test_cannot_remove_required_views(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg = config_template(temp)
            cfg["quality"]["required_view_roles"] = ["main"]
            file = Path(temp) / "config.json"
            write_json(file, cfg)
            with self.assertRaises(ValueError):
                load_config(file)

    def test_revision_limit_blocks_unbounded_retry(self):
        from scene_pipeline.common import sha256
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model = root / "model" / "scene_editable.blend"
            model.parent.mkdir()
            model.write_bytes(b"test fixture")
            write_json(root / "model" / "report.json", {"sha256": sha256(model), "scene": "test"})
            cfg = config_template(root)
            cfg.update(_root=temp, _config_path=str(root / "config.json"))
            cfg["revision"] = {"round": 3}
            with self.assertRaises(PendingWork):
                revise(cfg, root / "next", ["unused.py"])

    def test_invalid_texture_roi_is_rejected(self):
        for box in ([0, 0, 2, 1], [1, 0, 0, 1], [0, 0, float("nan"), 1], None):
            with self.assertRaises(ValueError):
                validate_roi(box)

    def test_source_texture_crops_and_comparison_boards_are_real_files(self):
        from PIL import Image
        from scene_pipeline.common import sha256
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "frame"
            Image.new("RGB", (100, 80), "gray").save(source, format="PNG")
            render = root / "model" / "view.png"
            render.parent.mkdir()
            Image.new("RGB", (160, 90), "gray").save(render)
            data = observation()
            write_json(root / "analysis" / "observations.json", data)
            material_crops(root, data)
            self.assertTrue((root / "analysis" / "roi_crops" / "roi_000.png").is_file())
            write_json(root / "model" / "review_views" / "manifest.json", {"model_sha256": "model",
                       "images": [{"role": role, "file": "model/view.png", "sha256": sha256(render)} for role in ("main", "top", "reverse", "detail")]})
            cfg = config_template(temp)
            cfg["_root"] = temp
            manifest = comparisons(cfg, data)
            self.assertEqual(len(manifest["comparisons"]), 4)
            for row in manifest["comparisons"]:
                self.assertEqual(row["alignment"], "unregistered")
                self.assertTrue((root / row["board"]).is_file())

    def test_visual_gate_can_resume_and_rejects_tampered_board(self):
        from unittest.mock import patch
        from PIL import Image
        from scene_pipeline.accuracy import quality, verify_gate
        from scene_pipeline.common import read_json, sha256
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            Image.new("RGB", (100, 80), "gray").save(root / "frame", format="PNG")
            model = root / "model" / "scene_editable.blend"
            model.parent.mkdir()
            model.write_bytes(b"unit test model placeholder; Blender call is explicitly mocked")
            render = root / "model" / "view.png"
            Image.new("RGB", (160, 90), "gray").save(render)
            data = observation()
            write_json(root / "analysis" / "observations.json", data)
            write_json(root / "inputs" / "manifest.json", {"videos":[{"frames":[{"file":"frame", "sha256":sha256(root/'frame')}]}]})
            write_json(root / "analysis" / "validated.json", {"observations_sha256":sha256(root/'analysis/observations.json'),
                       "input_manifest_sha256":sha256(root/'inputs/manifest.json'), "fixture_only":False})
            write_json(root / "model" / "report.json", {"sha256":sha256(model)})
            write_json(root / "model" / "review_views" / "manifest.json", {"model_sha256":sha256(model),
                       "images":[{"role":role,"file":"model/view.png","sha256":sha256(render)} for role in ("main","top","reverse","detail")]})
            write_json(root / "quality" / "audit.json", {"passed":True,"checks":[]})
            cfg = config_template(temp)
            cfg["_root"] = temp
            with patch("scene_pipeline.blender_bridge.run_blender"):
                with self.assertRaises(PendingWork):
                    quality(cfg)
                review = read_json(root / "tasks" / "review" / "response.template.json")
                review.update(status="complete", reviewer={"name":"mock reviewer for contract test"})
                for row in review["items"]:
                    row.update(verdict="pass", inspected_source=True, inspected_render=True, findings="Mock review response, not a real semantic assessment")
                write_json(root / "quality" / "review.json", review)
                self.assertTrue(quality(cfg)["passed"])
            self.assertTrue(verify_gate(cfg)["passed"])
            (root / "quality" / "comparisons" / "comparison_000.png").write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                verify_gate(cfg)


if __name__ == "__main__":
    unittest.main()
