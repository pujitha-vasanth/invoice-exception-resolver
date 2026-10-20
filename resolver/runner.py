"""Runs the agent team on one or more invoices and saves what happened."""

import json
import sys
import time

from agents import (
    InputGuardrailTripwireTriggered,
    ItemHelpers,
    MaxTurnsExceeded,
    OutputGuardrailTripwireTriggered,
    Runner,
)
from agents.mcp import MCPServerStdio, create_static_tool_filter

from . import config, db, workflow
from .llm import build_model
from .mcp_server import READ_ONLY_TOOLS
from .team import build_team


def _tools_server(name: str, read_only: bool) -> MCPServerStdio:
    """Start the MCP server as a child process that talks over stdin/stdout."""
    return MCPServerStdio(
        name=name,
        params={
            "command": sys.executable,
            "args": ["-m", "resolver.mcp_server"],
            "cwd": str(config.ROOT),
            "env": {"AP_DB_PATH": str(config.db_path()), "PYTHONIOENCODING": "utf-8"},
        },
        cache_tools_list=True,
        client_session_timeout_seconds=60,
        tool_filter=create_static_tool_filter(allowed_tool_names=READ_ONLY_TOOLS) if read_only else None,
    )


def _short(value, limit: int = 400) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + " ..."


def extract_trace(result) -> list[dict]:
    """Turn the run into a readable step list: thoughts, tool calls, tool results, handoffs."""
    steps = []
    for item in result.new_items:
        agent = getattr(getattr(item, "agent", None), "name", "")
        if item.type == "message_output_item":
            text = ItemHelpers.text_message_output(item).strip()
            if text:
                steps.append({"agent": agent, "kind": "thought", "text": _short(text)})
        elif item.type == "tool_call_item":
            raw = item.raw_item
            name = getattr(raw, "name", None) or (raw.get("name") if isinstance(raw, dict) else "tool")
            args = getattr(raw, "arguments", None) or (raw.get("arguments") if isinstance(raw, dict) else "")
            steps.append({"agent": agent, "kind": "tool_call", "tool": name, "text": _short(args, 300)})
        elif item.type == "tool_call_output_item":
            steps.append({"agent": agent, "kind": "tool_result", "text": _short(item.output)})
        elif item.type == "handoff_output_item":
            steps.append({"agent": item.source_agent.name, "kind": "handoff",
                          "text": f"{item.source_agent.name} handed off to {item.target_agent.name}"})
    return steps


async def _resolve_one(triage, invoice_id: str) -> dict:
    started = time.perf_counter()
    base = {"invoice_id": invoice_id}
    try:
        result = await Runner.run(triage, f"Resolve invoice {invoice_id}.", max_turns=config.MAX_AGENT_TURNS)
    except InputGuardrailTripwireTriggered as exc:
        return {**base, "ok": False, "error": f"Skipped by input guardrail: {exc.guardrail_result.output.output_info}"}
    except OutputGuardrailTripwireTriggered as exc:
        return {**base, "ok": False, "error": f"Blocked by output guardrail: {exc.guardrail_result.output.output_info}"}
    except MaxTurnsExceeded:
        return {**base, "ok": False, "error": "Agent ran out of steps without finishing. Invoice left for a person."}

    seconds = time.perf_counter() - started
    agent_name = result.last_agent.name
    conn = db.connect()
    workflow.save_run_details(conn, invoice_id, agent_name, seconds, extract_trace(result))
    recorded = db.latest_resolution(conn, invoice_id)
    conn.close()

    if not recorded:
        return {**base, "ok": False, "agent": agent_name, "seconds": round(seconds, 1),
                "error": "No resolution was recorded. Final message: " + _short(str(result.final_output))}
    return {
        **base, "ok": True, "agent": agent_name, "seconds": round(seconds, 1),
        "action": recorded["action"], "status": recorded["status"],
        "exposure": recorded["exposure_amount"], "payment": recorded["payment_amount"],
        "approval_reason": recorded["approval_reason"], "summary": recorded["summary"],
    }


async def resolve_invoices(invoice_ids: list[str], on_done=None) -> list[dict]:
    """Resolve invoices one after another. on_done(result) is called after each one (used for progress)."""
    model = build_model()
    results = []
    async with _tools_server("ap-tools", read_only=False) as full, \
               _tools_server("ap-tools-readonly", read_only=True) as read_only:
        triage = build_team(model, full, read_only)
        for invoice_id in invoice_ids:
            try:
                outcome = await _resolve_one(triage, invoice_id)
            except Exception as exc:  # network errors, wrong key, rate limits
                outcome = {"invoice_id": invoice_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
            results.append(outcome)
            if on_done:
                on_done(outcome)
    return results


def open_exception_ids() -> list[str]:
    conn = db.connect()
    ids = [r["invoice_id"] for r in db.rows(conn, "SELECT invoice_id FROM invoices WHERE status = 'EXCEPTION' ORDER BY invoice_id")]
    conn.close()
    return ids
