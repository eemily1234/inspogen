"""營運系統商業規則：錢貨同步、批號效期（FEFO）、權限、報表。

所有會動到錢或庫存的操作，都在同一個資料庫交易內完成。
"""
import hashlib
import hmac
import os
import secrets
from datetime import date, datetime, timedelta

MIN_PIN_LENGTH = int(os.environ.get("OPS_MIN_PIN_LENGTH", "4"))   # 對外上線建議設 6
MAX_LOGIN_FAILS = 5          # 同一姓名 10 分鐘內最多失敗次數
GLOBAL_LOGIN_FAILS = 30      # 全部姓名合計 10 分鐘內最多失敗次數
LOCK_MINUTES = 10

OWNER = "owner"
STAFF = "staff"
KITCHEN = "kitchen"   # 廚房帳號：資料庫存為 staff + kitchen=1


class OpsError(ValueError):
    """可直接顯示給使用者的錯誤。"""


# ───────────────────────── 使用者與權限 ─────────────────────────
def _hash(pin, salt):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 100_000).hex()


def has_users(conn):
    return conn.execute("SELECT 1 FROM user LIMIT 1").fetchone() is not None


def create_user(conn, name, role, pin):
    name = (name or "").strip()
    if not name:
        raise OpsError("請輸入姓名")
    if role not in (OWNER, STAFF, KITCHEN):
        raise OpsError("角色錯誤")
    if not (pin or "").isdigit() or len(pin) < MIN_PIN_LENGTH:
        raise OpsError(f"PIN 需為至少 {MIN_PIN_LENGTH} 位數字")
    if conn.execute("SELECT COUNT(*) FROM user WHERE active=1").fetchone()[0] >= 4:
        raise OpsError("使用者已達 4 人上限")
    salt = secrets.token_hex(8)
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO user(name, role, kitchen, salt, pin_hash) VALUES (?,?,?,?,?)",
                (name, STAFF if role == KITCHEN else role, 1 if role == KITCHEN else 0,
                 salt, _hash(pin, salt)),
            )
    except Exception as e:  # UNIQUE 衝突
        raise OpsError("姓名已存在") from e
    return cur.lastrowid


def authenticate(conn, name, pin, now=None):
    """驗證姓名與 PIN；成功回傳使用者 dict，失敗回傳 None。

    防暴力破解：同一姓名 10 分鐘內失敗 5 次、或全部姓名合計失敗 30 次，就鎖定 10 分鐘
    （鎖定期間連正確的 PIN 也不接受），丟出 OpsError。
    """
    now = now or datetime.now()
    name = (name or "").strip()
    since = (now - timedelta(minutes=LOCK_MINUTES)).isoformat(timespec="seconds")
    fails = conn.execute("SELECT COUNT(*) FROM login_attempt WHERE name=? AND at>=?", (name, since)).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM login_attempt WHERE at>=?", (since,)).fetchone()[0]
    if fails >= MAX_LOGIN_FAILS or total >= GLOBAL_LOGIN_FAILS:
        raise OpsError(f"登入失敗次數過多，請 {LOCK_MINUTES} 分鐘後再試")
    row = conn.execute("SELECT * FROM user WHERE name=? AND active=1", (name,)).fetchone()
    # 即使姓名不存在也做一次雜湊，避免從回應時間猜出帳號是否存在
    ok = hmac.compare_digest(row["pin_hash"] if row else _hash("", "x"),
                             _hash(pin or "", row["salt"] if row else "x")) and row is not None
    with conn:
        if ok:
            conn.execute("DELETE FROM login_attempt WHERE name=?", (name,))
        else:
            conn.execute("INSERT INTO login_attempt(name, at) VALUES (?,?)",
                         (name, now.isoformat(timespec="seconds")))
            conn.execute("DELETE FROM login_attempt WHERE at < ?",
                         ((now - timedelta(days=1)).isoformat(timespec="seconds"),))
    return dict(row) if ok else None


# ───────────────────────── 裝置登入連結（平板書籤） ─────────────────────────
def _token_hash(token):
    return hashlib.sha256((token or "").encode()).hexdigest()


def create_device_token(conn, owner, user_id, label=None):
    """老闆替店員／廚房帳號產生裝置登入連結用的密鑰。回傳明文密鑰（只會顯示這一次）。"""
    if owner["role"] != OWNER or is_kitchen(owner):
        raise OpsError("只有老闆可以產生裝置登入連結")
    target = conn.execute("SELECT * FROM user WHERE id=? AND active=1", (user_id,)).fetchone()
    if not target:
        raise OpsError("找不到使用者")
    if target["role"] == OWNER:
        raise OpsError("老闆帳號不能使用裝置登入連結，請用姓名與 PIN 登入")
    token = secrets.token_urlsafe(24)
    with conn:
        conn.execute("INSERT INTO device_token(token_hash,user_id,label) VALUES (?,?,?)",
                     (_token_hash(token), user_id, (label or "").strip() or None))
    return token


