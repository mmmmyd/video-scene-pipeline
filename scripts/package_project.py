"""Package the one current handoff project; no historical runs, credentials or caches."""
import hashlib
import json
from pathlib import Path
import zipfile


root = Path(__file__).resolve().parents[1]
destination = root.parent / "modeling_pipeline_handoff.zip"
excluded = {"outputs", "runs", "dist", "release", "__pycache__", ".pytest_cache", ".git"}
files = []
for file in sorted(root.rglob("*")):
    relative = file.relative_to(root)
    if not file.is_file() or any(p in excluded or p.endswith(".egg-info") for p in relative.parts):
        continue
    if file.name == "HANDOFF_MANIFEST.json" or file.name.startswith(".env") or file.suffix in {".pyc", ".key"}:
        continue
    files.append(file)
manifest = {"version": "1.0.0", "entry": "start.py", "configuration": "project.json", "files": {
    file.relative_to(root).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest() for file in files}}
manifest_path = root / "HANDOFF_MANIFEST.json"
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
    for file in files + [manifest_path]:
        archive.write(file, Path(root.name) / file.relative_to(root))
with zipfile.ZipFile(destination) as archive:
    if archive.testzip() is not None:
        raise RuntimeError("Archive verification failed")
print(destination)
