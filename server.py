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
from pathlib import Path

from mcp.server.fastmcp import FastMCP

import trace_prompts as tp

mcp = FastMCP(
    "trace",
    instructions=(
        "Trace is an honest CV co-pilot. Flow: build a Truth Base from the "
        "user's CV, enrich it with an interview, score fit against a job "
        "honestly (willing to say skip), then tailor a CV whose every claim "
        "is receipt-verified against the Truth Base by validate_receipts. "
        "Start with the trace_wizard prompt or the get_wizard_state tool. "
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
                state["jobs"].append(
                    {
                        "company": d.name,
                        "has_fit": (d / "fit.json").exists(),
                        "exports": sorted(p.name for p in d.glob("*.md")),
                    }
                )
    if not state["truth_base"]:
        state["next_step"] = "No Truth Base. Ask the user to paste their CV, then use the parse_cv prompt."
    elif not state["truth_base"]["has_voice_sample"] or state["truth_base"]["with_metrics"] < state["truth_base"]["achievements"] / 2:
        state["next_step"] = "Truth Base is thin (missing metrics or voice sample). Offer the enrich interview, or accept a job description to score fit."
    elif not state["jobs"]:
        state["next_step"] = "Truth Base ready. Ask for a job description + company name to score fit."
    else:
        state["next_step"] = "Score fit for a new job, or tailor for a job that has a fit recorded."
    return json.dumps(state, indent=2)


@mcp.tool()
def save_truth_base(truth_base_json: str) -> str:
    """Persist the user's Truth Base (full JSON document, overwrites). The
    Truth Base is the ONLY ground truth Trace will tailor from."""
    tb = _parse_json(truth_base_json, "truth_base_json")
    if not isinstance(tb, dict) or "roles" not in tb:
        raise ValueError("Truth Base must be an object with at least a 'roles' array.")
    _tb_path().write_text(json.dumps(tb, indent=2, ensure_ascii=False), encoding="utf-8")
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
    return f"Saved {p}"


@mcp.tool()
def list_jobs() -> str:
    """List companies with saved jobs, fits, and exported documents."""
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
            docs = ", ".join(p.name for p in sorted(d.glob("*.md"))) or "no exports"
            lines.append(f"- {d.name}{fit} — {docs}")
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


if __name__ == "__main__":
    mcp.run()