def user_from_device_token(conn, token):
    """用裝置密鑰換回使用者；已撤銷、帳號停用、老闆帳號都不行。"""
    if not token:
        return None
    row = conn.execute(
        "SELECT d.id AS did, u.* FROM device_token d JOIN user u ON u.id=d.user_id "
        "WHERE d.token_hash=? AND d.revoked=0 AND u.active=1 AND u.role!='owner'",
        (_token_hash(token),)).fetchone()
    if not row:
        return None
    with conn:
        conn.execute("UPDATE device_token SET last_used_at=datetime('now','localtime') WHERE id=?", (row["did"],))
    user = dict(row)
    user.pop("did")
    return user


def list_device_tokens(conn):
    return [dict(r) for r in conn.execute(
        "SELECT d.id, d.label, d.created_at, d.last_used_at, d.revoked, u.name AS user_name "
        "FROM device_token d JOIN user u ON u.id=d.user_id ORDER BY d.id DESC")]


def revoke_device_token(conn, token_id):
    with conn:
        conn.execute("UPDATE device_token SET revoked=1 WHERE id=?", (token_id,))


def is_kitchen(user):
    return bool(user.get("kitchen"))


def can(user, action):
    """權限：店員可 POS 與看庫存（不含成本）；老闆全部；廚房帳號什麼後台功能都沒有。"""
    if is_kitchen(user):
        return False
    staff_ok = {"pos", "view_stock"}
    return user["role"] == OWNER or action in staff_ok


def meal_pages(user):
    """餐點系統各角色能進的頁面（依序，第一個是預設首頁）。
    老闆：全部；店員：外場、點餐、廚房、結帳；廚房帳號：只有點餐與廚房。"""
    if is_kitchen(user):
        return ["kitchen", "order"]
    if user["role"] == OWNER:
        return ["floor", "order", "kitchen", "orders", "settings"]
    return ["floor", "order", "kitchen", "orders"]


# ───────────────────────── 品項 ─────────────────────────
def add_item(conn, name, category, price, cost=0, unit="件", safety_stock=0):
    name = (name or "").strip()
    if not name:
        raise OpsError("請輸入品名")
    if category not in ("health", "organic", "meal"):
        raise OpsError("分類錯誤")
    if price < 0 or cost < 0 or safety_stock < 0:
        raise OpsError("金額與數量不可為負")
    typ = "meal" if category == "meal" else "product"
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO item(name,type,category,unit,price,cost,safety_stock)"
                " VALUES (?,?,?,?,?,?,?)",
                (name, typ, category, unit, int(price), int(cost), int(safety_stock)),
            )
    except Exception as e:
        raise OpsError("品名已存在") from e
    return cur.lastrowid


def list_items(conn, active_only=True):
    q = "SELECT * FROM item" + (" WHERE active=1" if active_only else "") + " ORDER BY category, name"
    return [dict(r) for r in conn.execute(q)]


def get_item(conn, item_id):
    row = conn.execute("SELECT * FROM item WHERE id=?", (item_id,)).fetchone()
    if not row:
        raise OpsError("找不到品項")
    return dict(row)


def update_item(conn, item_id, price=None, safety_stock=None, active=None):
    sets, args = [], []
    for col, val in (("price", price), ("safety_stock", safety_stock), ("active", active)):
        if val is not None:
            if col != "active" and val < 0:
                raise OpsError("不可為負")
            sets.append(f"{col}=?")
            args.append(int(val))
    if sets:
        with conn:
            conn.execute(f"UPDATE item SET {', '.join(sets)} WHERE id=?", (*args, item_id))


# ───────────────────────── 庫存 ─────────────────────────
def stock_level(conn, item_id):
    return conn.execute(
        "SELECT COALESCE(SUM(qty_delta),0) FROM stock_move WHERE item_id=?", (item_id,)
    ).fetchone()[0]


def batches(conn, item_id):
    """有庫存的批次，效期最近者在前（無效期排最後）。"""
    rows = conn.execute(
        """SELECT batch_no, expiry, SUM(qty_delta) AS qty FROM stock_move
           WHERE item_id=? GROUP BY batch_no, expiry HAVING SUM(qty_delta) > 0
           ORDER BY expiry IS NULL, expiry, batch_no""",
        (item_id,),
    )
    return [dict(r) for r in rows]


