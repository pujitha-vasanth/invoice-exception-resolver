"""SQLite database: schema, connection and simple query helpers.

SQLite keeps the project runnable on a laptop with zero setup. In a real deployment this
layer would point at the ERP (SAP, Dynamics 365, Oracle) or a SQL database instead.
"""

import json
import sqlite3
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS vendors (
    vendor_id      TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    payment_terms  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    po_number   TEXT PRIMARY KEY,
    vendor_id   TEXT NOT NULL REFERENCES vendors(vendor_id),
    po_date     TEXT NOT NULL,
    buyer       TEXT NOT NULL,
    notes       TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS po_lines (
    po_number    TEXT NOT NULL REFERENCES purchase_orders(po_number),
    line_no      INTEGER NOT NULL,
    item_code    TEXT NOT NULL,
    description  TEXT NOT NULL,
    qty          REAL NOT NULL,
    unit_price   REAL NOT NULL,
    PRIMARY KEY (po_number, line_no)
);

CREATE TABLE IF NOT EXISTS goods_receipts (
    grn_number    TEXT PRIMARY KEY,
    po_number     TEXT NOT NULL REFERENCES purchase_orders(po_number),
    receipt_date  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS grn_lines (
    grn_number    TEXT NOT NULL REFERENCES goods_receipts(grn_number),
    line_no       INTEGER NOT NULL,
    qty_received  REAL NOT NULL,
    PRIMARY KEY (grn_number, line_no)
);

CREATE TABLE IF NOT EXISTS invoices (
    invoice_id         TEXT PRIMARY KEY,
    vendor_invoice_no  TEXT NOT NULL,
    vendor_id          TEXT NOT NULL REFERENCES vendors(vendor_id),
    po_number          TEXT NOT NULL REFERENCES purchase_orders(po_number),
    invoice_date       TEXT NOT NULL,
    total_amount       REAL NOT NULL,
    status             TEXT NOT NULL DEFAULT 'NEW',
    match_flags        TEXT NOT NULL DEFAULT '[]',
    vendor_note        TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS invoice_lines (
    invoice_id  TEXT NOT NULL REFERENCES invoices(invoice_id),
    line_no     INTEGER NOT NULL,
    item_code   TEXT NOT NULL,
    qty         REAL NOT NULL,
    unit_price  REAL NOT NULL,
    PRIMARY KEY (invoice_id, line_no)
);

CREATE TABLE IF NOT EXISTS resolutions (
    resolution_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id          TEXT NOT NULL REFERENCES invoices(invoice_id),
    exception_type      TEXT NOT NULL,
    action              TEXT NOT NULL,
    exposure_amount     REAL NOT NULL,
    payment_amount      REAL NOT NULL,
    summary             TEXT NOT NULL,
    policy_refs         TEXT NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL,
    approval_reason     TEXT DEFAULT '',
    agent_name          TEXT DEFAULT '',
    agent_seconds       REAL,
    agent_trace         TEXT DEFAULT '[]',
    created_at          TEXT NOT NULL,
    decided_by          TEXT,
    decided_at          TEXT,
    decision_note       TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    actor     TEXT NOT NULL,
    event     TEXT NOT NULL,
    invoice_id TEXT,
    details   TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    path = config.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def row(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> dict | None:
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None


def audit(conn: sqlite3.Connection, actor: str, event: str, invoice_id: str | None, details: dict) -> None:
    conn.execute(
        "INSERT INTO audit_log (ts, actor, event, invoice_id, details) VALUES (?, ?, ?, ?, ?)",
        (now(), actor, event, invoice_id, json.dumps(details, default=str)),
    )
    conn.commit()


# ---------- read helpers used by the tools and the UI ----------

def get_invoice(conn, invoice_id: str) -> dict | None:
    inv = row(conn, "SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,))
    if not inv:
        return None
    inv["match_flags"] = json.loads(inv["match_flags"])
    inv["lines"] = rows(
        conn, "SELECT line_no, item_code, qty, unit_price FROM invoice_lines WHERE invoice_id = ? ORDER BY line_no",
        (invoice_id,),
    )
    vendor = row(conn, "SELECT name FROM vendors WHERE vendor_id = ?", (inv["vendor_id"],))
    inv["vendor_name"] = vendor["name"] if vendor else ""
    return inv


def get_purchase_order(conn, po_number: str) -> dict | None:
    po = row(conn, "SELECT * FROM purchase_orders WHERE po_number = ?", (po_number,))
    if not po:
        return None
    po["lines"] = rows(
        conn,
        "SELECT line_no, item_code, description, qty, unit_price FROM po_lines WHERE po_number = ? ORDER BY line_no",
        (po_number,),
    )
    return po


def get_goods_receipts(conn, po_number: str) -> list[dict]:
    grns = rows(conn, "SELECT * FROM goods_receipts WHERE po_number = ? ORDER BY receipt_date", (po_number,))
    for g in grns:
        g["lines"] = rows(
            conn, "SELECT line_no, qty_received FROM grn_lines WHERE grn_number = ? ORDER BY line_no",
            (g["grn_number"],),
        )
    return grns


def received_qty_by_line(conn, po_number: str) -> dict[int, float]:
    result = rows(
        conn,
        """SELECT gl.line_no, SUM(gl.qty_received) AS qty
           FROM grn_lines gl JOIN goods_receipts g ON g.grn_number = gl.grn_number
           WHERE g.po_number = ? GROUP BY gl.line_no""",
        (po_number,),
    )
    return {r["line_no"]: r["qty"] for r in result}


def list_invoices(conn) -> list[dict]:
    return rows(
        conn,
        """SELECT i.invoice_id, i.vendor_invoice_no, v.name AS vendor, i.po_number, i.invoice_date,
                  i.total_amount, i.status, i.match_flags
           FROM invoices i JOIN vendors v ON v.vendor_id = i.vendor_id
           ORDER BY i.invoice_id""",
    )


def latest_resolution(conn, invoice_id: str) -> dict | None:
    return row(
        conn, "SELECT * FROM resolutions WHERE invoice_id = ? ORDER BY resolution_id DESC LIMIT 1", (invoice_id,)
    )


def pending_approvals(conn) -> list[dict]:
    return rows(
        conn,
        """SELECT r.*, i.total_amount, v.name AS vendor
           FROM resolutions r
           JOIN invoices i ON i.invoice_id = r.invoice_id
           JOIN vendors v ON v.vendor_id = i.vendor_id
           WHERE r.status = 'AWAITING_APPROVAL' ORDER BY r.resolution_id""",
    )


def audit_trail(conn, invoice_id: str) -> list[dict]:
    return rows(conn, "SELECT ts, actor, event, details FROM audit_log WHERE invoice_id = ? ORDER BY id", (invoice_id,))
