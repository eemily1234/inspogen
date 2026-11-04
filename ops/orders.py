"""餐點點餐：下單 → 廚房 → 上菜 → 結帳。

- 內用以桌號辨識；同一桌只有一張未結帳訂單，再次下單視為「加點」（第 2、3… 輪）。
- 外帶自動產生當日取餐號。
- 每個品項可帶特殊需求；整張訂單可帶備註（如過敏）。
- 餐點不扣庫存；結帳時寫入 sale / sale_line / 收入分錄（與 POS 同一份帳）。
- 分開結帳：可只結部分品項（甚至同一品項只結其中幾份）；全部結清後訂單才結束。
- 併桌／換桌：把一桌的訂單併入另一桌（目標桌沒有訂單時就是換桌）。
- 桌位分樓層（dining_table），每桌預設 4 人；外場總覽把每桌歸到流程階段。
- 廚房可切換「接單中／暫停接單」；暫停時不能送出新單。
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


def place_order(conn, order_type, lines, table_no=None, note=None, user_id=None, today=None,
                guests=None):
    """下單。lines = [{"item_id":..,"qty":..,"note":..}, ...]

    回傳 {order_id, round_no, table_no, pickup_no, is_addon}。
    """
    if order_type not in ("dine_in", "takeout"):
        raise OpsError("請選擇內用或外帶")
    ks = kitchen_status(conn)
    if not ks["accepting"]:
        raise OpsError("廚房目前暫停接單" + (f"：{ks['reason']}" if ks["reason"] else ""))
    table_no = (table_no or "").strip() or None
    if order_type == "dine_in" and not table_no:
        raise OpsError("內用請輸入桌號")
    if not lines:
        raise OpsError("尚未選擇餐點")
    note = (note or "").strip() or None
    today = (today or date.today()).isoformat()
    if order_type == "dine_in" and conn.execute("SELECT 1 FROM dining_table LIMIT 1").fetchone():
        if not conn.execute("SELECT 1 FROM dining_table WHERE name=?", (table_no,)).fetchone():
            raise OpsError(f"找不到桌號「{table_no}」")
    if guests is not None:
        guests = int(guests)
        if not 1 <= guests <= 30:
            raise OpsError("人數需在 1～30 之間")

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
                "INSERT INTO meal_order(order_type,table_no,pickup_no,note,created_by,guests) VALUES (?,?,?,?,?,?)",
                (order_type, table_no, pickup_no, note, user_id, guests))
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
            if guests and not order["guests"]:
                conn.execute("UPDATE meal_order SET guests=? WHERE id=?", (guests, order_id))
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
        if dst["guests"] or src["guests"]:
            conn.execute("UPDATE meal_order SET guests=? WHERE id=?",
                         ((dst["guests"] or 0) + (src["guests"] or 0), dst["id"]))
        conn.execute("UPDATE meal_order SET status='cancelled', merged_into=? WHERE id=?",
                     (dst["id"], src["id"]))
        return {"action": "merge", "order_id": dst["id"]}


def wait_minutes(created_at, now=None):
    now = now or datetime.now()
    return max(0, int((now - datetime.fromisoformat(created_at)).total_seconds() // 60))


# ───────────────────────── 廚房是否接單 ─────────────────────────
def _setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def _set_setting(conn, key, value):
    conn.execute("INSERT INTO setting(key,value) VALUES (?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def kitchen_status(conn):
    """回傳 {"accepting": bool, "reason": str}。預設接單。"""
    return {"accepting": _setting(conn, "kitchen_accepting", "1") == "1",
            "reason": _setting(conn, "kitchen_reason", "") or ""}


def set_kitchen_accepting(conn, accepting, reason=None):
    """廚房切換接單／暫停接單。暫停期間前台不能送新單（已送出的單不受影響）。"""
    with conn:
        _set_setting(conn, "kitchen_accepting", "1" if accepting else "0")
        _set_setting(conn, "kitchen_reason", "" if accepting else (reason or "").strip())


# ───────────────────────── 桌位與外場總覽 ─────────────────────────
STAGES = [("new", "待接單"), ("preparing", "製作中"), ("ready", "待上菜"), ("dining", "用餐中")]
STAGE_LABEL = dict(STAGES, empty="空桌")


def setup_tables(conn, floors, seats=4):
    """設定桌位。floors = {1: 6, 2: 6} 表示 1 樓 6 桌、2 樓 6 桌；桌名 101～106、201～206。
    只會新增缺少的桌，或刪除超出的空桌；有客人使用的桌不會被刪。"""
    if seats < 1:
        raise OpsError("每桌座位數需至少 1")
    for fl, n in floors.items():
        if not 1 <= int(fl) <= 9 or not 0 <= int(n) <= 99:
            raise OpsError("樓層需為 1～9，每層桌數 0～99")
    want = {f"{int(fl)}{i:02d}": (int(fl), i) for fl, n in floors.items() for i in range(1, int(n) + 1)}
    with conn:
        have = {r["name"] for r in conn.execute("SELECT name FROM dining_table")}
        for name, (fl, _) in want.items():
            if name not in have:
                conn.execute("INSERT INTO dining_table(floor,name,seats) VALUES (?,?,?)", (fl, name, seats))
        in_use = {r["table_no"] for r in conn.execute(
            "SELECT table_no FROM meal_order WHERE status='open' AND order_type='dine_in'")}
        for name in have - set(want):
            if name in in_use:
                raise OpsError(f"桌 {name} 還有客人，無法刪除")
            conn.execute("DELETE FROM dining_table WHERE name=?", (name,))
        conn.execute("UPDATE dining_table SET seats=?", (seats,))
    return len(want)


def list_tables(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM dining_table ORDER BY floor, name")]


def _stage_of(od):
    """一桌的流程階段：有可上菜 > 有待接單 > 有製作中 > 全部已上菜（用餐中）。"""
    sts = {l["status"] for l in od["lines"] if l["status"] != "cancelled"}
    for st in ("ready", "new", "preparing"):
        if st in sts:
            return st
    return "dining"


def _tile(od, now=None):
    active = [l for l in od["lines"] if l["status"] != "cancelled"]
    return {
        "order_id": od["id"], "stage": _stage_of(od), "guests": od.get("guests"),
        "wait": wait_minutes(od["created_at"], now), "total": od["total"],
        "unpaid_total": od["unpaid_total"], "to_serve": od["to_serve"],
        "counts": {k: sum(1 for l in active if l["status"] == k) for k, _ in STAGES[:3]},
        "note": od["note"],
    }


def floor_overview(conn, now=None):
    """外場即時總覽：各桌狀態、外帶單、流程各階段桌數、廚房是否接單。"""
    ods = open_orders(conn)
    dine = {o["table_no"]: o for o in ods if o["order_type"] == "dine_in"}
    tables = []
    for t in list_tables(conn):
        od = dine.pop(t["name"], None)
        tables.append({**t, "stage": "empty", "order_id": None, "guests": None,
                       "to_serve": 0, "wait": 0, "total": 0, "unpaid_total": 0,
                       "counts": {}, "note": None, **(_tile(od, now) if od else {})})
    # 沒設定在桌位表的內用單（舊資料或未設定桌位）也要看得到
    for name, od in dine.items():
        tables.append({"id": None, "floor": 0, "name": name, "seats": 4, **_tile(od, now)})
    takeouts = [{"where": o["where"], **_tile(o, now)} for o in ods if o["order_type"] == "takeout"]
    flow = {k: 0 for k, _ in STAGES}
    flow["empty"] = sum(1 for t in tables if t["stage"] == "empty")
    for t in tables:
        if t["stage"] in flow and t["stage"] != "empty":
            flow[t["stage"]] += 1
    return {"tables": tables, "takeouts": takeouts, "flow": flow, "kitchen": kitchen_status(conn),
            "floors": sorted({t["floor"] for t in tables})}


def serve_all_ready(conn, order_id):
    """服務生一鍵上菜：這張訂單所有「待上菜」品項標為已上菜。回傳份數。"""
    with conn:
        cur = conn.execute("UPDATE order_line SET status='served' WHERE order_id=? AND status='ready'",
                           (order_id,))
        if cur.rowcount == 0:
            raise OpsError("目前沒有可上的菜")
        return cur.rowcount


DEFAULT_FLOORS = {1: 4, 2: 4}   # 每樓 4 桌
DEFAULT_SEATS = 4               # 每桌 4 人


def ensure_default_tables(conn):
    """第一次使用時自動建立桌位：1 樓 101～104、2 樓 201～204，每桌 4 人。
    只會做一次；之後老闆在設定頁改成別的（甚至全部刪除）都不會被覆蓋。"""
    if _setting(conn, "tables_initialized") == "1":
        return False
    with conn:
        _set_setting(conn, "tables_initialized", "1")
    if conn.execute("SELECT 1 FROM dining_table LIMIT 1").fetchone():
        return False
    setup_tables(conn, DEFAULT_FLOORS, seats=DEFAULT_SEATS)
    return True
