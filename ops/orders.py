"""餐點點餐：下單 → 廚房 → 上菜 → 結帳。

- 內用以桌號辨識；同一桌只有一張未結帳訂單，再次下單視為「加點」（第 2、3… 輪）。
- 外帶自動產生當日取餐號。
- 每個品項可帶特殊需求；整張訂單可帶備註（如過敏）。
- 餐點不扣庫存；結帳時寫入 sale / sale_line / 收入分錄（與 POS 同一份帳）。
"""
from datetime import date, datetime

from . import services as s
from .services import OpsError

FLOW = ["new", "preparing", "ready", "served"]
NEXT = {"new": "preparing", "preparing": "ready", "ready": "served"}
LABEL = {"new": "新單", "preparing": "製作中", "ready": "待上菜", "served": "已上菜",
         "cancelled": "已取消"}


def _get_order(conn, order_id):
    row = conn.execute("SELECT * FROM meal_order WHERE id=?", (order_id,)).fetchone()
    if not row:
        raise OpsError("找不到訂單")
    return row


def open_order_for_table(conn, table_no):
    table_no = (table_no or "").strip()
    if not table_no:
        return None
    row = conn.execute(
        "SELECT * FROM meal_order WHERE status='open' AND order_type='dine_in' AND table_no=?",
        (table_no,)).fetchone()
    return dict(row) if row else None


def place_order(conn, order_type, lines, table_no=None, note=None, user_id=None, today=None):
    """下單。lines = [{"item_id":..,"qty":..,"note":..}, ...]

    回傳 {order_id, round_no, table_no, pickup_no, is_addon}。
    """
    if order_type not in ("dine_in", "takeout"):
        raise OpsError("請選擇內用或外帶")
    table_no = (table_no or "").strip() or None
    if order_type == "dine_in" and not table_no:
        raise OpsError("內用請輸入桌號")
    if not lines:
        raise OpsError("尚未選擇餐點")
    note = (note or "").strip() or None
    today = (today or date.today()).isoformat()

    clean = []
    for ln in lines:
        qty = int(ln["qty"])
        if qty <= 0:
            raise OpsError("數量需大於 0")
        it = s.get_item(conn, ln["item_id"])
        if it["type"] != "meal":
            raise OpsError(f"「{it['name']}」不是餐點，請改用 POS")
        if not it["active"]:
            raise OpsError(f"「{it['name']}」已停售")
        clean.append((it, qty, (ln.get("note") or "").strip() or None))

    with conn:
        order = None
        if order_type == "dine_in":
            order = conn.execute(
                "SELECT * FROM meal_order WHERE status='open' AND order_type='dine_in' AND table_no=?",
                (table_no,)).fetchone()
        pickup_no = None
        if order is None:
            if order_type == "takeout":
                pickup_no = conn.execute(
                    "SELECT COALESCE(MAX(pickup_no),0)+1 FROM meal_order "
                    "WHERE order_type='takeout' AND date(created_at)=?", (today,)).fetchone()[0]
            cur = conn.execute(
                "INSERT INTO meal_order(order_type,table_no,pickup_no,note,created_by) VALUES (?,?,?,?,?)",
                (order_type, table_no, pickup_no, note, user_id))
            order_id, round_no = cur.lastrowid, 1
        else:
            order_id = order["id"]
            pickup_no = order["pickup_no"]
            round_no = conn.execute(
                "SELECT COALESCE(MAX(round_no),0)+1 FROM order_line WHERE order_id=?",
                (order_id,)).fetchone()[0]
            if note:
                merged = f"{order['note']}；{note}" if order["note"] else note
                conn.execute("UPDATE meal_order SET note=? WHERE id=?", (merged, order_id))
        for it, qty, lnote in clean:
            conn.execute(
                "INSERT INTO order_line(order_id,round_no,item_id,qty,unit_price,note) VALUES (?,?,?,?,?,?)",
                (order_id, round_no, it["id"], qty, it["price"], lnote))
    return {"order_id": order_id, "round_no": round_no, "table_no": table_no,
            "pickup_no": pickup_no, "is_addon": round_no > 1}


def _where(order):
    return f"桌 {order['table_no']}" if order["order_type"] == "dine_in" else f"外帶 #{order['pickup_no']}"


def _ticket_status(statuses):
    return min(statuses, key=FLOW.index)


def kitchen_tickets(conn):
    """廚房看板：尚未出餐（新單、製作中）的單，依下單時間由舊到新。
    一張單 = 同一訂單的同一輪下單。"""
    rows = conn.execute(
        """SELECT o.id AS order_id, o.order_type, o.table_no, o.pickup_no, o.note AS order_note,
                  l.id AS line_id, l.round_no, l.qty, l.note, l.status, l.created_at, i.name
           FROM order_line l JOIN meal_order o ON o.id=l.order_id JOIN item i ON i.id=l.item_id
           WHERE o.status='open' AND l.status IN ('new','preparing')
           ORDER BY l.created_at, l.id""").fetchall()
    tickets = {}
    for r in rows:
        key = (r["order_id"], r["round_no"])
        t = tickets.setdefault(key, {
            "order_id": r["order_id"], "round_no": r["round_no"], "is_addon": r["round_no"] > 1,
            "where": _where(r), "order_note": r["order_note"], "created_at": r["created_at"],
            "lines": [], "_st": []})
        t["lines"].append({"line_id": r["line_id"], "name": r["name"], "qty": r["qty"], "note": r["note"]})
        t["_st"].append(r["status"])
    out = []
    for t in tickets.values():
        t["status"] = _ticket_status(t.pop("_st"))
        out.append(t)
    return out


