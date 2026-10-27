from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageDraw

from .common import resolve, sha256, write_json


def read_video(path):
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"Cannot open video: {path}")
    return capture


def ingest(cfg):
    root = Path(cfg["_root"])
    records = []
    for video_index, value in enumerate(cfg["videos"]):
        source = resolve(root, value)
        cap = read_video(source)
        try:
            fps = float(cap.get(cv2.CAP_PROP_FPS))
            count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if not np.isfinite(fps) or fps <= 0 or count < 1:
                raise ValueError(f"Video has invalid metadata: {source}")
            duration = count / fps
            times = list(np.arange(0, max(0, (count - 1) / fps), cfg["ingest"]["interval_s"]))
            times += [(count - 1) / fps]
            times += cfg["ingest"].get("extra_times_s", {}).get(source.name, [])
            indices = sorted({max(0, min(count - 1, round(float(t) * fps))) for t in times})
            maximum = cfg["ingest"]["max_frames_per_video"]
            if len(indices) > maximum:
                raise ValueError(f"{source.name}: {len(indices)} frames exceed limit {maximum}; increase interval or limit explicitly")
            folder = root / "inputs" / f"video_{video_index:02d}"
            folder.mkdir(parents=True, exist_ok=True)
            frames = []
            for index in indices:
                cap.set(cv2.CAP_PROP_POS_FRAMES, index)
                ok, bgr = cap.read()
                if not ok:
                    raise RuntimeError(f"Decode failed: {source} frame {index}")
                file = folder / f"frame_{index:06d}.png"
                Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)).save(file)
                frames.append({"file": file.relative_to(root).as_posix(), "source_frame": index,
                               "time_s": index / fps, "resolution": [bgr.shape[1], bgr.shape[0]], "sha256": sha256(file)})
            for page in range(0, len(frames), 20):
                board = Image.new("RGB", (1280, 5 * 210), (25, 28, 32))
                draw = ImageDraw.Draw(board)
                for j, rec in enumerate(frames[page:page + 20]):
                    x, y = (j % 4) * 320, (j // 4) * 210
                    with Image.open(root / rec["file"]) as image:
                        image.thumbnail((310, 174))
                        board.paste(image, (x + 5, y + 24))
                    draw.text((x + 6, y + 5), f"video_{video_index:02d} / frame {rec['source_frame']} / {rec['time_s']:.2f}s", fill="white")
                board.save(folder / f"contact_{page // 20:02d}.jpg")
            records.append({"path": str(source), "sha256": sha256(source), "fps": fps,
                            "frame_count": count, "duration_s": duration, "frames": frames,
                            "timestamp_policy": "nominal frame_index/fps; VFR must be normalized externally"})
        finally:
            cap.release()
    if not records:
        # Historical scenes may have no preserved source video in this workspace.
        note = "No source video configured. Existing-model reuse only; no claim of new video reconstruction."
    else:
        note = "Sampled frames are an index; inspect complete videos and add topology/material event frames."
    write_json(root / "inputs" / "manifest.json", {"videos": records, "note": note})
    return {"videos": len(records), "frames": sum(len(r["frames"]) for r in records)}


def encode(cfg):
    root = Path(cfg["_root"])
    manifest = __import__("json").loads((root / "dataset" / "manifest.json").read_text(encoding="utf-8"))
    outputs = []
    for track in manifest["tracks"]:
        folder = root / "dataset" / track["id"]
        frames = track["frames"]
        first = cv2.imread(str(root / "dataset" / frames[0]["rgb"]))
        h, w = first.shape[:2]
        file = folder / "reverse.mp4"
        codec = cfg["export"]["video_codec"]
        writer = cv2.VideoWriter(str(file), cv2.VideoWriter_fourcc(*codec), track["fps"], (w, h))
        if not writer.isOpened():
            raise RuntimeError(f"OpenCV encoder {codec} unavailable")
        try:
            for frame in frames:
                image = cv2.imread(str(root / "dataset" / frame["rgb"]))
                if image is None or image.shape[:2] != (h, w):
                    raise RuntimeError(f"Missing/mismatched RGB: {frame['rgb']}")
                writer.write(image)
        finally:
            writer.release()
        cap = read_video(file)
        decoded = 0
        try:
            actual_fps = cap.get(cv2.CAP_PROP_FPS)
            while True:
                ok, image = cap.read()
                if not ok:
                    break
                if image.shape[:2] != (h, w):
                    raise RuntimeError("Encoded dimensions differ")
                decoded += 1
        finally:
            cap.release()
        if decoded != len(frames) or abs(actual_fps - track["fps"]) > 0.01:
            raise RuntimeError(f"Encoded frame count/fps mismatch: {file}")
        outputs.append({"track": track["id"], "file": file.relative_to(root).as_posix(),
                        "codec_requested": codec, "decoded_frames": decoded, "fps": actual_fps,
                        "duration_s": decoded / actual_fps, "sha256": sha256(file)})
    write_json(root / "dataset" / "videos.json", outputs)
    return outputs
