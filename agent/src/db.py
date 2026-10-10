"""
SQLite storage for the shop agent.

One file, no server. Every write that touches money or order state runs
inside `transaction()` (BEGIN IMMEDIATE), so two requests can never create
the same invoice or count the same payment twice.

Amounts are stored as integer paise. Quantities are stored as decimal text.
"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    phone       TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL
);

-- Inbound Kapso messages (dedup by message_id) and our outbound replies.
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  TEXT UNIQUE,
    customer    TEXT NOT NULL,
    direction   TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    body        TEXT NOT NULL,
    status      TEXT NOT NULL,
    error       TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id           TEXT PRIMARY KEY,
    customer           TEXT NOT NULL REFERENCES customers(phone),
    source_message_id  TEXT NOT NULL,
    seq                INTEGER NOT NULL,
    status             TEXT NOT NULL
                       CHECK (status IN ('pending', 'cancelled', 'delivered')),
    created_at         TEXT NOT NULL,
    cancelled_at       TEXT,
    delivered_at       TEXT,
    delivered_by       TEXT,
    delivery_note      TEXT,
    UNIQUE (source_message_id, seq)
);

CREATE TABLE IF NOT EXISTS order_items (
    order_id  TEXT NOT NULL REFERENCES orders(order_id),
    line_no   INTEGER NOT NULL,
    item      TEXT NOT NULL,
    qty       TEXT NOT NULL,
    unit      TEXT NOT NULL,
    PRIMARY KEY (order_id, line_no)
);

CREATE TABLE IF NOT EXISTS invoices (
    invoice_id    TEXT PRIMARY KEY,
    order_id      TEXT NOT NULL UNIQUE REFERENCES orders(order_id),
    customer      TEXT NOT NULL REFERENCES customers(phone),
    amount_paise  INTEGER NOT NULL CHECK (amount_paise > 0),
    disputed      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);

-- Prices are copied here at delivery time, so later price changes
-- never alter an existing bill.
CREATE TABLE IF NOT EXISTS invoice_items (
    invoice_id        TEXT NOT NULL REFERENCES invoices(invoice_id),
    line_no           INTEGER NOT NULL,
    item              TEXT NOT NULL,
    qty               TEXT NOT NULL,
    unit              TEXT NOT NULL,
    unit_price_paise  INTEGER NOT NULL,
    line_total_paise  INTEGER NOT NULL,
    PRIMARY KEY (invoice_id, line_no)
);

-- Only shop-verified money lives here.
CREATE TABLE IF NOT EXISTS payments (
    payment_id    TEXT PRIMARY KEY,
    customer      TEXT NOT NULL REFERENCES customers(phone),
    amount_paise  INTEGER NOT NULL CHECK (amount_paise > 0),
    reference     TEXT NOT NULL UNIQUE,
    method        TEXT,
    invoice_id    TEXT,
    verified_by   TEXT,
    note          TEXT,
    verified_at   TEXT NOT NULL
);

-- How each verified payment is spread over invoices. Unallocated
-- payment money is the customer's credit.
CREATE TABLE IF NOT EXISTS allocations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id    TEXT NOT NULL REFERENCES payments(payment_id),
    invoice_id    TEXT NOT NULL REFERENCES invoices(invoice_id),
    amount_paise  INTEGER NOT NULL CHECK (amount_paise > 0),
    created_at    TEXT NOT NULL
);

-- Things the customer *said* that are not trusted facts.
CREATE TABLE IF NOT EXISTS claims (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    customer      TEXT NOT NULL,
    message_id    TEXT NOT NULL,
    kind          TEXT NOT NULL
                  CHECK (kind IN ('PAYMENT_CLAIM', 'PROMISE', 'DELIVERY_CLAIM')),
    amount_paise  INTEGER,
    status        TEXT NOT NULL DEFAULT 'unverified',
    created_at    TEXT NOT NULL,
    UNIQUE (message_id, kind)
);

CREATE TABLE IF NOT EXISTS review_flags (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    customer           TEXT NOT NULL,
    order_id           TEXT,
    invoice_id         TEXT,
    reason             TEXT NOT NULL,
    source_message_id  TEXT,
    created_at         TEXT NOT NULL,
    resolved_at        TEXT,
    resolution_note    TEXT,
    UNIQUE (source_message_id, reason)
);

-- One row per outgoing bill/receipt message, so retries never resend
-- something that already went out.
CREATE TABLE IF NOT EXISTS notifications (
    ref_id        TEXT NOT NULL,
    kind          TEXT NOT NULL,
    customer      TEXT NOT NULL,
    status        TEXT NOT NULL
                  CHECK (status IN ('pending', 'sending', 'sent', 'failed', 'skipped')),
    amount_paise  INTEGER,
    attempts      INTEGER NOT NULL DEFAULT 0,
    error         TEXT,
    provider_message_id TEXT,
    updated_at    TEXT NOT NULL,
    sent_at       TEXT,
    PRIMARY KEY (ref_id, kind)
);
"""


def db_path():
    return os.getenv("SHOP_DB_PATH") or str(BASE_DIR / "shop.db")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path=None):
    conn = sqlite3.connect(
        path or db_path(),
        isolation_level=None,  # we issue BEGIN/COMMIT ourselves
        timeout=10,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn):
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(notifications)")}
    if "provider_message_id" not in columns:
        conn.execute("ALTER TABLE notifications ADD COLUMN provider_message_id TEXT")


@contextmanager
def transaction(conn):
    """Exclusive write transaction; rolls back on any error."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
