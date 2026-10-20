"""End-to-end test of the agent plumbing WITHOUT calling a real LLM.

A scripted model replays a fixed sequence of tool calls. Everything else is real:
the Agents SDK, the handoff, the MCP server process, the tools, the database and both guardrail layers.
This proves the wiring works. It does not test how well a real model reasons; for that, run the app with a key.
"""

import asyncio
import json

from agents import ModelResponse, Usage
from agents.models.interface import Model
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

from resolver import db, runner
from resolver.team import build_team


class ScriptedModel(Model):
    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = 0
        self.tools_seen = []  # tool names offered on each call, to check least privilege

    def _message(self, text):
        return ResponseOutputMessage(
            id=f"msg_{self.calls}", role="assistant", status="completed", type="message",
            content=[ResponseOutputText(text=text, type="output_text", annotations=[])],
        )

    def _call(self, name, args):
        return ResponseFunctionToolCall(
            id=f"fc_{self.calls}", call_id=f"call_{self.calls}", name=name,
            arguments=json.dumps(args), type="function_call", status="completed",
        )

    async def get_response(self, system_instructions, input, model_settings, tools, output_schema, handoffs,
                           tracing, *, previous_response_id=None, conversation_id=None, prompt=None, **kwargs):
        self.calls += 1
        self.tools_seen.append([t.name for t in tools])
        kind, *rest = self.steps.pop(0)
        if kind == "tool":
            name, args, thought = rest
            output = [self._message(f"Thought: {thought}"), self._call(name, args)]
        elif kind == "handoff":
            target = next(h for h in handoffs if h.agent_name == rest[0])
            output = [self._call(target.tool_name, {})]
        else:  # final answer
            output = [self._message(json.dumps(rest[0]))]
        return ModelResponse(output=output, usage=Usage(), response_id=None)

    def stream_response(self, *args, **kwargs):
        raise NotImplementedError


DUPLICATE_SCRIPT = [
    ("tool", "get_invoice", {"invoice_id": "INV-1007"}, "Read the invoice first."),
    ("tool", "analyze_variance", {"invoice_id": "INV-1007"}, "Check the match flags."),
    ("handoff", "Duplicate Invoice Specialist"),
    ("tool", "find_duplicate_candidates", {"invoice_id": "INV-1007"}, "Look for the earlier bill."),
    ("tool", "search_ap_policy", {"query": "duplicate invoice same number formatting"}, "Check the rule."),
    ("tool", "record_resolution", {
        "invoice_id": "INV-1007", "exception_type": "duplicate_invoice", "action": "reject_duplicate",
        "summary": "Same bill as INV-0907 (CL-2026-457), already paid.", "policy_refs": ["AP-6", "AP-7"],
        "agent_name": "Duplicate Invoice Specialist"}, "Record the decision."),
    ("final", {
        "invoice_id": "INV-1007", "exception_type": "duplicate_invoice", "action": "reject_duplicate",
        "summary": "Same bill as INV-0907, already paid.", "evidence": ["Normalised numbers match"],
        "policy_references": ["AP-6", "AP-7"], "recorded_status": "AUTO_RESOLVED"}),
]


async def run_with(model, invoice_id):
    async with runner._tools_server("full", False) as full, runner._tools_server("ro", True) as read_only:
        triage = build_team(model, full, read_only)
        return await runner._resolve_one(triage, invoice_id)


def test_full_duplicate_flow(conn):
    model = ScriptedModel(DUPLICATE_SCRIPT)
    outcome = asyncio.run(run_with(model, "INV-1007"))

    assert outcome["ok"], outcome
    assert outcome["agent"] == "Duplicate Invoice Specialist"
    assert outcome["action"] == "reject_duplicate"
    assert outcome["status"] == "AUTO_RESOLVED"  # 18,400 is under the threshold and releases no money
    assert db.get_invoice(conn, "INV-1007")["status"] == "REJECTED"

    # Least privilege: the triage agent never saw the write tool, the specialist did
    assert "record_resolution" not in model.tools_seen[0]
    assert "record_resolution" in model.tools_seen[3]

    trace = json.loads(db.latest_resolution(conn, "INV-1007")["agent_trace"])
    kinds = [step["kind"] for step in trace]
    assert "handoff" in kinds and "thought" in kinds and "tool_call" in kinds


def test_input_guardrail_skips_invoices_that_are_not_exceptions(conn):
    model = ScriptedModel([])
    outcome = asyncio.run(run_with(model, "INV-1001"))
    assert not outcome["ok"]
    assert "input guardrail" in outcome["error"]
    assert model.calls == 0  # no tokens spent


def test_output_guardrail_blocks_an_answer_that_was_never_recorded(conn):
    script = DUPLICATE_SCRIPT[:5] + [DUPLICATE_SCRIPT[-1]]  # skips record_resolution
    outcome = asyncio.run(run_with(ScriptedModel(script), "INV-1007"))
    assert not outcome["ok"]
    assert "output guardrail" in outcome["error"]
    assert db.get_invoice(conn, "INV-1007")["status"] == "EXCEPTION"
