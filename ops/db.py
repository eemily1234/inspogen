"""SQLite 連線與資料表（營運系統 P1）。金額、數量皆為整數。"""
import os
import sqlite3

DB_PATH = os.environ.get("OPS_DB", "ops.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS user (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL CHECK (role IN ('owner','staff')),
    salt TEXT NOT NULL,
    pin_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS item (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL CHECK (type IN ('product','meal')),
    category TEXT NOT NULL CHECK (category IN ('health','organic','meal')),
    unit TEXT NOT NULL DEFAULT '件',
    price INTEGER NOT NULL CHECK (price >= 0),
    cost INTEGER NOT NULL DEFAULT 0 CHECK (cost >= 0),
    safety_stock INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    CHECK ((type = 'meal' AND category = 'meal')
        OR (type = 'product' AND category IN ('health','organic')))
);

-- 庫存異動：只新增、不修改。現有庫存 = SUM(qty_delta)。
CREATE TABLE IF NOT EXISTS stock_move (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES item(id),
    qty_delta INTEGER NOT NULL,
    move_type TEXT NOT NULL CHECK (move_type IN
        ('purchase','sale','void','return','adjust','scrap')),
    batch_no TEXT,
    expiry TEXT,                 -- YYYY-MM-DD
    ref_type TEXT,
    ref_id INTEGER,
    note TEXT,
    created_by INTEGER REFERENCES user(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_move_item ON stock_move(item_id);

CREATE TABLE IF NOT EXISTS sale (
    id INTEGER PRIMARY KEY,
    total INTEGER NOT NULL,
    pay_method TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'done' CHECK (status IN ('done','void')),
    invoice_no TEXT,             -- 預留：電子發票
    created_by INTEGER REFERENCES user(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS sale_line (
    id INTEGER PRIMARY KEY,
    sale_id INTEGER NOT NULL REFERENCES sale(id),
    item_id INTEGER NOT NULL REFERENCES item(id),
    qty INTEGER NOT NULL CHECK (qty > 0),
    unit_price INTEGER NOT NULL,
    unit_cost INTEGER NOT NULL
);

-- 帳務：income 收入 / cost 進貨成本 / expense 支出。更正用反向分錄。
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('income','cost','expense')),
    amount INTEGER NOT NULL,     -- 可為負（反向分錄）
    source TEXT NOT NULL,        -- pos / purchase / manual
    ref_type TEXT,
    ref_id INTEGER,
    note TEXT,
    created_by INTEGER REFERENCES user(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
"""


def connect(path=None):
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn
