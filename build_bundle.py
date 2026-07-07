"""Build trace-mcp.mcpb (a zip) directly.

We don't use `mcpb pack` because its manifest schema rejects the MCP-spec
tool inputSchema / prompt argument objects that Smithery's registry requires
in the serverCard. Run: python build_bundle.py
Expects the mcp dependency vendored first: pip install --target lib mcp
"""
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "trace-mcp.mcpb"

INCLUDE_FILES = ["manifest.json", "server.py", "trace_prompts.py", "LICENSE", "README.md", "PRIVACY.md"]

assert (ROOT / "lib").is_dir(), "vendor deps first: pip install --target lib mcp"

with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for name in INCLUDE_FILES:
        z.write(ROOT / name, name)
    for p in (ROOT / "lib").rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts:
            z.write(p, p.relative_to(ROOT).as_posix())

print(f"built {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")
