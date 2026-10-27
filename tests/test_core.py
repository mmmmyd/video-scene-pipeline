import copy
import json
import tempfile
import unittest
import sys
from unittest.mock import patch
from pathlib import Path

import numpy as np
from PIL import Image

from scene_pipeline.common import input_fingerprint, load_config, sha256, write_json
from scene_pipeline.configuration import config_template
from scene_pipeline.geometry import CV_FROM_BLENDER, backproject, reprojection_error, trajectory_positions, write_ply
from scene_pipeline.workflow import PendingWork, references, require_review, review_template


class Contracts(unittest.TestCase):
    def test_camera_axis_and_projection_roundtrip(self):
        k = np.array([[600., 0, 159.5], [0, 600., 89.5], [0, 0, 1]])
        z = np.full((180, 320), 3., np.float32)
        z[0, 0] = np.nan
        tw = CV_FROM_BLENDER.copy()
        tw[:3, 3] = [2, 3, 1]
        cam, world, uv = backproject(z, k, tw, 3)
        self.assertTrue(np.all(cam[:, 2] == 3))
        self.assertTrue(np.allclose(world[:, 2], -2))
        self.assertLess(reprojection_error(world, uv, k, tw), 1e-10)

    def test_approach_and_retreat_are_different_and_horizontal(self):
        # Blender camera forward is local -Z, here aligned with world +Y.
        mw = np.array([[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 1], [0, 0, 0, 1]], float)
        approach = trajectory_positions(mw, "approach", 1.2, 48)
        retreat = trajectory_positions(mw, "retreat", 1.2, 48)
        self.assertTrue(np.allclose(approach, retreat[::-1]))
        self.assertTrue(np.allclose(approach[-1], mw[:3, 3]))
        self.assertTrue(np.allclose(np.diff(approach, axis=0), [0, 1.2 / 47, 0]))

    def test_vertical_camera_rejected(self):
        with self.assertRaises(ValueError):
            trajectory_positions(np.eye(4), "approach", 1, 10)

    def test_ply_binary_readback(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "points.ply"
            xyz = np.array([[1, 2, 3], [-1, -.5, 8]], np.float32)
            rgb = np.array([[20, 50, 200], [255, 0, 5]], np.uint8)
            write_ply(file, xyz, rgb)
            payload = file.read_bytes().split(b"end_header\n")[1]
            rows = np.frombuffer(payload, dtype=np.dtype([("xyz", "<f4", (3,)), ("rgb", "u1", (3,))]))
            np.testing.assert_array_equal(rows["xyz"], xyz)
            np.testing.assert_array_equal(rows["rgb"], rgb)

    def test_review_is_bound_to_artifact_and_requires_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            review = review_template("model_sha256", "model1")
            review.update(status="accepted", reviewer="independent_agent")
            review["checks"] = {k: True for k in review["checks"]}
            write_json(root / "review.json", review)
            with self.assertRaises(PendingWork):
                require_review(root / "review.json", "model_sha256", "model1", root)
            (root / "real_render.png").write_bytes(b"evidence")
            review["evidence"] = ["real_render.png"]
            write_json(root / "review.json", review)
            require_review(root / "review.json", "model_sha256", "model1", root)
            with self.assertRaises(PendingWork):
                require_review(root / "review.json", "model_sha256", "model2", root)

    @patch("scene_pipeline.accuracy.verified_observations", return_value={"software_test": True})
    def test_reference_handoff_cannot_silently_succeed(self, _verified):
        with tempfile.TemporaryDirectory() as temp:
            cfg = config_template(temp)
            cfg["_root"] = temp
            cfg["model"] = {"mode": "scripts", "scripts": []}
            write_json(Path(temp) / "inputs" / "manifest.json", {"videos": []})
            with self.assertRaises(PendingWork):
                references(cfg)
            self.assertTrue((Path(temp) / "references" / "request.json").is_file())

    @patch("scene_pipeline.accuracy.verified_observations", return_value={"software_test": True})
    def test_provider_request_id_must_match(self, _verified):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cfg = config_template(temp)
            cfg["_root"] = temp
            write_json(root / "inputs" / "manifest.json", {"videos": []})
            write_json(root / "references" / "response.json", {"request_id": "stale", "images": ["x.png"]})
            with self.assertRaisesRegex(ValueError, "request_id"):
                references(cfg)

    @patch("scene_pipeline.accuracy.verified_observations", return_value={"software_test": True})
    def test_external_command_adapter_roundtrip(self, _verified):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / "external_reference.png"
            Image.new("RGB", (16, 16), "gray").save(image)
            adapter = Path(__file__).resolve().parents[1] / "adapters" / "reference_command_example.py"
            cfg = config_template(temp)
            cfg["_root"] = temp
            cfg["references"].update(provider="command", command=[sys.executable, str(adapter),
                "--request", "{request}", "--response", "{response}", "--image", str(image)])
            write_json(root / "inputs" / "manifest.json", {"videos": []})
            with self.assertRaises(PendingWork):
                references(cfg)
            manifest = json.loads((root / "references" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["actual_model"], "unknown")
            self.assertEqual(len(manifest["images"]), 1)
            self.assertEqual(manifest["images"][0]["sha256"], sha256(image))

    def test_scripts_editable_before_model_and_reviews_do_not_relock_run(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg = config_template(temp)
            cfg["_root"] = temp
            cfg["model"] = {"mode": "scripts", "scripts": ["generated/build.py"]}
            original = input_fingerprint(cfg)
            (Path(temp) / "generated").mkdir()
            (Path(temp) / "generated/build.py").write_text("def build(context): return {}", encoding="utf-8")
            write_json(Path(temp) / "model" / "review.json", {"status": "accepted"})
            self.assertEqual(original, input_fingerprint(cfg))
            cfg["trajectories"][0]["travel"] += 1
            self.assertNotEqual(original, input_fingerprint(cfg))

    def test_nonfinite_trajectory_duration_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg = config_template(temp)
            cfg["trajectories"][0]["duration_s"] = float("inf")
            file = Path(temp) / "config.json"
            file.write_text(json.dumps(cfg), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(file)


if __name__ == "__main__":
    unittest.main()
