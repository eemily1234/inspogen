"""SQLite 連線與資料表（營運系統 P1）。金額、數量皆為整數。"""
import os
import sqlite3

DB_PATH = os.environ.get("OPS_DB", "ops.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS user (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL CHECK (role IN ('owner','staff')),
    kitchen INTEGER NOT NULL DEFAULT 0,   -- 1 = 廚房帳號（role 為 staff，但只能用點餐與廚房頁）
    salt TEXT NOT NULL,
    pin_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

-- 登入失敗紀錄（防止暴力猜 PIN）
CREATE TABLE IF NOT EXISTS login_attempt (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    at TEXT NOT NULL             -- ISO 時間，秒
);
CREATE INDEX IF NOT EXISTS ix_login_attempt_at ON login_attempt(at);

-- 裝置登入連結（平板書籤）：只存雜湊，可撤銷。僅店員／廚房帳號可用，老闆不行。
CREATE TABLE IF NOT EXISTS device_token (
    id INTEGER PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES user(id),
    label TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    last_used_at TEXT,
    revoked INTEGER NOT NULL DEFAULT 0
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

-- 餐點點餐：訂單（一桌一張未結帳訂單，可多次加點）與品項
CREATE TABLE IF NOT EXISTS meal_order (
    id INTEGER PRIMARY KEY,
    order_type TEXT NOT NULL CHECK (order_type IN ('dine_in','takeout')),
    table_no TEXT,               -- 內用桌號
    pickup_no INTEGER,           -- 外帶取餐號（每日從 1 起算）
    note TEXT,                   -- 整單備註（過敏、趕時間…）
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','paid','cancelled')),
    sale_id INTEGER REFERENCES sale(id),
    merged_into INTEGER,         -- 併桌：此單已併入的訂單（status 會是 cancelled）
    guests INTEGER,              -- 用餐人數
    source TEXT NOT NULL DEFAULT 'pos',   -- 訂單來源：pos（店內）/ uber（Uber Eats）
    external_id TEXT,            -- 外部平台的訂單編號（用來防止重複建單）
    external_label TEXT,         -- 外部平台顯示給人看的短編號
    created_by INTEGER REFERENCES user(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS order_line (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES meal_order(id),
    round_no INTEGER NOT NULL DEFAULT 1,   -- 第幾次下單（>1 為加點）
    item_id INTEGER NOT NULL REFERENCES item(id),
    qty INTEGER NOT NULL CHECK (qty > 0),
    unit_price INTEGER NOT NULL,           -- 下單當下的價格
    note TEXT,                              -- 特殊需求（少辣、不要香菜…）
    paid_qty INTEGER NOT NULL DEFAULT 0,   -- 已結帳份數（分開結帳）
    origin TEXT,                            -- 併桌來源，例：併自 B1
    status TEXT NOT NULL DEFAULT 'new'
        CHECK (status IN ('new','preparing','ready','served','cancelled')),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_oline_order ON order_line(order_id);
-- 外部平台（Uber Eats）品項對應：平台品項 → 我們的菜單
CREATE TABLE IF NOT EXISTS external_item_map (
    source TEXT NOT NULL,
    external_item_id TEXT NOT NULL,
    item_id INTEGER NOT NULL REFERENCES item(id),
    PRIMARY KEY (source, external_item_id)
);
-- 外部平台收到的原始資料與處理結果（除錯、對帳、失敗提醒用）
CREATE TABLE IF NOT EXISTS external_event (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT,
    event_type TEXT,
    status TEXT NOT NULL,        -- ok / duplicate / denied / cancelled / failed
    note TEXT,
    payload TEXT,
    received_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_external_event_ext ON external_event(source, external_id);

-- 桌位：分樓層，每桌座位數（預設 4 人）
CREATE TABLE IF NOT EXISTS dining_table (
    id INTEGER PRIMARY KEY,
    floor INTEGER NOT NULL,
    name TEXT NOT NULL UNIQUE,   -- 例：101（1 樓第 1 桌）
    seats INTEGER NOT NULL DEFAULT 4
);
-- 系統設定（例如廚房是否接單）
CREATE TABLE IF NOT EXISTS setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- 每次結帳付了哪些品項（作廢時據此還原）
CREATE TABLE IF NOT EXISTS order_payment_line (
    id INTEGER PRIMARY KEY,
    sale_id INTEGER NOT NULL REFERENCES sale(id),
    order_id INTEGER NOT NULL,
    order_line_id INTEGER NOT NULL REFERENCES order_line(id),
    qty INTEGER NOT NULL CHECK (qty > 0)
);
CREATE INDEX IF NOT EXISTS ix_opl_sale ON order_payment_line(sale_id);

-- Line 記帳：綁定 Line 帳號到系統使用者（僅老闆）
CREATE TABLE IF NOT EXISTS line_user (
    line_user_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES user(id),
    bound_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS bind_code (
    code TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES user(id),
    expires_at TEXT NOT NULL
);
-- Line 記帳紀錄：供「復原」使用；同一個 webhook 事件只處理一次
CREATE TABLE IF NOT EXISTS line_log (
    id INTEGER PRIMARY KEY,
    line_user_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('expense','purchase')),
    ref_id INTEGER NOT NULL,     -- expense: ledger.id；purchase: stock_move.id
    undone INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS line_event (
    event_id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
"""


# 舊資料庫升級：CREATE TABLE IF NOT EXISTS 不會替既有資料表補欄位，這裡逐一補上
MIGRATIONS = [
    ("user", "kitchen", "INTEGER NOT NULL DEFAULT 0"),
    ("meal_order", "merged_into", "INTEGER"),
    ("meal_order", "guests", "INTEGER"),
    ("meal_order", "source", "TEXT NOT NULL DEFAULT 'pos'"),
    ("meal_order", "external_id", "TEXT"),
    ("meal_order", "external_label", "TEXT"),
    ("order_line", "paid_qty", "INTEGER NOT NULL DEFAULT 0"),
    ("order_line", "origin", "TEXT"),
]


def migrate(conn):
    for table, col, decl in MIGRATIONS:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    # 欄位補齊後才能建立：同一個平台的同一張外部訂單只能有一張（防止重複建單）
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_order_external ON meal_order(source, external_id) "
                 "WHERE external_id IS NOT NULL")
    conn.commit()


def connect(path=None):
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # 多個程式（網頁、廚房、webhook）同時讀寫同一個檔案：WAL 讓讀寫不互相擋，busy_timeout 讓碰撞時等待而不是報錯
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.executescript(SCHEMA)
    migrate(conn)
    return conn
