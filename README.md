# Trace — the CV tool that won't lie for you

An MCP server that turns your own Claude (Desktop, Code, or any MCP client) into an honest CV co-pilot. Your career data stays on your machine, and every claim on a generated CV is mechanically verified against what you actually did.

## Why this exists

Every AI CV tool has the same failure mode: it invents. Trace inverts it.

1. **Truth Base** — your CV is parsed into a structured record of what you actually did (`~/.trace/truth_base.json`, never uploaded anywhere).
2. **Enrich interview** — sharp questions that surface the metrics and scope your CV under-sells. Facts you supply, never suggestions to inflate.
3. **Honest fit score** — a job description is scored against your Truth Base with a verdict of apply, stretch, or **skip**. Trace will tell you not to apply.
4. **Tailor with receipts** — the CV is generated ONLY from Truth Base evidence. Every claim carries a JSON-path receipt, and the server's `validate_receipts` tool resolves each path mechanically. A claim that doesn't resolve fails the gate; the model must remove it before you ever see the document.

The honesty guardrail is code, not a system prompt.

## Install

Requires Python 3.10+.

```bash
git clone https://github.com/defibabylon/trace-mcp.git
cd trace-mcp
pip install -r requirements.txt
```

Claude Code (from the repo directory):

```bash
claude mcp add trace -- python server.py
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "trace": {
      "command": "python",
      "args": ["<absolute path>/trace-mcp/server.py"]
    }
  }
}
```

No API key. The model you're already talking to does the generation.

## Use

Start with the **trace_wizard** prompt (or just say "run the Trace wizard"). Steps:

| Prompt | What happens |
|---|---|
| `trace_wizard` | Resume wherever you left off |
| `parse_cv` | Paste your CV → Truth Base |
| `enrich` | Interview to fill gaps (metrics, scope, your voice) |
| `score_fit` | Paste a JD → blockers, gap map, apply/stretch/skip |
| `tailor` | Receipt-verified CV + cover letter, exported to `~/.trace/jobs/<company>/` |
| `prep_pack` | Interview prep: company cheat sheet, question bank, a real Anki deck (.apkg), and a morning-of briefing script |

Say "I applied" / "they invited me to interview" and the built-in tracker (`set_status`) keeps every application's status; `list_jobs` shows the whole pipeline.

## Obsidian career vault

Say "set up Obsidian" and Trace turns its folder into a proper [Obsidian](https://obsidian.md) vault: a **Home.md** dashboard (every application with fit, status, and linked documents) and a readable **Truth Base.md**, both auto-refreshed on every change. Open the folder as a vault and your job hunt has persistent, browsable memory outside chat. Your own notes are never touched; Trace only rewrites those two files.

## Tools (for the model)

`get_wizard_state`, `save_truth_base`, `get_truth_base`, `save_job`, `get_job`, `record_fit`, `validate_receipts`, `export_document`, `list_jobs`, `set_status`, `build_anki`, `setup_obsidian`

## Privacy

Everything lives in `~/.trace/` on your machine (override with `TRACE_HOME`). Nothing is sent anywhere except the conversation you are already having with your model. Delete the folder to reset.

## Test

```bash
python test_server.py
```

## Build + publish the MCPB bundle (Smithery)

```bash
pip install --target lib mcp genanki
python build_bundle.py
npx -y smithery@latest mcp publish ./trace-mcp.mcpb -n defibabylon/trace-mcp
```

The bundle vendors the `mcp` dependency into `lib/` (see `manifest.json`), so users only need Python 3.10+ on PATH. We zip directly (`build_bundle.py`) rather than using `mcpb pack`: Smithery's registry requires MCP-spec tool `inputSchema` objects in the manifest, which `mcpb pack`'s stricter schema rejects.

Live listing: https://smithery.ai/servers/defibabylon/trace-mcp

## License

MIT — [AtlasFlow Systems](https://atlasflowsystems.co.za). Trace is an AtlasFlow product; the full web app (no MCP client needed) is in development.
