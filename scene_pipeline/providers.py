"""Configured model transports. Credentials stay in environment variables, never task logs."""
import base64
import ast
import io
import json
import mimetypes
import os
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from PIL import Image, ImageDraw

from .common import read_json, write_json


def role_profile(cfg, kind):
    settings = cfg["automation"]
    name = settings["roles"][kind]
    return name, settings["models"][name]


def context_bundle(packet, profile):
    root = Path(packet["run_root"])
    context = []
    for artifact in packet.get("artifacts", []):
        path = Path(artifact["file"])
        if path.suffix not in {".json", ".py", ".md", ".log"}:
            continue
        value = read_json(path) if path.suffix == ".json" else path.read_text(encoding="utf-8")
        # Source inventories can be large. Include all names/types, bounding info is available in artifact.
        if path.name == "source_inventory.json":
            value = {**value, "objects": [{"name": x["name"], "type": x["type"], "materials": x.get("materials", [])} for x in value["objects"]]}
        context.append({"file": str(path), "data": value})
    images = []
    kind = packet.get("kind", packet.get("task", "reference"))
    if kind == "review":
        comp = read_json(root / "quality" / "comparisons" / "manifest.json")
        for item in comp["comparisons"]:
            images.extend([(item["source"], "source:" + item["target_id"]), (item["render"], "render:" + item["target_id"])])
    else:
        index = root / "inputs" / "manifest.json"
        if index.is_file():
            for video in read_json(index)["videos"]:
                images.extend((f["file"], f["file"]) for f in video["frames"])
        for relative, key in (("references/manifest.json", "images"), ("analysis/roi_crops/manifest.json", "crops")):
            file = root / relative
            if file.is_file():
                images.extend((x["file"], x.get("target_id", x["file"])) for x in read_json(file).get(key, []))
    unique = []
    seen = set()
    for value, label in images:
        path = (root / value).resolve()
        if path not in seen:
            seen.add(path)
            unique.append((path, label))
    if len(unique) > profile.get("max_images", 80):
        raise ValueError(f"Task needs {len(unique)} images; raise configured max_images or adjust extraction interval")
    text = json.dumps({"request": packet, "context": context}, ensure_ascii=False)
    if len(text) > profile.get("max_context_chars", 600000):
        raise ValueError("Model task exceeds max_context_chars; select a larger context or simplify source inventory")
    return text, unique


