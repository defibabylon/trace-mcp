"""Trace — honest CV co-pilot as a shareable MCP server.

Architecture: the HOST model (the user's own Claude) does all generation via
the wizard prompts; this server owns state and honesty. The Truth Base lives
on the user's machine (~/.trace/), never uploaded anywhere. The one thing the
model cannot talk its way past is `validate_receipts`: every claim on a
generated CV must cite a JSON path into the Truth Base, and the server
resolves those paths mechanically.

Run:  python server.py   (stdio transport)
State dir override for tests: TRACE_HOME env var.
"""
import json
import os
import re
import sys
from pathlib import Path

# Vendored dependencies: when lib/ ships next to this file (mcpb bundle or
# installer layout), put it on sys.path ourselves so no PYTHONPATH is needed.
# pywin32 (an mcp dependency on Windows) needs its subdirs added explicitly:
# they are normally wired up by a .pth file, which PYTHONPATH entries and
# plain sys.path inserts never process.
_LIB = Path(__file__).resolve().parent / "lib"
if _LIB.is_dir():
    for _sub in ("", "win32", os.path.join("win32", "lib"), "pythonwin"):
        _p = str(_LIB / _sub) if _sub else str(_LIB)
        if os.path.isdir(_p) and _p not in sys.path:
            sys.path.insert(0, _p)

from mcp.server.fastmcp import FastMCP

import trace_prompts as tp

mcp = FastMCP(
    "trace",
    instructions=(
        "Trace is an honest CV co-pilot: the full application flow in chat. "
        "Build a Truth Base from the user's CV, enrich it with an interview, "
        "score fit against a job honestly (willing to say skip), tailor a CV "
        "whose every claim is receipt-verified by validate_receipts, then "
        "build the interview prep pack (cheat sheet, question bank, Anki deck "
        "via build_anki, briefing script) and track applications with "
        "set_status. Start with the trace_wizard prompt or get_wizard_state. "
        "Never invent facts about the user; the receipts check will fail."
    ),
)


