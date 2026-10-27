import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from scene_pipeline.common import write_json
from scene_pipeline.launcher import project_template, preflight
from scene_pipeline.providers import call_model, materialize_files


class Providers(unittest.TestCase):
    def context(self, root, transport="chat_completions"):
        source = root / "source.png"
        Image.new("RGB", (32, 24), "gray").save(source)
        write_json(root / "inputs" / "manifest.json", {"videos":[{"frames":[{"file":"source.png"}]}]})
        profile = {"provider":"test service", "model":"test model", "transport":transport,
                   "endpoint":"https://test.invalid/v1/chat/completions", "api_key_env":"TEST_SCENE_KEY", "max_images":10}
        cfg = {"automation":{"models":{"test":profile}, "roles":{"analysis":"test","reference":"test"}, "limits":{"provider_attempts":1}}}
        packet = {"kind":"analysis", "request_id":"request123", "run_root":str(root), "artifacts":[], "prompt":"Return JSON"}
        return cfg, packet

    def test_chat_sends_image_bytes_and_keeps_secret_out_of_logs(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cfg,packet=self.context(root)
            response={"choices":[{"message":{"content":json.dumps({"request_id":"request123","status":"ready"})}}]}
            with patch.dict("os.environ", {"TEST_SCENE_KEY":"temporary_test_secret"}), patch("urllib.request.urlopen",return_value=io.BytesIO(json.dumps(response).encode())) as opening:
                self.assertEqual(call_model(cfg,"analysis",packet)["status"],"ready")
                req=opening.call_args.args[0]
                payload=json.loads(req.data)
                self.assertEqual(payload["model"],"test model")
                self.assertTrue(any(part["type"]=="image_url" for part in payload["messages"][1]["content"]))
            self.assertNotIn("temporary_test_secret",(root/"calls/analysis/request.json").read_text())
            self.assertNotIn("temporary_test_secret",(root/"calls/analysis/provenance.json").read_text())

    def test_image_edit_transport_posts_multipart_and_decodes_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cfg,packet=self.context(root,"image_edits");packet["kind"]="reference"
            buffer=io.BytesIO();Image.new("RGB",(16,16),"blue").save(buffer,format="PNG")
            response={"data":[{"b64_json":base64.b64encode(buffer.getvalue()).decode()}]}
            with patch.dict("os.environ", {"TEST_SCENE_KEY":"temporary_test_secret"}),patch("urllib.request.urlopen",return_value=io.BytesIO(json.dumps(response).encode())) as opening:
                result=call_model(cfg,"reference",packet)
                self.assertTrue(Path(result["images"][0]).is_file())
                self.assertIn(b'name="image[]"',opening.call_args.args[0].data)
                self.assertIn(b'name="model"',opening.call_args.args[0].data)

    def test_json_http_service_receives_task_and_images(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cfg,packet=self.context(root,"json_http")
            response={"request_id":"request123","status":"ready"}
            with patch.dict("os.environ", {"TEST_SCENE_KEY":"temporary_test_secret"}),patch("urllib.request.urlopen",return_value=io.BytesIO(json.dumps(response).encode())) as opening:
                call_model(cfg,"analysis",packet)
                body=json.loads(opening.call_args.args[0].data)
                self.assertEqual(body["task"]["request_id"],"request123")
                self.assertEqual(body["images"][0]["mime_type"],"image/png")

    def test_stale_http_request_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg,packet=self.context(Path(temp))
            response={"choices":[{"message":{"content":'{"request_id":"old"}'}}]}
            with patch.dict("os.environ", {"TEST_SCENE_KEY":"temporary_test_secret"}),patch("urllib.request.urlopen",return_value=io.BytesIO(json.dumps(response).encode())):
                with self.assertRaises(ValueError):
                    call_model(cfg,"analysis",packet)

    def test_generated_files_cannot_escape_run_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            data={"status":"ready","files":[{"path":"../foreign.py","content":"def build(context): return {}"}]}
            with self.assertRaises(ValueError):
                materialize_files(temp,data)

    def test_generated_code_needs_build_and_is_validated_before_write(self):
        with tempfile.TemporaryDirectory() as temp:
            data={"status":"ready","files":[{"path":"generated/a.py","content":"print('wrong contract')"}]}
            with self.assertRaises(ValueError):
                materialize_files(temp,data,"generated/a.py")
            self.assertFalse((Path(temp)/"generated/a.py").exists())

    def test_syntax_error_does_not_partially_write_other_files(self):
        with tempfile.TemporaryDirectory() as temp:
            data={"status":"ready","files":[{"path":"generated/a.py","content":"def build(context): return {}"},
                                              {"path":"generated/b.py","content":"def invalid syntax"}]}
            with self.assertRaises(SyntaxError):
                materialize_files(temp,data,"generated/a.py")
            self.assertFalse((Path(temp)/"generated/a.py").exists())

    def test_startup_template_lists_models_before_input(self):
        cfg=project_template()
        self.assertLess(list(cfg).index("models"),list(cfg).index("input"))
        self.assertIn("reference_review",cfg["roles"])

    def test_startup_rejects_missing_configuration_before_service_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"project.json";write_json(path,project_template())
            with patch("urllib.request.urlopen") as opening:
                with self.assertRaisesRegex(ValueError,"Startup configuration incomplete"):
                    preflight(path)
                opening.assert_not_called()


if __name__=="__main__":
    unittest.main()
