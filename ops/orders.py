"""餐點點餐：下單 → 廚房 → 上菜 → 結帳。

- 內用以桌號辨識；同一桌只有一張未結帳訂單，再次下單視為「加點」（第 2、3… 輪）。
- 外帶自動產生當日取餐號。
- 每個品項可帶特殊需求；整張訂單可帶備註（如過敏）。
- 餐點不扣庫存；結帳時寫入 sale / sale_line / 收入分錄（與 POS 同一份帳）。
- 分開結帳：可只結部分品項（甚至同一品項只結其中幾份）；全部結清後訂單才結束。
- 併桌／換桌：把一桌的訂單併入另一桌（目標桌沒有訂單時就是換桌）。
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
                  l.id AS line_id, l.round_no, l.qty, l.note, l.origin, l.status, l.created_at, i.name
           FROM order_line l JOIN meal_order o ON o.id=l.order_id JOIN item i ON i.id=l.item_id
           WHERE o.status='open' AND l.status IN ('new','preparing')
           ORDER BY l.created_at, l.id""").fetchall()
    tickets = {}
    for r in rows:
        key = (r["order_id"], r["round_no"])
        t = tickets.setdefault(key, {
            "order_id": r["order_id"], "round_no": r["round_no"], "is_addon": r["round_no"] > 1,
            "where": _where(r), "order_note": r["order_note"], "created_at": r["created_at"],
            "lines": [], "origins": [], "_st": []})
        t["lines"].append({"line_id": r["line_id"], "name": r["name"], "qty": r["qty"], "note": r["note"]})
        t["_st"].append(r["status"])
        if r["origin"] and r["origin"] not in t["origins"]:
            t["origins"].append(r["origin"])
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
            "SELECT l.status, l.paid_qty, o.status AS ostatus FROM order_line l "
            "JOIN meal_order o ON o.id=l.order_id WHERE l.id=?", (line_id,)).fetchone()
        if not row or row["ostatus"] != "open":
            raise OpsError("找不到可取消的品項")
        if row["paid_qty"]:
            raise OpsError("這個品項已結帳，無法取消")
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
        if conn.execute("SELECT COUNT(*) FROM order_line WHERE order_id=? AND paid_qty>0",
                        (order_id,)).fetchone()[0]:
            raise OpsError("已有部分品項結帳，無法取消整張訂單")
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
        for l in lines:
            l["remaining"] = 0 if l["status"] == "cancelled" else l["qty"] - l["paid_qty"]
        result.append({
            **dict(o), "where": _where(o), "lines": lines,
            "total": sum(l["qty"] * l["unit_price"] for l in active),
            "paid_total": sum(l["paid_qty"] * l["unit_price"] for l in active),
            "unpaid_total": sum(l["remaining"] * l["unit_price"] for l in active),
            "unfinished": sum(1 for l in active if l["status"] in ("new", "preparing")),
            "to_serve": sum(1 for l in active if l["status"] == "ready"),
        })
    return result


def checkout_lines(conn, order_id, selections, pay_method, user_id=None, today=None):
    """結帳指定品項（分開結帳）。selections = [(line_id, 份數), ...]。

    同一交易內：寫 sale / sale_line / 收入分錄、累加各品項已付份數；
    全部結清後訂單才標記為已結帳。回傳 sale_id。
    """
    today = (today or date.today()).isoformat()
    picks = {}
    for line_id, qty in selections:
        qty = int(qty)
        if qty < 0:
            raise OpsError("份數不可為負")
        if qty:
            picks[line_id] = picks.get(line_id, 0) + qty
    if not picks:
        raise OpsError("請選擇要結帳的品項與份數")
    with conn:
        order = _get_order(conn, order_id)
        if order["status"] != "open":
            raise OpsError("這張訂單已結帳或已取消")
        total, rows = 0, []
        for line_id, qty in picks.items():
            l = conn.execute(
                "SELECT l.*, i.cost FROM order_line l JOIN item i ON i.id=l.item_id "
                "WHERE l.id=? AND l.order_id=?", (line_id, order_id)).fetchone()
            if not l or l["status"] == "cancelled":
                raise OpsError("品項不存在或已取消")
            if qty > l["qty"] - l["paid_qty"]:
                raise OpsError("結帳份數超過未結帳份數")
            total += qty * l["unit_price"]
            rows.append((l, qty))
        cur = conn.execute("INSERT INTO sale(total,pay_method,created_by) VALUES (?,?,?)",
                           (total, pay_method, user_id))
        sale_id = cur.lastrowid
        for l, qty in rows:
            conn.execute(
                "INSERT INTO sale_line(sale_id,item_id,qty,unit_price,unit_cost) VALUES (?,?,?,?,?)",
                (sale_id, l["item_id"], qty, l["unit_price"], l["cost"]))
            conn.execute("INSERT INTO order_payment_line(sale_id,order_id,order_line_id,qty) VALUES (?,?,?,?)",
                         (sale_id, order_id, l["id"], qty))
            conn.execute("UPDATE order_line SET paid_qty=paid_qty+? WHERE id=?", (qty, l["id"]))
        left = conn.execute(
            "SELECT COUNT(*) FROM order_line WHERE order_id=? AND status!='cancelled' AND qty>paid_qty",
            (order_id,)).fetchone()[0]
        earlier = conn.execute(
            "SELECT COUNT(*) FROM order_payment_line WHERE order_id=? AND sale_id!=?",
            (order_id, sale_id)).fetchone()[0]
        split = "（分開結帳）" if (left > 0 or earlier) else ""
        conn.execute(
            "INSERT INTO ledger(date,kind,amount,source,ref_type,ref_id,note,created_by)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (today, "income", total, "pos", "sale", sale_id,
             f"餐點結帳 {_where(order)}（訂單 #{order_id}）{split}", user_id))
        if left == 0:
            conn.execute("UPDATE meal_order SET status='paid', sale_id=? WHERE id=?", (sale_id, order_id))
    return sale_id


