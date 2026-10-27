import hashlib
import json
import math
from pathlib import Path


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(base, value):
    path = Path(value)
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")


def load_config(path):
    path = Path(path).resolve()
    cfg = read_json(path)
    if cfg.get("schema_version") != "1.0":
        raise ValueError("Unsupported schema_version")
    if cfg["model"]["mode"] not in {"existing", "scripts"}:
        raise ValueError("model.mode must be existing or scripts")
    if cfg["schema_version"] == "1.0":
        if set(cfg.get("quality", {}).get("required_view_roles", [])) != {"main", "top", "reverse", "detail"}:
            raise ValueError("Current schema requires main/top/reverse/detail review view roles")
        limit = cfg["quality"].get("max_revisions")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
            raise ValueError("quality.max_revisions must be a nonnegative integer")
    if cfg["scale"]["unit"] not in {"estimated_m", "m"}:
        raise ValueError("scale.unit must be estimated_m or m")
    if cfg["scale"]["unit"] == "m" and not cfg["scale"].get("measurement_evidence"):
        raise ValueError("Measured meters require measurement_evidence")
    for name in ("width", "height", "samples"):
        positive(cfg["render"][name], "render." + name)
        if not isinstance(cfg["render"][name], int):
            raise ValueError(f"render.{name} must be integer")
    if cfg["render"]["width"] % 2 or cfg["render"]["height"] % 2:
        raise ValueError("Video output width and height must be even")
    positive(cfg["ingest"]["interval_s"], "ingest.interval_s")
    ids = set()
    if not cfg["trajectories"]:
        raise ValueError("At least one trajectory is required")
    for track in cfg["trajectories"]:
        name = track["id"]
        if not name or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in name) or name in ids:
            raise ValueError("Trajectory IDs must be unique ASCII path components")
        ids.add(name)
        for key in ("duration_s", "fps", "travel"):
            positive(track[key], name + "." + key)
        if not isinstance(track["fps"], int):
            raise ValueError("fps must be integer")
        count = track["duration_s"] * track["fps"]
        if abs(count - round(count)) > 1e-7 or count < 2:
            raise ValueError("duration_s * fps must be an integer >= 2")
        if track["direction"] not in {"approach", "retreat"}:
            raise ValueError("direction must be approach or retreat")
    cfg["_config_path"] = str(path)
    cfg["_root"] = str(path.parent)
    return cfg


def input_fingerprint(cfg):
    """Lock run inputs; changing geometry/reference/config requires a new run."""
    plain = {k: v for k, v in cfg.items() if not k.startswith("_")}
    files = list(cfg["videos"])
    model = cfg["model"]
    # Stage scripts are written by the modeling agent AFTER ingest.
    # They are locked separately when the model stage finishes.
    files.extend([model["source"]] if model["mode"] == "existing" else [])
    ref = cfg["references"]
    if ref.get("source"):
        files.append(ref["source"])
    hashes = {}
    for value in files:
        p = resolve(Path(cfg["_root"]), value)
        if not p.is_file():
            raise FileNotFoundError(p)
        hashes[str(p)] = sha256(p)
    payload = json.dumps({"config": plain, "inputs": hashes}, sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()
