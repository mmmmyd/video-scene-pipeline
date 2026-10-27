"""CLI backend and the local transport used by the bundled official Blender MCP addon.

This socket adapter is not an MCP client. Any MCP-capable agent may alternatively
call execute_blender_code with the exact same file-based entry points.
"""
import json
import shutil
import socket
import subprocess
import time
from pathlib import Path


def executable(configured):
    path = shutil.which(configured)
    if path:
        return path
    if Path(configured).is_file():
        return configured
    # Local convenience only; portable deployments set tools.blender explicitly.
    options = sorted(Path("C:/Program Files/Blender Foundation").glob("Blender */blender.exe"))
    if configured == "blender" and options:
        return str(options[-1])
    raise FileNotFoundError(f"Blender executable not found: {configured}")


def send_code(code, host="127.0.0.1", port=9876, timeout=300):
    request = json.dumps({"type": "execute", "code": code, "strict_json": True}) + "\0"
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(request.encode("utf-8"))
        response = bytearray()
        while b"\0" not in response:
            chunk = sock.recv(65536)
            if not chunk:
                raise ConnectionError("Blender closed before the response delimiter; inspect logs before retry")
            response.extend(chunk)
    decoded = json.loads(response.partition(b"\0")[0])
    if decoded.get("status") != "ok":
        raise RuntimeError(decoded)
    return decoded.get("result")


def inspect_mcp(cfg):
    tools = cfg["tools"]
    return send_code(
        "import bpy\nresult = {'file': bpy.data.filepath, 'dirty': bpy.data.is_dirty, "
        "'scene': bpy.context.scene.name, 'background': bpy.app.background, "
        "'scenes': [s.name for s in bpy.data.scenes], "
        "'cameras': [o.name for o in bpy.context.scene.objects if o.type == 'CAMERA']}",
        tools["mcp_host"], tools["mcp_port"], tools["timeout_s"])


def run_blender(cfg, job, blend_file=None):
    """Run immutable jobs in an isolated process; no dependency on an LLM vendor."""
    root = Path(cfg["_root"])
    runtime = Path(__file__).resolve().parent / "blender_runtime.py"
    job_path = root / "jobs" / f"{job['action']}.json"
    from .common import write_json
    write_json(job_path, job)
    command = [executable(cfg["tools"]["blender"]), "--background"]
    command += [str(blend_file)] if blend_file else ["--factory-startup"]
    command += ["--python-exit-code", "1", "--python", str(runtime), "--", str(job_path)]
    log = root / "logs" / f"blender_{job['action']}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as stream:
        completed = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=cfg["tools"]["timeout_s"])
    if completed.returncode:
        raise RuntimeError(f"Blender failed ({completed.returncode}); see {log}")


def model_via_mcp(cfg, job, expected_file):
    from .common import write_json
    before = inspect_mcp(cfg)
    if before["dirty"]:
        raise RuntimeError("Connected Blender has unsaved changes; save them before using the MCP backend")
    if not before["file"] or Path(before["file"]).resolve() != Path(expected_file).resolve():
        raise RuntimeError(f"Connected file differs from configured source: {before['file']}")
    runtime = Path(__file__).resolve().parent / "blender_runtime.py"
    job_path = Path(cfg["_root"]) / "jobs" / "model.json"
    write_json(job_path, job)
    code = ("import importlib.util, pathlib\n"
            f"spec = importlib.util.spec_from_file_location('pipeline_runtime', {str(runtime)!r})\n"
            "module = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(module)\n"
            f"result = module.main({str(job_path)!r})")
    t = cfg["tools"]
    return send_code(code, t["mcp_host"], t["mcp_port"], t["timeout_s"])


def managed_model(cfg, job, source=None):
    """Own and stop an isolated addon worker; no dependency on a pre-opened Blender GUI."""
    from .common import write_json
    root = Path(cfg["_root"])
    worker = Path(__file__).resolve().parents[1] / "scripts" / "start_mcp_worker.py"
    runtime = Path(__file__).with_name("blender_runtime.py")
    request = root / "jobs" / "model.json"
    write_json(request, job)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    command = [executable(cfg["tools"]["blender"]), "--background"]
    command += [str(source)] if source else ["--factory-startup"]
    command += ["--python-exit-code", "1", "--python", str(worker), "--", "--addon-dir", cfg["tools"]["addon_dir"], "--port", str(port)]
    log_path = root / "logs" / "blender_model.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            deadline = time.monotonic() + min(60, cfg["tools"]["timeout_s"])
            while True:
                try:
                    actual = send_code("import bpy\nresult={'file':bpy.data.filepath}", "127.0.0.1", port, timeout=3)
                    break
                except OSError:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(f"Managed Blender MCP worker failed; inspect {log_path}")
                    time.sleep(.2)
            if source and Path(actual["file"]).resolve() != Path(source).resolve():
                raise ValueError("Managed worker opened an unexpected source file")
            code = ("import importlib.util\n"
                    f"spec=importlib.util.spec_from_file_location('pipeline_runtime',{str(runtime)!r})\n"
                    "module=importlib.util.module_from_spec(spec)\nspec.loader.exec_module(module)\n"
                    f"result=module.main({str(request)!r})")
            return send_code(code, "127.0.0.1", port, cfg["tools"]["timeout_s"])
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
