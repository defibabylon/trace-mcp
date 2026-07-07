# Trace one-line installer for Claude Desktop (Windows).
#   powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/defibabylon/trace-mcp/main/install.ps1 | iex"
# Downloads the latest release bundle (deps vendored), installs to %USERPROFILE%\.trace-mcp,
# and safely merges the server into claude_desktop_config.json (backup kept).
$ErrorActionPreference = "Stop"

$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { $py = (Get-Command py -ErrorAction SilentlyContinue) }
if (-not $py) {
  Write-Host "Python 3.10+ is required. Install from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') and re-run."
  exit 1
}
$pyExe = $py.Source
& $pyExe -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if ($LASTEXITCODE -ne 0) { Write-Host "Python 3.10+ required."; exit 1 }

$appDir = Join-Path $env:USERPROFILE ".trace-mcp"
Write-Host "Downloading Trace (latest release)..."
$zip = Join-Path $env:TEMP "trace-mcp-install.zip"
Invoke-WebRequest -Uri "https://github.com/defibabylon/trace-mcp/releases/latest/download/trace-mcp.mcpb" -OutFile $zip
New-Item -ItemType Directory -Force $appDir | Out-Null
& $pyExe -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" $zip $appDir
Remove-Item $zip -Force
Write-Host "Installed to $appDir"

$cfg = Join-Path $env:APPDATA "Claude\claude_desktop_config.json"
$merge = @'
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
print(f"Registered in {cfg_path}")
'@
$mergeFile = Join-Path $env:TEMP "trace_merge_config.py"
Set-Content -Path $mergeFile -Value $merge -Encoding utf8
& $pyExe $mergeFile $cfg $appDir $pyExe
Remove-Item $mergeFile -Force

Write-Host ""
Write-Host "Done. Restart Claude Desktop, then say: run the Trace wizard"
