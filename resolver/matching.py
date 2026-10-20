"""Deterministic three-way match: invoice vs purchase order vs goods receipt.

This runs BEFORE any agent. Clean invoices go straight through and never touch the LLM.
Only invoices with flags become exceptions for the agents to investigate.
All the arithmetic lives here, in code, so the LLM never has to do maths.
"""

import json
import re
from datetime import date

from . import db

DUPLICATE_WINDOW_DAYS = 60


def normalize_invoice_no(value: str) -> str:
    """'CL/2026/0457' and 'CL-2026-457' both become 'CL2026457'."""
    parts = re.findall(r"[A-Z]+|\d+", value.upper())
    return "".join(p.lstrip("0") or "0" if p.isdigit() else p for p in parts)


def find_possible_duplicates(conn, invoice: dict) -> list[dict]:
    others = db.rows(
        conn,
        """SELECT invoice_id, vendor_invoice_no, po_number, invoice_date, total_amount, status
           FROM invoices WHERE vendor_id = ? AND invoice_id != ? AND status != 'REJECTED'""",
        (invoice["vendor_id"], invoice["invoice_id"]),
    )
    this_no = normalize_invoice_no(invoice["vendor_invoice_no"])
    this_date = date.fromisoformat(invoice["invoice_date"])
    candidates = []
    for other in others:
        reasons = []
        if normalize_invoice_no(other["vendor_invoice_no"]) == this_no:
            reasons.append("same vendor invoice number after normalising")
        days_apart = abs((date.fromisoformat(other["invoice_date"]) - this_date).days)
        if abs(other["total_amount"] - invoice["total_amount"]) < 0.01 and days_apart <= DUPLICATE_WINDOW_DAYS:
            reasons.append(f"same total amount, {days_apart} days apart")
        if reasons:
            candidates.append({**other, "days_apart": days_apart, "same_po": other["po_number"] == invoice["po_number"],
                               "reasons": reasons})
    return candidates


def analyze_invoice(conn, invoice_id: str) -> dict:
    """Line-by-line comparison of an invoice against its PO and goods receipts."""
    invoice = db.get_invoice(conn, invoice_id)
    if not invoice:
        raise ValueError(f"Invoice {invoice_id} not found")
    po = db.get_purchase_order(conn, invoice["po_number"])
    if not po:
        raise ValueError(f"Purchase order {invoice['po_number']} not found")

    po_lines = {line["line_no"]: line for line in po["lines"]}
    received = db.received_qty_by_line(conn, invoice["po_number"])

    lines, flags = [], set()
    price_total = qty_total = 0.0
    for il in invoice["lines"]:
        pl = po_lines.get(il["line_no"])
        if not pl or pl["item_code"] != il["item_code"]:
            flags.add("line_not_on_po")
            lines.append({"line_no": il["line_no"], "item_code": il["item_code"], "problem": "item not on the PO"})
            continue

        prev = db.row(
            conn,
            """SELECT COALESCE(SUM(l.qty), 0) AS qty FROM invoice_lines l JOIN invoices i ON i.invoice_id = l.invoice_id
               WHERE i.po_number = ? AND l.line_no = ? AND i.invoice_id != ?
                 AND i.status NOT IN ('REJECTED', 'NEW')""",
            (invoice["po_number"], il["line_no"], invoice_id),
        )["qty"]
        qty_received = received.get(il["line_no"], 0.0)
        available = max(0.0, qty_received - prev)

        price_diff = round(il["unit_price"] - pl["unit_price"], 4)
        price_var_amount = round(price_diff * il["qty"], 2)
        price_var_pct = round(price_diff / pl["unit_price"] * 100, 2) if pl["unit_price"] else 0.0
        qty_over = max(0.0, il["qty"] - available)
        qty_var_amount = round(qty_over * il["unit_price"], 2)

        if abs(price_var_amount) >= 0.01:
            flags.add("price_variance")
        if qty_over > 0:
            flags.add("quantity_variance")
        price_total += price_var_amount
        qty_total += qty_var_amount

        lines.append({
            "line_no": il["line_no"], "item_code": il["item_code"], "description": pl["description"],
            "po_qty": pl["qty"], "po_unit_price": pl["unit_price"],
            "invoiced_qty": il["qty"], "invoiced_unit_price": il["unit_price"],
            "received_qty": qty_received, "previously_invoiced_qty": prev, "qty_available_to_bill": available,
            "price_variance_per_unit": price_diff, "price_variance_pct": price_var_pct,
            "price_variance_amount": price_var_amount,
            "qty_over_billed": qty_over, "quantity_variance_amount": qty_var_amount,
        })

    duplicates = find_possible_duplicates(conn, invoice)
    if duplicates:
        flags.add("possible_duplicate")

    return {
        "invoice_id": invoice_id,
        "vendor_id": invoice["vendor_id"],
        "po_number": invoice["po_number"],
        "invoice_total": invoice["total_amount"],
        "lines": lines,
        "price_variance_amount": round(price_total, 2),
        "quantity_variance_amount": round(qty_total, 2),
        "possible_duplicates": duplicates,
        "flags": sorted(flags),
    }


def run_matching(conn) -> list[dict]:
    """Match every NEW invoice. Returns one summary row per invoice."""
    results = []
    new_ids = [r["invoice_id"] for r in db.rows(conn, "SELECT invoice_id FROM invoices WHERE status = 'NEW' ORDER BY invoice_id")]
    for invoice_id in new_ids:
        analysis = analyze_invoice(conn, invoice_id)
        status = "EXCEPTION" if analysis["flags"] else "MATCHED"
        conn.execute(
            "UPDATE invoices SET status = ?, match_flags = ? WHERE invoice_id = ?",
            (status, json.dumps(analysis["flags"]), invoice_id),
        )
        conn.commit()
        db.audit(conn, "match-engine", "three_way_match", invoice_id, {"status": status, "flags": analysis["flags"]})
        results.append({"invoice_id": invoice_id, "status": status, "flags": analysis["flags"]})
    return results