def advance_ticket(conn, order_id, round_no):
    """廚房：新單 → 製作中 → 完成出餐（待上菜）。"""
    with conn:
        sts = [r["status"] for r in conn.execute(
            "SELECT status FROM order_line WHERE order_id=? AND round_no=? AND status IN ('new','preparing')",
            (order_id, round_no))]
        if not sts:
            raise OpsError("這張單已處理完成")
        cur = _ticket_status(sts)
        conn.execute(
            "UPDATE order_line SET status=? WHERE order_id=? AND round_no=? AND status=?",
            (NEXT[cur], order_id, round_no, cur))
    return NEXT[cur]


def serve_line(conn, line_id):
    """服務生：待上菜 → 已上菜。"""
    with conn:
        row = conn.execute("SELECT status FROM order_line WHERE id=?", (line_id,)).fetchone()
        if not row or row["status"] != "ready":
            raise OpsError("這個品項還不能上菜")
        conn.execute("UPDATE order_line SET status='served' WHERE id=?", (line_id,))


def cancel_line(conn, line_id):
    """取消品項：只有廚房尚未開始製作（新單）才能取消。"""
    with conn:
        row = conn.execute(
            "SELECT l.status, o.status AS ostatus FROM order_line l JOIN meal_order o ON o.id=l.order_id "
            "WHERE l.id=?", (line_id,)).fetchone()
        if not row or row["ostatus"] != "open":
            raise OpsError("找不到可取消的品項")
        if row["status"] != "new":
            raise OpsError("廚房已開始製作，無法取消；請洽廚房或老闆")
        conn.execute("UPDATE order_line SET status='cancelled' WHERE id=?", (line_id,))


def cancel_order(conn, order_id):
    """取消整張訂單：所有品項都還是新單才能取消。"""
    with conn:
        order = _get_order(conn, order_id)
        if order["status"] != "open":
            raise OpsError("訂單已結束")
        started = conn.execute(
            "SELECT COUNT(*) FROM order_line WHERE order_id=? AND status NOT IN ('new','cancelled')",
            (order_id,)).fetchone()[0]
        if started:
            raise OpsError("廚房已開始製作，無法取消整張訂單")
        conn.execute("UPDATE order_line SET status='cancelled' WHERE order_id=?", (order_id,))
        conn.execute("UPDATE meal_order SET status='cancelled' WHERE id=?", (order_id,))


def open_orders(conn):
    """未結帳訂單（服務生／櫃台用），含品項與合計。"""
    result = []
    for o in conn.execute("SELECT * FROM meal_order WHERE status='open' ORDER BY id").fetchall():
        lines = [dict(r) for r in conn.execute(
            "SELECT l.*, i.name FROM order_line l JOIN item i ON i.id=l.item_id "
            "WHERE l.order_id=? ORDER BY l.round_no, l.id", (o["id"],))]
        active = [l for l in lines if l["status"] != "cancelled"]
        result.append({
            **dict(o), "where": _where(o), "lines": lines,
            "total": sum(l["qty"] * l["unit_price"] for l in active),
            "unfinished": sum(1 for l in active if l["status"] in ("new", "preparing")),
            "to_serve": sum(1 for l in active if l["status"] == "ready"),
        })
    return result


def checkout_order(conn, order_id, pay_method, user_id=None, today=None):
    """結帳：同一交易內寫 sale / sale_line / 收入分錄，並結束訂單。回傳 sale_id。"""
    today = (today or date.today()).isoformat()
    with conn:
        order = _get_order(conn, order_id)
        if order["status"] != "open":
            raise OpsError("這張訂單已結帳或已取消")
        lines = conn.execute(
            "SELECT l.*, i.cost FROM order_line l JOIN item i ON i.id=l.item_id "
            "WHERE l.order_id=? AND l.status!='cancelled'", (order_id,)).fetchall()
        if not lines:
            raise OpsError("沒有可結帳的品項")
        total = sum(l["qty"] * l["unit_price"] for l in lines)
        cur = conn.execute("INSERT INTO sale(total,pay_method,created_by) VALUES (?,?,?)",
                           (total, pay_method, user_id))
        sale_id = cur.lastrowid
        for l in lines:
            conn.execute(
                "INSERT INTO sale_line(sale_id,item_id,qty,unit_price,unit_cost) VALUES (?,?,?,?,?)",
                (sale_id, l["item_id"], l["qty"], l["unit_price"], l["cost"]))
        conn.execute(
            "INSERT INTO ledger(date,kind,amount,source,ref_type,ref_id,note,created_by)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (today, "income", total, "pos", "sale", sale_id,
             f"餐點結帳 {_where(order)}（訂單 #{order_id}）", user_id))
        conn.execute("UPDATE meal_order SET status='paid', sale_id=? WHERE id=?", (sale_id, order_id))
    return sale_id


def wait_minutes(created_at, now=None):
    now = now or datetime.now()
    return max(0, int((now - datetime.fromisoformat(created_at)).total_seconds() // 60))