def call_model(cfg, kind, packet):
    name, profile = role_profile(cfg, kind)
    root = Path(packet["run_root"])
    call_dir = root / "calls" / kind
    call_dir.mkdir(parents=True, exist_ok=True)
    request = call_dir / "request.json"
    response = call_dir / "response.json"
    write_json(request, packet)
    # A cache is keyed by request_id; a previous round's response never satisfies a new task.
    if response.is_file():
        cached = read_json(response)
        if cached.get("request_id") == packet["request_id"]:
            return cached
    attempts = cfg["automation"]["limits"]["provider_attempts"]
    for attempt in range(attempts):
        try:
            if profile["transport"] == "command":
                argv = [sys.executable if x == "{python}" else x.replace("{request}", str(request)).replace("{response}", str(response)) for x in profile["command"]]
                # Existing draft output is not a successful response to this invocation.
                with (call_dir / "provider.log").open("w", encoding="utf-8") as log:
                    subprocess.run(argv, cwd=root, stdout=log, stderr=subprocess.STDOUT, timeout=profile.get("timeout_s", 600), check=True)
                data = read_json(response)
            elif profile["transport"] == "chat_completions":
                text, images = context_bundle(packet, profile)
                content = [{"type": "text", "text": text}]
                for path, label in images:
                    with Image.open(path) as image:
                        image = image.convert("RGB")
                        image.thumbnail((profile.get("image_max_side", 1280),) * 2)
                        buffer = io.BytesIO()
                        image.save(buffer, format="JPEG", quality=90)
                    content.extend([{"type": "text", "text": label}, {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")}}])
                payload = {"model": profile["model"], "messages": [
                    {"role": "system", "content": "Return one valid JSON object matching the task response_template. Never claim viewing original video when only sampled frames are supplied. Python file contents belong in JSON files[].content, not Markdown fences."},
                    {"role": "user", "content": content}], "stream": False}
                payload.update(profile.get("parameters", {}))
                headers = {"Content-Type": "application/json"}
                key_env = profile.get("api_key_env")
                if key_env:
                    headers["Authorization"] = "Bearer " + os.environ[key_env]
                req = urllib.request.Request(profile["endpoint"], data=json.dumps(payload).encode(), headers=headers, method="POST")
                try:
                    with urllib.request.urlopen(req, timeout=profile.get("timeout_s", 600)) as stream:
                        raw = json.load(stream)
                except urllib.error.HTTPError as exc:
                    raise RuntimeError(f"Model profile {name} returned HTTP {exc.code}; check endpoint/model/account access") from None
                message = raw["choices"][0]["message"]["content"]
                if isinstance(message, list):
                    message = "".join(x.get("text", "") for x in message)
                message = message.strip()
                if message.startswith("```"):
                    message = message.split("\n", 1)[1].rsplit("```", 1)[0]
                data = json.loads(message)
            elif profile["transport"] == "image_edits":
                _, images = context_bundle(packet, profile)
                if not images:
                    raise ValueError("Image reference generation requires source video frames")
                collages = []
                for offset in range(0, len(images), 16):
                    board = Image.new("RGB", (1280, 1040), "#20242a")
                    draw = ImageDraw.Draw(board)
                    for i, (path, _) in enumerate(images[offset:offset + 16]):
                        x, y = i % 4 * 320, i // 4 * 260
                        with Image.open(path) as source:
                            source = source.convert("RGB")
                            source.thumbnail((310, 230))
                            board.paste(source, (x + 5, y + 25))
                        draw.text((x + 5, y + 5), f"source_{offset+i:03d}", fill="white")
                    buffer = io.BytesIO()
                    board.save(buffer, format="PNG")
                    collages.append(buffer.getvalue())
                boundary = "scene_" + uuid.uuid4().hex
                chunks = []
                fields = {"model": profile["model"], "prompt": packet.get("prompt", packet.get("brief", "")) + "\n" + json.dumps(packet.get("observations", {}), ensure_ascii=False), **profile.get("parameters", {})}
                for key, value in fields.items():
                    chunks.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode())
                for i, blob in enumerate(collages):
                    chunks.extend([(f'--{boundary}\r\nContent-Disposition: form-data; name="image[]"; filename="source_{i:02d}.png"\r\nContent-Type: image/png\r\n\r\n').encode(), blob, b"\r\n"])
                chunks.append(f"--{boundary}--\r\n".encode())
                headers = {"Content-Type": "multipart/form-data; boundary=" + boundary}
                if profile.get("api_key_env"):
                    headers["Authorization"] = "Bearer " + os.environ[profile["api_key_env"]]
                req = urllib.request.Request(profile["endpoint"], data=b"".join(chunks), headers=headers, method="POST")
                try:
                    with urllib.request.urlopen(req, timeout=profile.get("timeout_s", 600)) as stream:
                        raw = json.load(stream)
                except urllib.error.HTTPError as exc:
                    raise RuntimeError(f"Image profile {name} returned HTTP {exc.code}; check the configured image model and endpoint") from None
                data = {"request_id": packet["request_id"], "images_base64": [item["b64_json"] for item in raw["data"]],
                        "provider": profile["provider"], "actual_model": profile["model"], "notes": ["source frames were supplied as indexed contact sheets"]}
            elif profile["transport"] == "json_http":
                # Service-side adapter contract for image tools or non-chat APIs.
                text, images = context_bundle(packet, profile)
                attachments = [{"label": label, "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream", "base64": base64.b64encode(path.read_bytes()).decode()} for path, label in images]
                payload = {"model": profile["model"], "task": packet, "context": json.loads(text)["context"], "images": attachments}
                headers = {"Content-Type": "application/json"}
                if profile.get("api_key_env"):
                    headers["Authorization"] = "Bearer " + os.environ[profile["api_key_env"]]
                req = urllib.request.Request(profile["endpoint"], data=json.dumps(payload).encode(), headers=headers, method="POST")
                try:
                    with urllib.request.urlopen(req, timeout=profile.get("timeout_s", 600)) as stream:
                        data = json.load(stream)
                except urllib.error.HTTPError as exc:
                    raise RuntimeError(f"Model profile {name} returned HTTP {exc.code}") from None
            else:
                raise ValueError(f"Unsupported transport: {profile['transport']}")
            if not isinstance(data, dict) or data.get("request_id") != packet["request_id"]:
                raise ValueError("Provider response must return this task's request_id")
            # JSON HTTP image adapters may return bytes instead of a shared local path.
            if data.get("images_base64"):
                files = []
                for i, encoded in enumerate(data.pop("images_base64")):
                    file = call_dir / f"generated_{i:02d}.png"
                    with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
                        image.save(file, format="PNG")
                    files.append(str(file))
                data["images"] = files
            write_json(response, data)
            write_json(call_dir / "provenance.json", {"profile": name, "provider": profile["provider"], "model": profile["model"],
                       "transport": profile["transport"], "request_id": packet["request_id"], "attempt": attempt + 1})
            return data
        except (OSError, ValueError, KeyError, subprocess.SubprocessError, RuntimeError):
            if attempt + 1 == attempts:
                raise


def materialize_files(root, data, expected=None):
    if data.get("status") != "ready" or not data.get("files"):
        raise ValueError("Code generation requires status=ready and nonempty files[]")
    root = Path(root).resolve()
    prepared = []
    for entry in data["files"]:
        path = (root / entry["path"]).resolve()
        generated = root / "generated"
        if not path.is_relative_to(generated) or path.suffix not in {".py", ".json"}:
            raise ValueError("Generated text files must be .py/.json under the current run's generated/ directory")
        content = entry["content"]
        if path.suffix == ".py":
            compile(content, str(path), "exec")
            if expected and path == (root / expected).resolve() and not any(isinstance(node, ast.FunctionDef) and node.name == "build" for node in ast.parse(content).body):
                raise ValueError("The requested stage module must expose build(context)")
        if path.exists() and path.read_text(encoding="utf-8") != content:
            raise ValueError("Generated file already differs; use a fresh run")
        prepared.append((path, content))
    if expected and not any(path == (root / expected).resolve() for path, _ in prepared):
        raise ValueError(f"Provider did not return required code file: {expected}")
    for path, content in prepared:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return [path for path, _ in prepared]
