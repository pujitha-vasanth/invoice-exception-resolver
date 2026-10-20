"""MCP server that exposes the AP tools.

Any MCP-compatible client can use these tools: the agents in this project, Claude Desktop,
VS Code, or another team's agent. That is why they live behind MCP and not inside the agent code.

Run it on its own (for testing with an MCP client):   python -m resolver.mcp_server
"""

from mcp.server.mcpserver import MCPServer

from . import db, guardrails, matching, policy_search, workflow

server = MCPServer(
    name="ap-tools",
    instructions="Accounts payable tools: invoices, purchase orders, goods receipts, variance analysis, "
                 "duplicate checks, AP policy search, and recording a resolution.",
)

# The triage agent only gets these. It can look, but it cannot record a decision.
READ_ONLY_TOOLS = [
    "get_invoice", "get_purchase_order", "get_goods_receipts",
    "analyze_variance", "find_duplicate_candidates", "search_ap_policy",
]


def run_tool(tool_name: str, args: dict, work) -> dict:
    """Open the database, run the tool, log the call to the audit table, close the database.
    Errors come back as {"error": "..."} so the agent can read them and change course."""
    conn = db.connect()
    try:
        result = work(conn)
    except (guardrails.GuardrailViolation, ValueError) as exc:
        result = {"error": str(exc)}
    ok = "error" not in result
    db.audit(conn, "agent-tools", f"tool:{tool_name}", args.get("invoice_id"),
             {"args": args, "ok": ok, "error": None if ok else result["error"]})
    conn.close()
    return result


@server.tool()
def get_invoice(invoice_id: str) -> dict:
    """Get an invoice: vendor, PO number, line items, total, status, match flags and any note the vendor wrote."""
    return run_tool("get_invoice", {"invoice_id": invoice_id},
                    lambda conn: db.get_invoice(conn, invoice_id) or {"error": f"Invoice {invoice_id} not found"})


@server.tool()
def get_purchase_order(po_number: str) -> dict:
    """Get a purchase order: agreed items, quantities, unit prices, the buyer and any PO notes
    (for example dispatch details for a balance delivery)."""
    return run_tool("get_purchase_order", {"po_number": po_number},
                    lambda conn: db.get_purchase_order(conn, po_number) or {"error": f"PO {po_number} not found"})


@server.tool()
def get_goods_receipts(po_number: str) -> dict:
    """Get all goods receipt notes (GRNs) for a PO: what was actually received, and when."""
    return run_tool("get_goods_receipts", {"po_number": po_number},
                    lambda conn: {"po_number": po_number, "goods_receipts": db.get_goods_receipts(conn, po_number)})


@server.tool()
def analyze_variance(invoice_id: str) -> dict:
    """Line-by-line three-way match with all maths done in code: price variance (per unit, percent, amount),
    quantity billed versus received, previously invoiced quantity, and match flags.
    Use these numbers as they are. Do not recalculate them."""
    return run_tool("analyze_variance", {"invoice_id": invoice_id},
                    lambda conn: matching.analyze_invoice(conn, invoice_id))


@server.tool()
def find_duplicate_candidates(invoice_id: str) -> dict:
    """Find earlier invoices from the same vendor that might be the same bill (same normalised invoice number,
    or same amount within 60 days). Returns each candidate with its PO, line items and status so you can compare."""
    def work(conn):
        invoice = db.get_invoice(conn, invoice_id)
        if not invoice:
            return {"error": f"Invoice {invoice_id} not found"}
        candidates = matching.find_possible_duplicates(conn, invoice)
        for c in candidates:
            c["lines"] = db.get_invoice(conn, c["invoice_id"])["lines"]
        return {
            "invoice_id": invoice_id,
            "po_number": invoice["po_number"],
            "vendor_invoice_no": invoice["vendor_invoice_no"],
            "normalised_invoice_no": matching.normalize_invoice_no(invoice["vendor_invoice_no"]),
            "this_invoice_lines": invoice["lines"],
            "candidates": candidates,
        }
    return run_tool("find_duplicate_candidates", {"invoice_id": invoice_id}, work)


@server.tool()
def search_ap_policy(query: str) -> dict:
    """Search the accounts payable policy. Returns the most relevant sections with their IDs (for example AP-3).
    Cite these IDs in your resolution."""
    return run_tool("search_ap_policy", {"query": query},
                    lambda conn: {"query": query, "results": policy_search.search(query)})


@server.tool()
def record_resolution(invoice_id: str, exception_type: str, action: str, summary: str,
                      policy_refs: list[str], agent_name: str) -> dict:
    """Record your final decision for an exception. Call this exactly once, at the end.
    exception_type: price_mismatch | quantity_mismatch | duplicate_invoice
    action: approve_payment | short_pay | hold | escalate_to_buyer | reject_duplicate
    summary: 2 to 4 plain sentences for the AP clerk: what you found, the key numbers, and why this action.
    policy_refs: the policy section IDs you relied on, for example ["AP-3", "AP-10"].
    agent_name: your own name.
    The system decides by itself whether a human must approve. You cannot release payments."""
    args = {"invoice_id": invoice_id, "exception_type": exception_type, "action": action, "agent_name": agent_name}
    return run_tool("record_resolution", args, lambda conn: workflow.record_resolution(
        conn, invoice_id, exception_type, action, summary, policy_refs, agent_name))


if __name__ == "__main__":
    server.run()
