"""Trace MCP server tests. Run: python test_server.py

1. Unit: receipt path resolver (valid, invalid, junk paths)
2. Tools: state round-trip + validate_receipts catches a planted fabrication
3. Transport: real stdio handshake — spawn server, list tools + prompts,
   call get_wizard_state through the wire.

Uses a temp TRACE_HOME so the real ~/.trace is never touched.
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="trace_test_")
os.environ["TRACE_HOME"] = TMP

import server  # noqa: E402  (after TRACE_HOME so state lands in TMP)

FAILS = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


TB = {
    "identity": {"name": "Priya Osei", "location": "Leeds, UK", "work_auth": "UK citizen"},
    "voice_sample": "I like wards that run calmly because someone planned for the chaos.",
    "roles": [
        {
            "title": "Staff Nurse",
            "org": "St James's University Hospital",
            "dates": "2021 - Present",
            "achievements": [
                {"statement": "Coordinated shifts on a 28-bed acute ward", "metric": "28 beds", "evidence_tag": "owned", "skills": ["shift coordination"], "tools": []},
                {"statement": "Mentored 6 student nurses to sign-off", "metric": "6 students", "evidence_tag": "measured", "skills": ["mentoring"], "tools": []},
            ],
        }
    ],
    "skills": ["acute care", "escalation", "mentoring"],
    "education": ["BSc Nursing, University of Leeds"],
    "certs": [],
    "licenses": ["NMC registered"],
    "artifacts": [],
}


def unit_resolver():
    print("\n[1] resolver unit tests")
    ok, v = server.resolve_path(TB, "roles[0].achievements[1]")
    check("valid nested path", ok and v["metric"] == "6 students")
    ok, v = server.resolve_path(TB, "skills[2]")
    check("valid list index", ok and v == "mentoring")
    ok, v = server.resolve_path(TB, "identity.name")
    check("valid dotted path", ok and v == "Priya Osei")
    ok, v = server.resolve_path(TB, "roles[0].achievements[9]")
    check("out-of-range index fails", not ok)
    ok, v = server.resolve_path(TB, "roles[0].awards")
    check("missing key fails", not ok)
    ok, v = server.resolve_path(TB, "")
    check("empty path fails", not ok)
    ok, v = server.resolve_path(TB, "roles[0]; drop everything")
    check("junk path fails", not ok)


def tools_roundtrip():
    print("\n[2] tool round-trip + honesty gate")
    out = server.save_truth_base(json.dumps(TB))
    check("save_truth_base", "1 role(s)" in out and "2 achievement(s)" in out, out)
    tb_back = json.loads(server.get_truth_base())
    check("get_truth_base round-trip", tb_back["identity"]["name"] == "Priya Osei")

    state = json.loads(server.get_wizard_state())
    check("wizard state sees truth base", state["truth_base"]["roles"] == 1)

    jd = {"role": "Senior Staff Nurse", "org": "Leeds", "requirements": [{"text": "NMC registration", "must_have": True}], "hard_blockers": [{"type": "license", "detail": "NMC registration"}]}
    server.save_job("Leeds Teaching Hospitals", json.dumps(jd))
    fit = {"blocker_check": [{"detail": "NMC", "met": True}], "requirements": [], "fit_score": 78, "verdict": "apply", "reasoning": "solid"}
    out = server.record_fit("Leeds Teaching Hospitals", json.dumps(fit))
    check("record_fit", "78/100" in out)
    out = server.get_job("Leeds Teaching Hospitals")
    check("get_job returns job+fit", '"fit"' in out)

    # honest receipts -> PASS
    good = [
        {"claim": "Coordinated a 28-bed acute ward", "source": "roles[0].achievements[0]"},
        {"claim": "Mentored 6 students to sign-off", "source": "roles[0].achievements[1]"},
        {"claim": "NMC registered", "source": "licenses[0]"},
    ]
    rep = json.loads(server.validate_receipts("Leeds Teaching Hospitals", json.dumps(good)))
    check("honest receipts PASS", rep["verdict"] == "PASS" and rep["ok"] == 3)

    # planted fabrication -> FAIL (both an UNSUPPORTED flag and a bogus path)
    bad = good + [
        {"claim": "Led a 40-person ICU team", "source": "UNSUPPORTED"},
        {"claim": "Ran the hospital's quality board", "source": "roles[0].achievements[7]"},
    ]
    rep = json.loads(server.validate_receipts("Leeds Teaching Hospitals", json.dumps(bad)))
    check("fabrication caught", rep["verdict"] == "FAIL" and rep["unsupported"] == 1 and rep["failed_paths"] == 1)

    out = server.export_document("Leeds Teaching Hospitals", "cv.md", "# CV\n")
    check("export_document", out.startswith("Saved") and Path(out[6:]).exists())
    try:
        server.export_document("Leeds Teaching Hospitals", "../evil.md", "x")
        check("filename traversal rejected", False)
    except ValueError:
        check("filename traversal rejected", True)
    check("list_jobs", "leeds-teaching-hospitals" in server.list_jobs())


async def stdio_handshake():
    print("\n[3] stdio handshake (real transport)")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).parent / "server.py")],
        env={**os.environ, "TRACE_HOME": TMP},
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as sess:
            await sess.initialize()
            tools = {t.name for t in (await sess.list_tools()).tools}
            need = {"get_wizard_state", "save_truth_base", "get_truth_base", "save_job", "get_job", "record_fit", "validate_receipts", "export_document", "list_jobs"}
            check("all 9 tools listed", need <= tools, str(need - tools))
            prompts = {p.name for p in (await sess.list_prompts()).prompts}
            check("all 5 prompts listed", {"trace_wizard", "parse_cv", "enrich", "score_fit", "tailor"} <= prompts, str(prompts))
            res = await sess.call_tool("get_wizard_state", {})
            state = json.loads(res.content[0].text)
            check("get_wizard_state over the wire", state["truth_base"]["name"] == "Priya Osei")
            pr = await sess.get_prompt("parse_cv", {"cv_text": "dummy cv"})
            check("parse_cv prompt renders", "dummy cv" in pr.messages[0].content.text)


if __name__ == "__main__":
    unit_resolver()
    tools_roundtrip()
    asyncio.run(stdio_handshake())
    print("\n" + ("ALL TESTS PASSED" if not FAILS else f"{len(FAILS)} FAILURE(S): {FAILS}"))
    sys.exit(1 if FAILS else 0)
