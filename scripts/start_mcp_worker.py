"""Start an isolated official-addon worker. Run INSIDE Blender, never ordinary Python."""
import argparse
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--addon-dir", required=True)
parser.add_argument("--port", type=int, default=19876)
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
sys.path.insert(0, args.addon_dir)
from blender_mcp_addon import mcp_to_blender_server, execute_blocking

mcp_to_blender_server.start("127.0.0.1", args.port)
print("PIPELINE_MCP_WORKER_READY", flush=True)
execute_blocking.run()