def checkout_order(conn, order_id, pay_method, user_id=None, today=None):
    """結清這張訂單所有尚未結帳的品項。"""
    rows = conn.execute(
        "SELECT id, qty-paid_qty AS rest FROM order_line "
        "WHERE order_id=? AND status!='cancelled' AND qty>paid_qty", (order_id,)).fetchall()
    if not rows:
        raise OpsError("沒有可結帳的品項")
    return checkout_lines(conn, order_id, [(r["id"], r["rest"]) for r in rows], pay_method, user_id, today)


def split_evenly(amount, people):
    """均分試算：回傳每人應付金額（餘數由前幾位各多付 1 元）。只做試算，不寫帳。"""
    if people < 1:
        raise OpsError("人數需至少 1 人")
    base, extra = divmod(int(amount), people)
    return [base + (1 if i < extra else 0) for i in range(people)]


def merge_tables(conn, from_table, to_table):
    """換桌／併桌。目標桌沒有未結帳訂單 → 直接換桌；有 → 把來源桌的品項併入目標桌。

    併入的品項在廚房以新的一輪顯示並標示「併自 X」；已付份數、已上菜狀態都保留。
    """
    from_table, to_table = (from_table or "").strip(), (to_table or "").strip()
    if not from_table or not to_table:
        raise OpsError("請輸入來源桌與目標桌")
    if from_table == to_table:
        raise OpsError("來源桌與目標桌相同")
    with conn:
        src = conn.execute("SELECT * FROM meal_order WHERE status='open' AND order_type='dine_in' "
                           "AND table_no=?", (from_table,)).fetchone()
        if not src:
            raise OpsError(f"桌 {from_table} 沒有未結帳的訂單")
        dst = conn.execute("SELECT * FROM meal_order WHERE status='open' AND order_type='dine_in' "
                           "AND table_no=?", (to_table,)).fetchone()
        if dst is None:
            conn.execute("UPDATE meal_order SET table_no=? WHERE id=?", (to_table, src["id"]))
            return {"action": "move", "order_id": src["id"]}
        base = conn.execute("SELECT COALESCE(MAX(round_no),0) FROM order_line WHERE order_id=?",
                            (dst["id"],)).fetchone()[0]
        rounds = [r[0] for r in conn.execute(
            "SELECT DISTINCT round_no FROM order_line WHERE order_id=? ORDER BY round_no", (src["id"],))]
        for n, rn in enumerate(rounds, 1):
            conn.execute(
                "UPDATE order_line SET order_id=?, round_no=?, origin=COALESCE(origin, ?) "
                "WHERE order_id=? AND round_no=?",
                (dst["id"], base + n, f"併自 {from_table}", src["id"], rn))
        conn.execute("UPDATE order_payment_line SET order_id=? WHERE order_id=?", (dst["id"], src["id"]))
        notes = [n for n in (dst["note"], src["note"]) if n]
        conn.execute("UPDATE meal_order SET note=? WHERE id=?", ("；".join(notes) or None, dst["id"]))
        conn.execute("UPDATE meal_order SET status='cancelled', merged_into=? WHERE id=?",
                     (dst["id"], src["id"]))
        return {"action": "merge", "order_id": dst["id"]}


def wait_minutes(created_at, now=None):
    now = now or datetime.now()
    return max(0, int((now - datetime.fromisoformat(created_at)).total_seconds() // 60))
