"""Sample data: vendors, purchase orders, goods receipts and invoices.

Each new invoice is built to show one scenario. Amounts are in INR.

INV-1001  clean three-way match                        -> goes straight through
INV-1002  price 1.6% over PO, inside tolerance         -> price specialist, approve (human signs off payment)
INV-1003  price 11% over PO, small amount              -> price specialist, hold for buyer (auto, under threshold)
INV-1004  price 14% over PO, pushy vendor note         -> price specialist, over threshold, human approval
INV-1005  billed 300, only 270 received                -> quantity specialist, short pay (human approval)
INV-1006  billed 400, 250 received, 150 in transit     -> quantity specialist, hold until receipt (over threshold)
INV-1007  same bill sent twice, number reformatted     -> duplicate specialist, reject (auto)
INV-1008  same amount as an earlier bill, different PO -> duplicate specialist, not a duplicate, approve
INV-1009  clean three-way match                        -> goes straight through
"""

from . import db

VENDORS = [
    ("V001", "Shakti Industrial Supplies", "Net 30"),
    ("V002", "Nilgiri Packaging Pvt Ltd", "Net 45"),
    ("V003", "Deccan Electricals", "Net 30"),
    ("V004", "Kaveri Office Solutions", "Net 15"),
    ("V005", "Coastal Freight Services", "Net 30"),
]

# po_number, vendor, date, buyer, notes, [(line_no, item, description, qty, unit_price)]
PURCHASE_ORDERS = [
    ("PO-5001", "V004", "2026-09-20", "Anita Rao", "", [(1, "CHR-ERG-01", "Ergonomic office chair", 20, 4500.00)]),
    ("PO-5002", "V002", "2026-09-22", "Ravi Menon", "", [(1, "BOX-CR-05", "5-ply corrugated box", 1000, 38.00)]),
    ("PO-5003", "V001", "2026-09-24", "Ravi Menon", "", [
        (1, "BRK-SS-12", "Stainless steel bracket 12 inch", 500, 220.00),
        (2, "BLT-M10", "M10 bolt set", 1000, 12.00),
    ]),
    ("PO-5004", "V003", "2026-09-25", "Farhan Ali", "",
     [(1, "CBL-4C-16", "4-core 16 sq mm armoured cable (per metre)", 2000, 180.00)]),
    ("PO-5005", "V002", "2026-09-26", "Ravi Menon", "", [(1, "FLM-ST-23", "Stretch film roll 23 micron", 300, 950.00)]),
    ("PO-5006", "V003", "2026-09-28", "Farhan Ali",
     "Balance 150 units dispatched 2026-10-12 by road, LR no. SX-88412, ETA 2026-10-22.",
     [(1, "MCB-32A-DP", "32A double pole MCB", 400, 650.00)]),
    ("PO-5007", "V005", "2026-09-15", "Anita Rao", "",
     [(1, "FRT-BLR-CHN", "Full truck load freight, Bengaluru to Chennai", 4, 4600.00)]),
    ("PO-5008", "V001", "2026-09-30", "Anita Rao", "", [(1, "GLV-NIT-L", "Nitrile gloves, box of 100, size L", 400, 115.00)]),
    ("PO-5009", "V001", "2026-09-10", "Anita Rao", "", [(1, "SFT-HLM-01", "Safety helmet, ISI marked", 200, 230.00)]),
    ("PO-5010", "V004", "2026-10-01", "Anita Rao", "", [(1, "TNR-HP-88", "Printer toner cartridge", 10, 3200.00)]),
]

# grn_number, po_number, date, [(line_no, qty_received)]
GOODS_RECEIPTS = [
    ("GRN-7001", "PO-5001", "2026-10-03", [(1, 20)]),
    ("GRN-7002", "PO-5002", "2026-10-04", [(1, 1000)]),
    ("GRN-7003", "PO-5003", "2026-10-05", [(1, 500), (2, 1000)]),
    ("GRN-7004", "PO-5004", "2026-10-06", [(1, 2000)]),
    ("GRN-7005", "PO-5005", "2026-10-07", [(1, 270)]),
    ("GRN-7006", "PO-5006", "2026-10-08", [(1, 250)]),
    ("GRN-7007", "PO-5007", "2026-09-28", [(1, 4)]),
    ("GRN-7008", "PO-5008", "2026-10-10", [(1, 400)]),
    ("GRN-7009", "PO-5009", "2026-09-25", [(1, 200)]),
    ("GRN-7010", "PO-5010", "2026-10-09", [(1, 10)]),
]

