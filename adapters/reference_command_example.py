"""Vendor-neutral reference adapter example.

This imports an actual provider-generated image; it DOES NOT pretend to call image2.5.
Replace generate() with the chosen service SDK, HTTP API or another agent's job runner.
CLI: python reference_command_example.py --request REQUEST --response RESPONSE --image OUTPUT.png
"""
import argparse
import json
from pathlib import Path


def generate(request, image):
    path = Path(image).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    return {"request_id": request["request_id"], "images": [str(path)],
            "provider": "external_artifact_adapter_example", "actual_model": "unknown",
            "notes": ["Only imported a real image artifact. Implement provider submission/polling here."]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--response", required=True)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    req = json.loads(Path(args.request).read_text(encoding="utf-8"))
    Path(args.response).write_text(json.dumps(generate(req, args.image), ensure_ascii=False, indent=2), encoding="utf-8")
