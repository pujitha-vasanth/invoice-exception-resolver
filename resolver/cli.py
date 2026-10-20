"""Command line interface.

Examples (run from the project folder, with the virtual environment active):
    python -m resolver.cli setup              load sample data and run the three-way match
    python -m resolver.cli check              check your .env settings
    python -m resolver.cli list               show all invoices and their status
    python -m resolver.cli resolve INV-1003   let the agents work one exception
    python -m resolver.cli resolve --all      let the agents work every open exception
    python -m resolver.cli queue              show what is waiting for a human
    python -m resolver.cli approve 3 --by "Your Name" --note "Checked"
    python -m resolver.cli reject 3 --by "Your Name" --note "Wrong call"
    python -m resolver.cli show INV-1003      invoice detail, agent steps and audit trail
"""

import argparse
import asyncio
import json
import sys

from . import config, db, matching, seed_data, workflow


def table(rows: list[dict], columns: list[str]) -> None:
    if not rows:
        print("  (nothing to show)")
        return
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in columns}
    print("  " + "  ".join(c.upper().ljust(widths[c]) for c in columns))
    print("  " + "  ".join("-" * widths[c] for c in columns))
    for r in rows:
        print("  " + "  ".join(str(r.get(c, "")).ljust(widths[c]) for c in columns))


def cmd_setup(args) -> None:
    seed_data.reset_database()
    conn = db.connect()
    results = matching.run_matching(conn)
    conn.close()
    print(f"Database created at {config.db_path()}")
    print("Three-way match results:")
    table([{**r, "flags": ", ".join(r["flags"])} for r in results], ["invoice_id", "status", "flags"])
    print("\nNext: python -m resolver.cli resolve --all")


def cmd_check(args) -> None:
    print(f"LLM provider: {config.LLM_PROVIDER}")
    problems = config.llm_config_problems()
    if problems:
        print("Not ready. Fix these in your .env file:")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    model = config.AZURE_OPENAI_DEPLOYMENT if config.LLM_PROVIDER == "azure" else config.OPENAI_MODEL
    print(f"Model / deployment: {model}")
    print(f"Approval threshold: {config.money(config.APPROVAL_THRESHOLD)}")
    print(f"Database: {config.db_path()} ({'found' if config.db_path().exists() else 'not created yet, run setup'})")
    print("Settings look complete.")


def cmd_list(args) -> None:
    conn = db.connect()
    invoices = db.list_invoices(conn)
    conn.close()
    for inv in invoices:
        inv["total"] = f"{inv['total_amount']:,.2f}"
        inv["flags"] = ", ".join(json.loads(inv["match_flags"]))
    table(invoices, ["invoice_id", "vendor", "po_number", "total", "status", "flags"])


def cmd_resolve(args) -> None:
    from .runner import open_exception_ids, resolve_invoices  # imported here so setup works without the SDK

    ids = open_exception_ids() if args.all else [i.upper() for i in args.invoice_ids]
    if not ids:
        print("No open exceptions. Run setup first, or check the list.")
        return

    def show(outcome):
        print(f"\n=== {outcome['invoice_id']} ===")
        if not outcome["ok"]:
            print(f"  NOT RESOLVED: {outcome['error']}")
            return
        print(f"  Agent:   {outcome['agent']}  ({outcome['seconds']}s)")
        print(f"  Action:  {outcome['action']}")
        print(f"  Status:  {outcome['status']}")
        print(f"  At stake: {config.money(outcome['exposure'])}   Payment: {config.money(outcome['payment'])}")
        if outcome["status"] == "AWAITING_APPROVAL":
            print(f"  Why a human must approve: {outcome['approval_reason']}")
        print(f"  Summary: {outcome['summary']}")

    print(f"Working on {len(ids)} invoice(s): {', '.join(ids)}")
    try:
        asyncio.run(resolve_invoices(ids, on_done=show))
    except RuntimeError as exc:
        print(f"Cannot start: {exc}")
        sys.exit(1)
    print("\nNext: python -m resolver.cli queue")


def cmd_queue(args) -> None:
    conn = db.connect()
    pending = db.pending_approvals(conn)
    conn.close()
    for p in pending:
        p["at_stake"] = f"{p['exposure_amount']:,.2f}"
        p["payment"] = f"{p['payment_amount']:,.2f}"
    table(pending, ["resolution_id", "invoice_id", "vendor", "action", "at_stake", "payment"])
    for p in pending:
        print(f"\n  #{p['resolution_id']} {p['invoice_id']}: {p['summary']}\n  Needs approval because: {p['approval_reason']}")


def cmd_decide(args, approve: bool) -> None:
    conn = db.connect()
    try:
        result = workflow.decide(conn, args.resolution_id, approve, args.by, args.note)
    except ValueError as exc:
        print(f"Could not record decision: {exc}")
        sys.exit(1)
    finally:
        conn.close()
    print(f"Resolution #{result['resolution_id']} is now {result['status']}. Invoice status: {result['invoice_status']}")


def cmd_show(args) -> None:
    conn = db.connect()
    invoice_id = args.invoice_id.upper()
    invoice = db.get_invoice(conn, invoice_id)
    if not invoice:
        print(f"{invoice_id} not found")
        return
    print(f"{invoice_id}  {invoice['vendor_name']}  PO {invoice['po_number']}  {config.money(invoice['total_amount'])}")
    print(f"Status: {invoice['status']}   Flags: {', '.join(invoice['match_flags']) or 'none'}")
    if invoice["vendor_note"]:
        print(f"Vendor note: {invoice['vendor_note']}")
    res = db.latest_resolution(conn, invoice_id)
    if res:
        print(f"\nResolution #{res['resolution_id']} by {res['agent_name']}: {res['action']} -> {res['status']}")
        print(f"  {res['summary']}")
        print(f"  Policy: {', '.join(json.loads(res['policy_refs']))}")
        print("\nAgent steps:")
        for step in json.loads(res["agent_trace"] or "[]"):
            label = step["kind"] + (f" {step['tool']}" if step.get("tool") else "")
            print(f"  [{step['agent']}] {label}: {step['text'][:160]}")
    print("\nAudit trail:")
    for a in db.audit_trail(conn, invoice_id):
        print(f"  {a['ts']}  {a['actor']:<28} {a['event']}")
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="resolver", description="Invoice Exception Resolver")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="Create the database with sample data and run the three-way match")
    sub.add_parser("check", help="Check your .env settings")
    sub.add_parser("list", help="List invoices")
    p = sub.add_parser("resolve", help="Let the agents resolve exceptions")
    p.add_argument("invoice_ids", nargs="*")
    p.add_argument("--all", action="store_true")
    sub.add_parser("queue", help="Show resolutions waiting for a human")
    for name in ("approve", "reject"):
        p = sub.add_parser(name, help=f"{name.title()} a resolution as a human approver")
        p.add_argument("resolution_id", type=int)
        p.add_argument("--by", required=True, help="Your name")
        p.add_argument("--note", default="")
    p = sub.add_parser("show", help="Show one invoice in detail")
    p.add_argument("invoice_id")

    args = parser.parse_args()
    if args.command == "resolve" and not args.all and not args.invoice_ids:
        parser.error("give an invoice ID, or --all")
    if args.command not in ("setup", "check") and not config.db_path().exists():
        print("No database yet. Run: python -m resolver.cli setup")
        sys.exit(1)
    handlers = {
        "setup": cmd_setup, "check": cmd_check, "list": cmd_list, "resolve": cmd_resolve, "queue": cmd_queue,
        "approve": lambda a: cmd_decide(a, True), "reject": lambda a: cmd_decide(a, False), "show": cmd_show,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