def stock_overview(conn, with_cost=False):
    items = []
    for it in list_items(conn):
        if it["type"] != "product":
            continue
        qty = stock_level(conn, it["id"])
        row = {
            "id": it["id"], "name": it["name"], "category": it["category"],
            "unit": it["unit"], "qty": qty, "safety_stock": it["safety_stock"],
            "low": qty <= it["safety_stock"],
        }
        if with_cost:
            row["cost"] = it["cost"]
            row["value"] = qty * it["cost"]
        items.append(row)
    return items


def low_stock(conn):
    return [r for r in stock_overview(conn) if r["low"]]


def expiring(conn, days=30, today=None):
    """即將到期（含已過期）的批次。"""
    today = today or date.today()
    limit = (today + timedelta(days=days)).isoformat()
    rows = conn.execute(
        """SELECT i.name, m.batch_no, m.expiry, SUM(m.qty_delta) AS qty
           FROM stock_move m JOIN item i ON i.id = m.item_id
           WHERE m.expiry IS NOT NULL
           GROUP BY m.item_id, m.batch_no, m.expiry
           HAVING SUM(m.qty_delta) > 0 AND m.expiry <= ?
           ORDER BY m.expiry""",
        (limit,),
    )
    return [dict(r) for r in rows]


def _require_product(item):
    if item["type"] != "product":
        raise OpsError(f"「{item['name']}」是餐點，不管理庫存")


def _check_batch(item, batch_no, expiry):
    if item["category"] == "health":
        if not (batch_no or "").strip():
            raise OpsError("保健品必須填寫批號")
        if not expiry:
            raise OpsError("保健品必須填寫有效期限")
    if expiry:
        try:
            date.fromisoformat(str(expiry))
        except ValueError as e:
            raise OpsError("有效期限格式需為 YYYY-MM-DD") from e


def purchase(conn, item_id, qty, total_cost, batch_no=None, expiry=None,
             user_id=None, note=None, today=None):
    """進貨：庫存 +qty，同時記一筆「成本」。"""
    item = get_item(conn, item_id)
    _require_product(item)
    if qty <= 0:
        raise OpsError("進貨數量需大於 0")
    if total_cost < 0:
        raise OpsError("進貨金額不可為負")
    expiry = str(expiry) if expiry else None
    batch_no = (batch_no or "").strip() or None
    _check_batch(item, batch_no, expiry)
    today = (today or date.today()).isoformat()
    with conn:
        cur = conn.execute(
            "INSERT INTO stock_move(item_id,qty_delta,move_type,batch_no,expiry,"
            "ref_type,note,created_by) VALUES (?,?,?,?,?,?,?,?)",
            (item_id, qty, "purchase", batch_no, expiry, "purchase", note, user_id),
        )
        conn.execute("UPDATE stock_move SET ref_id=id WHERE id=?", (cur.lastrowid,))
        conn.execute(
            "INSERT INTO ledger(date,kind,amount,source,ref_type,ref_id,note,created_by)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (today, "cost", int(total_cost), "purchase", "stock_move", cur.lastrowid,
             f"進貨 {item['name']} x{qty}", user_id),
        )
        conn.execute("UPDATE item SET cost=? WHERE id=?", (round(total_cost / qty), item_id))
    return cur.lastrowid


def adjust_stock(conn, item_id, qty_delta, reason, batch_no=None, expiry=None,
                 user_id=None, scrap=False):
    """盤點調整（可正可負）或報廢（負數）。只影響庫存，不動帳。"""
    item = get_item(conn, item_id)
    _require_product(item)
    if qty_delta == 0:
        raise OpsError("調整數量不可為 0")
    if scrap and qty_delta > 0:
        raise OpsError("報廢數量需為負數")
    if not (reason or "").strip():
        raise OpsError("請填寫原因")
    batch_no = (batch_no or "").strip() or None
    expiry = str(expiry) if expiry else None
    if qty_delta < 0:
        if stock_level(conn, item_id) + qty_delta < 0:
            raise OpsError("庫存不足，無法調降")
    elif item["category"] == "health":
        _check_batch(item, batch_no, expiry)
    with conn:
        conn.execute(
            "INSERT INTO stock_move(item_id,qty_delta,move_type,batch_no,expiry,note,created_by)"
            " VALUES (?,?,?,?,?,?,?)",
            (item_id, qty_delta, "scrap" if scrap else "adjust", batch_no, expiry, reason, user_id),
        )


