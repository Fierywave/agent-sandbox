# track-b-agent/ has a hyphen in its directory name, so it can't be
# imported as a dotted Python package (import track-b-agent is invalid
# syntax). This file is kept only as documentation of the module's public
# surface — actual imports elsewhere use plain top-level names
# (from graph import build_graph) with track-b-agent/ itself on sys.path.
#
# Public surface once this directory is on sys.path:
#   from graph import GraphState, build_graph, run_task
#   from planner import Planner
#   from registry_client import get_tool_registry