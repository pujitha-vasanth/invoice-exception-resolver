from resolver import db, matching


def status(conn, invoice_id):
    return db.get_invoice(conn, invoice_id)["status"]


def test_clean_invoices_skip_the_agents(conn):
    assert status(conn, "INV-1001") == "MATCHED"
    assert status(conn, "INV-1009") == "MATCHED"


def test_price_variance_is_calculated_in_code(conn):
    analysis = matching.analyze_invoice(conn, "INV-1003")
    assert analysis["flags"] == ["price_variance"]
    assert analysis["price_variance_amount"] == 12500.0
    assert analysis["lines"][0]["price_variance_pct"] == 11.36


def test_quantity_variance_uses_goods_receipts(conn):
    analysis = matching.analyze_invoice(conn, "INV-1005")
    assert analysis["flags"] == ["quantity_variance"]
    assert analysis["lines"][0]["received_qty"] == 270
    assert analysis["quantity_variance_amount"] == 28500.0


def test_invoice_number_normalising():
    assert matching.normalize_invoice_no("CL/2026/0457") == matching.normalize_invoice_no("CL-2026-457")
    assert matching.normalize_invoice_no("SIS/2026/3318") != matching.normalize_invoice_no("SIS/2026/3290")


def test_reformatted_duplicate_is_caught(conn):
    analysis = matching.analyze_invoice(conn, "INV-1007")
    assert "possible_duplicate" in analysis["flags"]
    candidate = analysis["possible_duplicates"][0]
    assert candidate["invoice_id"] == "INV-0907"
    assert candidate["same_po"] is True


def test_same_amount_on_different_po_is_flagged_for_review_only(conn):
    analysis = matching.analyze_invoice(conn, "INV-1008")
    assert analysis["flags"] == ["possible_duplicate"]
    candidate = analysis["possible_duplicates"][0]
    assert candidate["same_po"] is False
    assert candidate["reasons"] == ["same total amount, 13 days apart"]