# ───────────────────────── 銷貨（錢貨一起） ─────────────────────────
def sell(conn, lines, pay_method, user_id=None, today=None):
    """POS 結帳。lines = [(item_id, qty), ...]。

    同一交易內：寫 sale/sale_line、收入分錄、庫存異動（商品依效期最近者先出）。
    餐點只記收入，不動庫存。任何一步失敗，整筆回復。
    """
    if not lines:
        raise OpsError("購物車是空的")
    merged = {}
    for item_id, qty in lines:
        if qty <= 0:
            raise OpsError("數量需大於 0")
        merged[item_id] = merged.get(item_id, 0) + qty
    today = (today or date.today()).isoformat()

    with conn:  # 單一交易
        items = {i: get_item(conn, i) for i in merged}
        for i, it in items.items():
            if not it["active"]:
                raise OpsError(f"「{it['name']}」已停售")
        total = sum(items[i]["price"] * q for i, q in merged.items())
        cur = conn.execute(
            "INSERT INTO sale(total,pay_method,created_by) VALUES (?,?,?)",
            (total, pay_method, user_id),
        )
        sale_id = cur.lastrowid
        for i, q in merged.items():
            it = items[i]
            conn.execute(
                "INSERT INTO sale_line(sale_id,item_id,qty,unit_price,unit_cost) VALUES (?,?,?,?,?)",
                (sale_id, i, q, it["price"], it["cost"]),
            )
            if it["type"] == "product":
                remaining = q
                for b in batches(conn, i):
                    take = min(remaining, b["qty"])
                    conn.execute(
                        "INSERT INTO stock_move(item_id,qty_delta,move_type,batch_no,expiry,"
                        "ref_type,ref_id,created_by) VALUES (?,?,?,?,?,?,?,?)",
                        (i, -take, "sale", b["batch_no"], b["expiry"], "sale", sale_id, user_id),
                    )
                    remaining -= take
                    if remaining == 0:
                        break
                if remaining > 0:
                    raise OpsError(f"「{it['name']}」庫存不足（缺 {remaining}）")
        conn.execute(
            "INSERT INTO ledger(date,kind,amount,source,ref_type,ref_id,note,created_by)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (today, "income", total, "pos", "sale", sale_id, f"銷貨 #{sale_id}", user_id),
        )
    return sale_id


def void_sale(conn, sale_id, user_id=None, today=None):
    """作廢：不刪除，產生反向庫存（回到原批次）與反向收入分錄。"""
    today = (today or date.today()).isoformat()
    with conn:
        sale = conn.execute("SELECT * FROM sale WHERE id=?", (sale_id,)).fetchone()
        if not sale:
            raise OpsError("找不到銷貨單")
        if sale["status"] == "void":
            raise OpsError("此單已作廢")
        # 餐點訂單的付款：作廢前先確認能還原（該桌不能已有新的未結帳訂單）
        linked = conn.execute(
            "SELECT DISTINCT o.id, o.status, o.order_type, o.table_no FROM order_payment_line p "
            "JOIN meal_order o ON o.id=p.order_id WHERE p.sale_id=?", (sale_id,)).fetchall()
        for o in linked:
            if o["status"] == "paid" and o["order_type"] == "dine_in" and conn.execute(
                    "SELECT 1 FROM meal_order WHERE status='open' AND order_type='dine_in' "
                    "AND table_no=? AND id!=?", (o["table_no"], o["id"])).fetchone():
                raise OpsError(f"桌 {o['table_no']} 已有新的未結帳訂單，請先結帳後再作廢這一筆")
        conn.execute("UPDATE sale SET status='void' WHERE id=?", (sale_id,))
        for p in conn.execute("SELECT * FROM order_payment_line WHERE sale_id=?", (sale_id,)).fetchall():
            conn.execute("UPDATE order_line SET paid_qty=MAX(0, paid_qty-?) WHERE id=?",
                         (p["qty"], p["order_line_id"]))
            conn.execute("UPDATE meal_order SET status='open' WHERE id=? AND status='paid'",
                         (p["order_id"],))
        moves = conn.execute(
            "SELECT * FROM stock_move WHERE ref_type='sale' AND ref_id=? AND move_type='sale'",
            (sale_id,),
        ).fetchall()
        for m in moves:
            conn.execute(
                "INSERT INTO stock_move(item_id,qty_delta,move_type,batch_no,expiry,"
                "ref_type,ref_id,note,created_by) VALUES (?,?,?,?,?,?,?,?,?)",
                (m["item_id"], -m["qty_delta"], "void", m["batch_no"], m["expiry"],
                 "sale", sale_id, "作廢回補", user_id),
            )
        conn.execute(
            "INSERT INTO ledger(date,kind,amount,source,ref_type,ref_id,note,created_by)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (today, "income", -sale["total"], "pos", "sale", sale_id,
             f"作廢 銷貨 #{sale_id}", user_id),
        )


