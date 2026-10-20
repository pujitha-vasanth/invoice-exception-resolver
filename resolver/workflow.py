"""State changes: recording an agent's resolution and recording a human decision.

record_resolution is exposed to agents (through the MCP server).
decide is NOT exposed to agents. Only a person can call it, from the CLI or the web app.
"""

import json

from . import db, guardrails, matching

# What the invoice status becomes once an action is final
FINAL_INVOICE_STATUS = {
    "approve_payment": "READY_TO_PAY",
    "short_pay": "READY_TO_PAY",
    "hold": "ON_HOLD",
    "escalate_to_buyer": "ESCALATED",
    "reject_duplicate": "REJECTED",
}


def record_resolution(conn, invoice_id: str, exception_type: str, action: str, summary: str,
                      policy_refs: list[str], agent_name: str = "agent") -> dict:
    invoice = db.get_invoice(conn, invoice_id)
    if not invoice:
        raise guardrails.GuardrailViolation(f"Invoice {invoice_id} not found")
    if invoice["status"] != "EXCEPTION":
        raise guardrails.GuardrailViolation(
            f"Invoice {invoice_id} has status {invoice['status']}. Only open exceptions can be resolved."
        )

    analysis = matching.analyze_invoice(conn, invoice_id)
    needs_approval, reason, exposure, payment = guardrails.approval_check(analysis, action)

    status = "AWAITING_APPROVAL" if needs_approval else "AUTO_RESOLVED"
    invoice_status = "AWAITING_APPROVAL" if needs_approval else FINAL_INVOICE_STATUS[action]

    cur = conn.execute(
        """INSERT INTO resolutions (invoice_id, exception_type, action, exposure_amount, payment_amount, summary,
                                    policy_refs, status, approval_reason, agent_name, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (invoice_id, exception_type, action, exposure, payment, summary, json.dumps(policy_refs),
         status, reason, agent_name, db.now()),
    )
    conn.execute("UPDATE invoices SET status = ? WHERE invoice_id = ?", (invoice_status, invoice_id))
    conn.commit()
    db.audit(conn, agent_name, "resolution_recorded", invoice_id, {
        "action": action, "exposure": exposure, "payment": payment, "status": status, "approval_reason": reason,
    })
    return {
        "resolution_id": cur.lastrowid, "status": status, "invoice_status": invoice_status,
        "exposure_amount": exposure, "payment_amount": payment,
        "approval_reason": reason or "none, within agent authority",
    }


def decide(conn, resolution_id: int, approve: bool, decided_by: str, note: str = "") -> dict:
    """A human approves or rejects an agent's recommendation."""
    if not decided_by.strip():
        raise ValueError("An approver name is required")
    res = db.row(conn, "SELECT * FROM resolutions WHERE resolution_id = ?", (resolution_id,))
    if not res:
        raise ValueError(f"Resolution {resolution_id} not found")
    if res["status"] != "AWAITING_APPROVAL":
        raise ValueError(f"Resolution {resolution_id} is {res['status']}, not awaiting approval")

    if approve:
        res_status, invoice_status = "APPROVED", FINAL_INVOICE_STATUS[res["action"]]
    else:
        res_status, invoice_status = "REJECTED_BY_APPROVER", "MANUAL_REVIEW"

    conn.execute(
        "UPDATE resolutions SET status = ?, decided_by = ?, decided_at = ?, decision_note = ? WHERE resolution_id = ?",
        (res_status, decided_by, db.now(), note, resolution_id),
    )
    conn.execute("UPDATE invoices SET status = ? WHERE invoice_id = ?", (invoice_status, res["invoice_id"]))
    conn.commit()
    db.audit(conn, decided_by, "human_decision", res["invoice_id"], {
        "resolution_id": resolution_id, "approved": approve, "note": note, "invoice_status": invoice_status,
    })
    return {"resolution_id": resolution_id, "status": res_status, "invoice_status": invoice_status}


def save_run_details(conn, invoice_id: str, agent_name: str, seconds: float, trace: list[dict]) -> None:
    """Attach timing and the agent's step-by-step trail to the latest resolution."""
    res = db.latest_resolution(conn, invoice_id)
    if not res:
        return
    conn.execute(
        "UPDATE resolutions SET agent_name = ?, agent_seconds = ?, agent_trace = ? WHERE resolution_id = ?",
        (agent_name, round(seconds, 1), json.dumps(trace), res["resolution_id"]),
    )
    conn.commit()
