"""Model-independent coordinate and dataset operations (no Blender dependency)."""
import numpy as np


CV_FROM_BLENDER = np.diag([1.0, -1.0, -1.0, 1.0])


def trajectory_positions(matrix_world, direction, travel, count):
    matrix_world = np.asarray(matrix_world, dtype=float)
    forward = -matrix_world[:3, 2].copy()
    forward[2] = 0
    length = np.linalg.norm(forward)
    if length < 1e-7:
        raise ValueError("Camera looks vertically; supply a rear-facing camera with horizontal heading")
    forward /= length
    anchor = matrix_world[:3, 3]
    # The source camera is always the nearest pose. Approach starts farther away.
    offsets = np.linspace(-travel, 0, count) if direction == "approach" else np.linspace(0, -travel, count)
    return anchor + offsets[:, None] * forward


def backproject(depth, K, T_world_from_cam_cv, stride=1):
    valid = np.isfinite(depth) & (depth > 0)
    sample = np.zeros_like(valid)
    sample[::stride, ::stride] = True
    v, u = np.nonzero(valid & sample)
    pixels = np.c_[u, v, np.ones(len(u))]
    cam = (pixels @ np.linalg.inv(K).T) * depth[v, u, None]
    transform = np.asarray(T_world_from_cam_cv)
    world = cam @ transform[:3, :3].T + transform[:3, 3]
    return cam, world, np.c_[u, v]


def write_ply(path, points, colors):
    dtype = np.dtype([("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("red", "u1"), ("green", "u1"), ("blue", "u1")])
    rows = np.empty(len(points), dtype=dtype)
    for i, key in enumerate(("x", "y", "z")):
        rows[key] = points[:, i]
    for i, key in enumerate(("red", "green", "blue")):
        rows[key] = colors[:, i]
    header = ("ply\nformat binary_little_endian 1.0\ncomment synthetic model surface points\n"
              f"element vertex {len(rows)}\nproperty float x\nproperty float y\nproperty float z\n"
              "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n")
    with open(path, "wb") as stream:
        stream.write(header.encode("ascii"))
        rows.tofile(stream)


def reprojection_error(points_world, uv, K, T_world_from_cam_cv):
    if not len(points_world):
        return None
    inverse = np.linalg.inv(T_world_from_cam_cv)
    cam = points_world @ inverse[:3, :3].T + inverse[:3, 3]
    pixels = cam @ np.asarray(K).T
    pixels = pixels[:, :2] / pixels[:, 2, None]
    return float(np.max(np.linalg.norm(pixels - uv, axis=1)))
