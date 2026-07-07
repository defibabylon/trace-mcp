#!/usr/bin/env bash
# Trace one-line installer for Claude Desktop (macOS / Linux).
#   curl -fsSL https://raw.githubusercontent.com/defibabylon/trace-mcp/main/install.sh | bash
# Downloads the latest release bundle (deps vendored), installs to ~/.trace-mcp,
# and safely merges the server into claude_desktop_config.json (backup kept).
set -euo pipefail

PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "Python 3.10+ is required. Install it from https://www.python.org/downloads/ and re-run."
  exit 1
fi
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo "Python 3.10+ required (found $("$PY" -V))."; exit 1; }

APP_DIR="$HOME/.trace-mcp"
echo "Downloading Trace (latest release)..."
TMP="$(mktemp -d)"
curl -fsSL -o "$TMP/trace-mcp.zip" \
  "https://github.com/defibabylon/trace-mcp/releases/latest/download/trace-mcp.mcpb"
mkdir -p "$APP_DIR"
"$PY" -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$TMP/trace-mcp.zip" "$APP_DIR"
rm -rf "$TMP"
echo "Installed to $APP_DIR"

case "$(uname -s)" in
  Darwin) CFG="$HOME/Library/Application Support/Claude/claude_desktop_config.json" ;;
  *)      CFG="${XDG_CONFIG_HOME:-$HOME/.config}/Claude/claude_desktop_config.json" ;;
esac

"$PY" - "$CFG" "$APP_DIR" "$PY" <<'MERGE'
import json, os, shutil, sys
cfg_path, app_dir, py = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
cfg = {}
if os.path.exists(cfg_path):
    with open(cfg_path, encoding="utf-8") as f:
        try:
            cfg = json.load(f)
        except json.JSONDecodeError:
            sys.exit(f"Existing config is not valid JSON, fix it first: {cfg_path}")
    shutil.copy2(cfg_path, cfg_path + ".bak")
cfg.setdefault("mcpServers", {})["trace"] = {
    "command": py,
    "args": [os.path.join(app_dir, "server.py")],
    "env": {"PYTHONPATH": os.path.join(app_dir, "lib")},
}
with open(cfg_path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2)
print(f"Registered in {cfg_path}" + (" (backup: .bak)" if os.path.exists(cfg_path + ".bak") else ""))
MERGE

echo
echo "Done. Restart Claude Desktop, then say: run the Trace wizard"