def recent_sales(conn, limit=20):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM sale ORDER BY id DESC LIMIT ?", (limit,))]


# ───────────────────────── 帳務 ─────────────────────────
def add_expense(conn, amount, note, user_id=None, on=None, source="manual"):
    if amount <= 0:
        raise OpsError("金額需大於 0")
    if not (note or "").strip():
        raise OpsError("請填寫說明")
    with conn:
        cur = conn.execute(
            "INSERT INTO ledger(date,kind,amount,source,note,created_by) VALUES (?,?,?,?,?,?)",
            ((on or date.today()).isoformat(), "expense", int(amount), source, note, user_id),
        )
    return cur.lastrowid


def report(conn, start, end):
    """期間報表。毛利 = 收入 − 已售商品成本（以售出當時成本計）。"""
    s, e = start.isoformat(), end.isoformat()
    sums = {k: 0 for k in ("income", "cost", "expense")}
    for r in conn.execute(
        "SELECT kind, SUM(amount) a FROM ledger WHERE date BETWEEN ? AND ? GROUP BY kind", (s, e)
    ):
        sums[r["kind"]] = r["a"]
    cogs = conn.execute(
        """SELECT COALESCE(SUM(l.qty*l.unit_cost),0) FROM sale_line l
           JOIN sale s ON s.id=l.sale_id JOIN item i ON i.id=l.item_id
           WHERE s.status='done' AND i.type='product' AND date(s.created_at) BETWEEN ? AND ?""",
        (s, e),
    ).fetchone()[0]
    by_cat = [dict(r) for r in conn.execute(
        """SELECT i.category, SUM(l.qty*l.unit_price) AS revenue, SUM(l.qty) AS qty
           FROM sale_line l JOIN sale s ON s.id=l.sale_id JOIN item i ON i.id=l.item_id
           WHERE s.status='done' AND date(s.created_at) BETWEEN ? AND ?
           GROUP BY i.category""", (s, e))]
    return {
        "income": sums["income"], "purchase_cost": sums["cost"], "expense": sums["expense"],
        "cogs": cogs, "gross_profit": sums["income"] - cogs,
        "net": sums["income"] - sums["cost"] - sums["expense"],
        "by_category": by_cat,
    }


# ───────────────────────── 復原（Line 記帳用） ─────────────────────────
def undo_expense(conn, ledger_id, user_id=None, today=None):
    """以反向分錄沖銷一筆支出。"""
    today = (today or date.today()).isoformat()
    row = conn.execute("SELECT * FROM ledger WHERE id=? AND kind='expense'", (ledger_id,)).fetchone()
    if not row:
        raise OpsError("找不到這筆支出")
    with conn:
        conn.execute(
            "INSERT INTO ledger(date,kind,amount,source,ref_type,ref_id,note,created_by)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (today, "expense", -row["amount"], row["source"], "ledger", ledger_id,
             f"復原：{row['note']}", user_id),
        )


def undo_purchase(conn, move_id, user_id=None, today=None):
    """沖銷一筆進貨：庫存 −qty（原批次）、成本反向。該批庫存已被賣出則不能復原。"""
    today = (today or date.today()).isoformat()
    mv = conn.execute(
        "SELECT * FROM stock_move WHERE id=? AND move_type='purchase'", (move_id,)).fetchone()
    if not mv:
        raise OpsError("找不到這筆進貨")
    left = conn.execute(
        "SELECT COALESCE(SUM(qty_delta),0) FROM stock_move WHERE item_id=? "
        "AND batch_no IS ? AND expiry IS ?", (mv["item_id"], mv["batch_no"], mv["expiry"]),
    ).fetchone()[0]
    if left < mv["qty_delta"]:
        raise OpsError("這批貨已有賣出，無法復原；請改用「盤點／報廢」調整")
    cost = conn.execute(
        "SELECT amount FROM ledger WHERE kind='cost' AND ref_type='stock_move' AND ref_id=?",
        (move_id,)).fetchone()
    with conn:
        conn.execute(
            "INSERT INTO stock_move(item_id,qty_delta,move_type,batch_no,expiry,ref_type,ref_id,note,created_by)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (mv["item_id"], -mv["qty_delta"], "adjust", mv["batch_no"], mv["expiry"],
             "purchase", move_id, "復原進貨", user_id),
        )
        if cost:
            conn.execute(
                "INSERT INTO ledger(date,kind,amount,source,ref_type,ref_id,note,created_by)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (today, "cost", -cost["amount"], "purchase", "stock_move", move_id,
                 "復原進貨", user_id),
            )