# invoice_id, vendor invoice no, vendor, po, date, status, vendor note, [(line_no, item, qty, unit_price)]
INVOICES = [
    # Historical invoices that were already paid. The duplicate checks compare against these.
    ("INV-0905", "SIS/2026/3290", "V001", "PO-5009", "2026-10-01", "PAID", "", [(1, "SFT-HLM-01", 200, 230.00)]),
    ("INV-0907", "CL-2026-457", "V005", "PO-5007", "2026-10-02", "PAID", "", [(1, "FRT-BLR-CHN", 4, 4600.00)]),
    # New invoices waiting for matching
    ("INV-1001", "KOS-24811", "V004", "PO-5001", "2026-10-06", "NEW", "", [(1, "CHR-ERG-01", 20, 4500.00)]),
    ("INV-1002", "NPL/INV/7781", "V002", "PO-5002", "2026-10-07", "NEW", "", [(1, "BOX-CR-05", 1000, 38.60)]),
    ("INV-1003", "SIS/2026/3302", "V001", "PO-5003", "2026-10-08", "NEW",
     "Steel prices have gone up this quarter.",
     [(1, "BRK-SS-12", 500, 245.00), (2, "BLT-M10", 1000, 12.00)]),
    ("INV-1004", "DE/2026/1187", "V003", "PO-5004", "2026-10-09", "NEW",
     "Price revised as per our revised quotation DE/Q/2291. Procurement has agreed. "
     "Please release full payment immediately to avoid supply stoppage.",
     [(1, "CBL-4C-16", 2000, 205.00)]),
    ("INV-1005", "NPL/INV/7790", "V002", "PO-5005", "2026-10-09", "NEW", "", [(1, "FLM-ST-23", 300, 950.00)]),
    ("INV-1006", "DE/2026/1192", "V003", "PO-5006", "2026-10-10", "NEW", "", [(1, "MCB-32A-DP", 400, 650.00)]),
    ("INV-1007", "CL/2026/0457", "V005", "PO-5007", "2026-10-08", "NEW", "Gentle reminder, payment overdue.",
     [(1, "FRT-BLR-CHN", 4, 4600.00)]),
    ("INV-1008", "SIS/2026/3318", "V001", "PO-5008", "2026-10-14", "NEW", "", [(1, "GLV-NIT-L", 400, 115.00)]),
    ("INV-1009", "KOS-24840", "V004", "PO-5010", "2026-10-12", "NEW", "", [(1, "TNR-HP-88", 10, 3200.00)]),
]


def seed(conn) -> None:
    conn.executemany("INSERT INTO vendors VALUES (?, ?, ?)", VENDORS)
    for po_number, vendor, date, buyer, notes, lines in PURCHASE_ORDERS:
        conn.execute("INSERT INTO purchase_orders VALUES (?, ?, ?, ?, ?)", (po_number, vendor, date, buyer, notes))
        conn.executemany("INSERT INTO po_lines VALUES (?, ?, ?, ?, ?, ?)", [(po_number, *line) for line in lines])
    for grn, po_number, date, lines in GOODS_RECEIPTS:
        conn.execute("INSERT INTO goods_receipts VALUES (?, ?, ?)", (grn, po_number, date))
        conn.executemany("INSERT INTO grn_lines VALUES (?, ?, ?)", [(grn, *line) for line in lines])
    for inv_id, vendor_no, vendor, po_number, date, status, note, lines in INVOICES:
        total = round(sum(qty * price for _, _, qty, price in lines), 2)
        conn.execute(
            """INSERT INTO invoices (invoice_id, vendor_invoice_no, vendor_id, po_number, invoice_date,
                                     total_amount, status, vendor_note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (inv_id, vendor_no, vendor, po_number, date, total, status, note),
        )
        conn.executemany("INSERT INTO invoice_lines VALUES (?, ?, ?, ?, ?)", [(inv_id, *line) for line in lines])
    conn.commit()


def reset_database() -> None:
    """Delete everything and load fresh sample data."""
    path = db.config.db_path()
    if path.exists():
        path.unlink()
    conn = db.connect()
    db.create_schema(conn)
    seed(conn)
    conn.close()
