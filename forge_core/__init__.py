"""FORGE mission workbench: discovery, mixed-source corpus, and reuse decisions.

``__version__`` is the single source of the Forge release version. The CLI, the
local web UI and the GitHub User-Agent all read it from here. Component and
schema versions are separate and are listed in docs/VERSIONS.md.
"""
__version__="0.8.4"

# Name of the default workspace folder (``~/.forge-workbench-0.8.4``). This is a
# workspace-layout identifier, not the release version: it stays fixed when the
# release version changes so existing workspaces keep working.
DEFAULT_HOME_NAME=".forge-workbench-0.8.4"