# --- state ------------------------------------------------------------------
def _home() -> Path:
    d = Path(os.environ.get("TRACE_HOME", Path.home() / ".trace"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _tb_path() -> Path:
    return _home() / "truth_base.json"


def _slug(company: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")
    return s or "unnamed"


def _job_dir(company: str, create: bool = False) -> Path:
    d = _home() / "jobs" / _slug(company)
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def _parse_json(raw: str, what: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"{what} is not valid JSON: {e}") from e


# --- obsidian vault rendering -------------------------------------------------
# ~/.trace doubles as an Obsidian vault: exports are already markdown, and
# these two notes (auto-refreshed on every state change) make it a browsable
# persistent career memory. Open the folder as a vault in Obsidian.
def _render_vault():
    home = _home()
    tb = json.loads(_tb_path().read_text(encoding="utf-8")) if _tb_path().exists() else None

    # Truth Base.md — human-readable render of the machine truth
    if tb:
        lines = ["# Truth Base", "", "> The single source of truth Trace tailors from.",
                 "> Machine copy: `truth_base.json` (edit via the enrich interview, not by hand).", ""]
        ident = tb.get("identity", {})
        if ident.get("name"):
            lines.append(f"**{ident['name']}** — {ident.get('location', '')}".rstrip(" —"))
        if ident.get("work_auth"):
            lines.append(f"Work auth: {ident['work_auth']}")
        if tb.get("voice_sample"):
            lines += ["", f"> *\"{tb['voice_sample']}\"*"]
        for r in tb.get("roles", []):
            lines += ["", f"## {r.get('title', '?')} — {r.get('org', '?')}",
                      f"*{r.get('dates', '')}*" + (f" · {r['scope']}" if r.get("scope") else "")]
            for a in r.get("achievements", []):
                metric = f" **[{a['metric']}]**" if a.get("metric") else ""
                tag = a.get("evidence_tag", "")
                lines.append(f"- {a.get('statement', '')}{metric} `{tag}`")
        for section, key in [("Skills", "skills"), ("Tools", "tools"), ("Education", "education"),
                             ("Certifications", "certs"), ("Licenses", "licenses"), ("Artifacts", "artifacts")]:
            vals = tb.get(key) or []
            if vals:
                lines += ["", f"## {section}", ", ".join(str(v) for v in vals)]
        (home / "Truth Base.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Home.md — the dashboard
    lines = ["# Trace — Career Vault", "",
             "Your job hunt's persistent memory. Maintained by the Trace MCP wizard; safe to read and annotate.", ""]
    if tb:
        roles = tb.get("roles", [])
        ach = [a for r in roles for a in r.get("achievements", [])]
        lines += [f"**[[Truth Base]]** — {len(roles)} role(s), {len(ach)} achievement(s), "
                  f"{sum(1 for a in ach if a.get('metric'))} with metrics.", ""]
    else:
        lines += ["*No Truth Base yet — paste your CV into the wizard to start.*", ""]
    lines.append("## Applications")
    jobs_dir = home / "jobs"
    any_jobs = False
    if jobs_dir.exists():
        for d in sorted(jobs_dir.iterdir()):
            if not d.is_dir():
                continue
            any_jobs = True
            fit = ""
            if (d / "fit.json").exists():
                f = json.loads((d / "fit.json").read_text(encoding="utf-8"))
                fit = f" · fit **{f.get('fit_score', '?')}/100** ({f.get('verdict', '?')})"
            status = ""
            if (d / "status.json").exists():
                s = json.loads((d / "status.json").read_text(encoding="utf-8"))
                status = f" · status **{s.get('status', '?')}**"
            lines.append(f"### {d.name}{fit}{status}")
            docs = sorted(d.glob("*.md")) + sorted(d.glob("*.apkg"))
            for p in docs:
                lines.append(f"- [{p.stem}](jobs/{d.name}/{p.name.replace(' ', '%20')})")
            if not docs:
                lines.append("- *no documents yet*")
            lines.append("")
    if not any_jobs:
        lines += ["*No applications tracked yet.*", ""]
    (home / "Home.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- receipt path resolver ---------------------------------------------------
_TOKEN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]")


def resolve_path(obj, path: str):
    """Resolve a receipt source path like roles[0].achievements[2].statement
    against the Truth Base. Returns (ok, value_or_error)."""
    if not path or not path.strip():
        return False, "empty source path"
    pos, cur = 0, obj
    cleaned = path.strip()
    for m in _TOKEN.finditer(cleaned):
        # reject junk between tokens (anything but dots/whitespace)
        gap = cleaned[pos : m.start()]
        if gap.strip(". "):
            return False, f"unparseable segment {gap!r} in path {path!r}"
        pos = m.end()
        key, idx = m.group(1), m.group(2)
        if key is not None:
            if not isinstance(cur, dict) or key not in cur:
                return False, f"key {key!r} not found (path {path!r})"
            cur = cur[key]
        else:
            i = int(idx)
            if not isinstance(cur, list) or i >= len(cur):
                return False, f"index [{i}] out of range (path {path!r})"
            cur = cur[i]
    if pos == 0:
        return False, f"unparseable path {path!r}"
    if cleaned[pos:].strip(". "):
        return False, f"unparseable segment {cleaned[pos:]!r} in path {path!r}"
    return True, cur


# --- tools --------------------------------------------------------------------
@mcp.tool()
def get_wizard_state() -> str:
    """Where is this user in the Trace flow? Call this first. Returns what
    exists (Truth Base, jobs, fits) and the suggested next step."""
    state = {"truth_base": None, "jobs": [], "next_step": ""}
    if _tb_path().exists():
        tb = json.loads(_tb_path().read_text(encoding="utf-8"))
        roles = tb.get("roles", [])
        ach = [a for r in roles for a in r.get("achievements", [])]
        state["truth_base"] = {
            "name": tb.get("identity", {}).get("name", ""),
            "roles": len(roles),
            "achievements": len(ach),
            "with_metrics": sum(1 for a in ach if a.get("metric")),
            "has_voice_sample": bool(tb.get("voice_sample")),
        }
    jobs_dir = _home() / "jobs"
    if jobs_dir.exists():
        for d in sorted(jobs_dir.iterdir()):
            if d.is_dir():
                status = None
                if (d / "status.json").exists():
                    status = json.loads((d / "status.json").read_text(encoding="utf-8")).get("status")
                state["jobs"].append(
                    {
                        "company": d.name,
                        "has_fit": (d / "fit.json").exists(),
                        "status": status,
                        "exports": sorted(p.name for p in list(d.glob("*.md")) + list(d.glob("*.apkg"))),
                    }
                )
    if not state["truth_base"]:
        state["next_step"] = "No Truth Base. Ask the user to paste their CV, then use the parse_cv prompt."
    elif not state["truth_base"]["has_voice_sample"] or state["truth_base"]["with_metrics"] < state["truth_base"]["achievements"] / 2:
        state["next_step"] = "Truth Base is thin (missing metrics or voice sample). Offer the enrich interview, or accept a job description to score fit."
    elif not state["jobs"]:
        state["next_step"] = "Truth Base ready. Ask for a job description + company name to score fit."
    else:
        needs_prep = [
            j["company"] for j in state["jobs"]
            if "cv.md" in j["exports"] and "cheat_sheet.md" not in j["exports"]
        ]
        if needs_prep:
            state["next_step"] = (
                f"CV exported for {', '.join(needs_prep)} without an interview prep pack yet. "
                "Offer the prep_pack step (cheat sheet, question bank, Anki deck, briefing). "
                "Or score fit for a new job."
            )
        else:
            state["next_step"] = "Score fit for a new job, tailor where a fit is recorded, or update statuses with set_status."
    return json.dumps(state, indent=2)


@mcp.tool()
def save_truth_base(truth_base_json: str) -> str:
    """Persist the user's Truth Base (full JSON document, overwrites). The
    Truth Base is the ONLY ground truth Trace will tailor from."""
    tb = _parse_json(truth_base_json, "truth_base_json")
    if not isinstance(tb, dict) or "roles" not in tb:
        raise ValueError("Truth Base must be an object with at least a 'roles' array.")
    _tb_path().write_text(json.dumps(tb, indent=2, ensure_ascii=False), encoding="utf-8")
    _render_vault()
    roles = tb.get("roles", [])
    ach = [a for r in roles for a in r.get("achievements", [])]
    return (
        f"Truth Base saved to {_tb_path()} — {len(roles)} role(s), {len(ach)} achievement(s), "
        f"{sum(1 for a in ach if a.get('metric'))} with metrics, "
        f"voice_sample={'yes' if tb.get('voice_sample') else 'no'}."
    )


@mcp.tool()
def get_truth_base() -> str:
    """Return the saved Truth Base JSON. This is the only source of truth for
    fit scoring and tailoring."""
    if not _tb_path().exists():
        return "NO_TRUTH_BASE: none saved yet. Parse the user's CV first (parse_cv prompt)."
    return _tb_path().read_text(encoding="utf-8")


@mcp.tool()
def save_job(company: str, jd_parsed_json: str) -> str:
    """Persist a parsed job description for a company (requirements + hard
    blockers JSON, per the score_fit prompt shape)."""
    jd = _parse_json(jd_parsed_json, "jd_parsed_json")
    d = _job_dir(company, create=True)
    (d / "job.json").write_text(json.dumps(jd, indent=2, ensure_ascii=False), encoding="utf-8")
    _render_vault()
    return f"Job saved for '{_slug(company)}' ({len(jd.get('requirements', []))} requirements, {len(jd.get('hard_blockers', []))} hard blocker(s))."


@mcp.tool()
def get_job(company: str) -> str:
    """Return the parsed job JSON and fit JSON (if recorded) for a company."""
    d = _job_dir(company)
    if not (d / "job.json").exists():
        return f"NO_JOB: nothing saved for '{_slug(company)}'. Use the score_fit prompt first."
    out = {"job": json.loads((d / "job.json").read_text(encoding="utf-8"))}
    if (d / "fit.json").exists():
        out["fit"] = json.loads((d / "fit.json").read_text(encoding="utf-8"))
    return json.dumps(out, indent=2)


@mcp.tool()
def record_fit(company: str, fit_json: str) -> str:
    """Persist the honest fit score for a company (blocker_check, gap map,
    fit_score, verdict apply|stretch|skip)."""
    fit = _parse_json(fit_json, "fit_json")
    d = _job_dir(company, create=True)
    (d / "fit.json").write_text(json.dumps(fit, indent=2, ensure_ascii=False), encoding="utf-8")
    _render_vault()
    return f"Fit recorded for '{_slug(company)}': {fit.get('fit_score', '?')}/100, verdict={fit.get('verdict', '?')}."


@mcp.tool()
def validate_receipts(company: str, receipts_json: str) -> str:
    """THE honesty gate. Verify a receipts list (JSON array of
    {claim, source}) mechanically against the saved Truth Base: every source
    path must resolve to a real entry. UNSUPPORTED or unresolvable sources
    fail. Do not present a CV to the user until this passes."""
    if not _tb_path().exists():
        raise ValueError("No Truth Base saved; nothing to validate against.")
    tb = json.loads(_tb_path().read_text(encoding="utf-8"))
    receipts = _parse_json(receipts_json, "receipts_json")
    if isinstance(receipts, dict) and "receipts" in receipts:
        receipts = receipts["receipts"]
    if not isinstance(receipts, list) or not receipts:
        raise ValueError("receipts_json must be a non-empty JSON array of {claim, source}.")

    results, ok_n, unsupported_n, failed_n = [], 0, 0, 0
    for r in receipts:
        claim = (r.get("claim") or "").strip()
        source = (r.get("source") or "").strip()
        if source.upper() == "UNSUPPORTED":
            unsupported_n += 1
            results.append({"claim": claim, "source": source, "status": "UNSUPPORTED", "note": "model flagged this claim as ungrounded"})
            continue
        ok, val = resolve_path(tb, source)
        if ok:
            ok_n += 1
            snippet = json.dumps(val, ensure_ascii=False)
            results.append({"claim": claim, "source": source, "status": "OK", "resolves_to": snippet[:200]})
        else:
            failed_n += 1
            results.append({"claim": claim, "source": source, "status": "FAILED", "note": val})

    verdict = "PASS" if (unsupported_n == 0 and failed_n == 0) else "FAIL"
    report = {
        "verdict": verdict,
        "ok": ok_n,
        "unsupported": unsupported_n,
        "failed_paths": failed_n,
        "instruction": (
            "All claims are backed by the Truth Base." if verdict == "PASS"
            else "Rewrite the CV: remove or reground every UNSUPPORTED/FAILED claim, then validate again. Do not show the user a failing CV."
        ),
        "receipts": results,
    }
    d = _job_dir(company, create=True)
    (d / "receipts.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return json.dumps(report, indent=2, ensure_ascii=False)


@mcp.tool()
def export_document(company: str, filename: str, markdown: str) -> str:
    """Save a finished document (e.g. cv.md, cover_letter.md) for a company.
    Only export after validate_receipts returns PASS."""
    if not re.fullmatch(r"[A-Za-z0-9._-]+\.md", filename):
        raise ValueError("filename must be a simple .md name, e.g. cv.md")
    d = _job_dir(company, create=True)
    p = d / filename
    p.write_text(markdown, encoding="utf-8")
    _render_vault()
    return f"Saved {p}"


VALID_STATUSES = {"preparing", "applied", "interview", "offer", "rejected", "withdrawn"}


@mcp.tool()
def set_status(company: str, status: str, note: str = "") -> str:
    """Track where an application stands. status must be one of:
    preparing | applied | interview | offer | rejected | withdrawn."""
    status = status.strip().lower()
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {sorted(VALID_STATUSES)}")
    d = _job_dir(company, create=True)
    (d / "status.json").write_text(
        json.dumps({"status": status, "note": note}, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _render_vault()
    return f"Status for '{_slug(company)}' set to {status}." + (f" Note: {note}" if note else "")


@mcp.tool()
def build_anki(company: str, cards_json: str, deck_name: str = "") -> str:
    """Build a real Anki deck (.apkg) from interview prep cards. cards_json is
    a JSON array of {front, back, tags?}. The user imports the file into Anki."""
    import hashlib

    import genanki

    cards = _parse_json(cards_json, "cards_json")
    if isinstance(cards, dict) and "cards" in cards:
        cards = cards["cards"]
    if not isinstance(cards, list) or not cards:
        raise ValueError("cards_json must be a non-empty JSON array of {front, back}.")
    for i, c in enumerate(cards):
        if not (c.get("front") or "").strip() or not (c.get("back") or "").strip():
            raise ValueError(f"card {i} is missing front or back text")

    deck_name = deck_name.strip() or f"Trace: {company} interview prep"

    # deterministic IDs so re-generating the deck updates rather than duplicates
    def _stable_id(text):
        return int(hashlib.md5(text.encode("utf-8")).hexdigest()[:9], 16)

    model = genanki.Model(
        _stable_id("trace-basic-model"),
        "Trace Basic",
        fields=[{"name": "Front"}, {"name": "Back"}],
        templates=[{
            "name": "Card 1",
            "qfmt": "{{Front}}",
            "afmt": "{{FrontSide}}<hr id=answer>{{Back}}",
        }],
    )
    deck = genanki.Deck(_stable_id(f"trace-deck-{_slug(company)}"), deck_name)
    for c in cards:
        tags = [str(t).replace(" ", "-") for t in c.get("tags", [])]
        deck.add_note(genanki.Note(model=model, fields=[c["front"], c["back"]], tags=tags))

    d = _job_dir(company, create=True)
    out = d / "interview_prep.apkg"
    genanki.Package(deck).write_to_file(str(out))
    _render_vault()
    return f"Anki deck written: {out} ({len(cards)} cards). Import it in Anki via File > Import."


@mcp.tool()
def setup_obsidian() -> str:
    """Set up the Trace state folder as an Obsidian vault: a Home.md dashboard
    and a readable Truth Base.md, auto-refreshed on every change. Gives the
    user a persistent, browsable career memory."""
    _render_vault()
    home = _home()
    made = [p.name for p in [home / "Home.md", home / "Truth Base.md"] if p.exists()]
    return (
        f"Vault ready at {home} ({', '.join(made)} generated; they refresh automatically on every change).\n"
        f"To browse it: open Obsidian (free, obsidian.md) > Open folder as vault > choose {home}. "
        "Start from Home.md. All CVs, cover letters, prep packs and statuses live there as linked notes; "
        "annotate freely, Trace only rewrites Home.md and Truth Base.md."
    )


@mcp.tool()
def list_jobs() -> str:
    """List companies with saved jobs, fits, statuses, and exported documents."""
    jobs_dir = _home() / "jobs"
    if not jobs_dir.exists():
        return "No jobs saved yet."
    lines = []
    for d in sorted(jobs_dir.iterdir()):
        if d.is_dir():
            fit = ""
            if (d / "fit.json").exists():
                f = json.loads((d / "fit.json").read_text(encoding="utf-8"))
                fit = f" — fit {f.get('fit_score', '?')}/100 ({f.get('verdict', '?')})"
            status = ""
            if (d / "status.json").exists():
                s = json.loads((d / "status.json").read_text(encoding="utf-8"))
                status = f" — status: {s.get('status', '?')}"
            docs = ", ".join(p.name for p in sorted(d.glob("*.md")) + sorted(d.glob("*.apkg"))) or "no exports"
            lines.append(f"- {d.name}{fit}{status} — {docs}")
    return "\n".join(lines) or "No jobs saved yet."


# --- prompts (the wizard steps) -----------------------------------------------
@mcp.prompt(name="trace_wizard", description="Start (or resume) the Trace honest-CV wizard.")
def trace_wizard() -> str:
    return tp.WIZARD_START


@mcp.prompt(name="parse_cv", description="Step 1: parse a pasted CV into the Truth Base.")
def parse_cv(cv_text: str) -> str:
    return tp.PARSE_CV.format(shape=tp.TRUTH_BASE_SHAPE, cv_text=cv_text)


@mcp.prompt(name="enrich", description="Step 2: interview the user to fill Truth Base gaps (metrics, scope, voice).")
def enrich() -> str:
    return tp.ENRICH


@mcp.prompt(name="score_fit", description="Step 3: parse a job description and score fit honestly (apply/stretch/skip).")
def score_fit(jd_text: str, company: str) -> str:
    return tp.SCORE_FIT.format(jd_text=jd_text, company=company)


@mcp.prompt(name="tailor", description="Step 4: tailor CV + cover letter with server-verified receipts.")
def tailor(company: str) -> str:
    return tp.TAILOR.format(company=company)


@mcp.prompt(name="prep_pack", description="Step 5: interview prep pack — company cheat sheet, question bank, Anki deck, briefing script.")
def prep_pack(company: str) -> str:
    return tp.PREP_PACK.format(company=company)


if __name__ == "__main__":
    mcp.run()
