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


def next_queue_no(conn, today=None):
    """當日排隊單號：內用、外帶、Uber 一起從 1 開始編。"""
    today = (today or date.today()).isoformat()
    return conn.execute("SELECT COALESCE(MAX(queue_no),0)+1 FROM meal_order WHERE date(created_at)=?",
                        (today,)).fetchone()[0]


def queue_label(no):
    return f"#{no:03d}" if no else "#—"


def _insert_line(conn, order_id, round_no, item, qty, note, seat_no=None, gift=False, option_ids=None):
    """寫入一個品項，並扣掉「剩餘份數」。套餐會展開：套餐本身一行（計價），底下每個品項各一行
    （價格 0，送到各自的廚房／吧台）。gift=True：招待（價格 0）。option_ids：選的加價選項。"""
    delta, opt_text = resolve_options(conn, item, option_ids or [])
    price = 0 if gift else item["price"] + delta
    comps = combo_components(conn, item["id"])
    _consume(conn, item, qty)
    ids_txt = ",".join(str(i) for i in (option_ids or [])) or None
    if comps:
        cur = conn.execute(
            "INSERT INTO order_line(order_id,round_no,item_id,qty,unit_price,note,seat_no,station,is_combo,gift,"
            "options,option_ids) VALUES (?,?,?,?,?,?,?,?,1,?,?,?)",
            (order_id, round_no, item["id"], qty, price, note, seat_no, "combo", 1 if gift else 0, opt_text, ids_txt))
        pid = cur.lastrowid
        for comp in comps:
            _consume(conn, s.get_item(conn, comp["id"]), qty * comp["qty"])
            conn.execute(
                "INSERT INTO order_line(order_id,round_no,item_id,qty,unit_price,note,seat_no,station,parent_id,options)"
                " VALUES (?,?,?,?,0,?,?,?,?,?)",
                (order_id, round_no, comp["id"], qty * comp["qty"], note, seat_no, comp["station"], pid, opt_text))
    else:
        conn.execute(
            "INSERT INTO order_line(order_id,round_no,item_id,qty,unit_price,note,seat_no,station,gift,options,option_ids)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (order_id, round_no, item["id"], qty, price, note, seat_no, item["station"], 1 if gift else 0,
             opt_text, ids_txt))


def place_order(conn, order_type, lines, table_no=None, note=None, user_id=None, today=None,
                guests=None, seats=None):
    """下單。lines = [{"item_id":..,"qty":..,"note":..,"seat":座位號或None}, ...]
    seats = {座位號: {"guest_name", "customer_id", "avoid"}}（誰坐哪裡、不吃什麼；可省略）

    回傳 {order_id, round_no, table_no, pickup_no, queue_no, is_addon}。
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
        seat = ln.get("seat")
        if seat is not None:
            seat = int(seat)
            if not 1 <= seat <= 30:
                raise OpsError("座位號碼錯誤")
        clean.append((it, qty, (ln.get("note") or "").strip() or None, seat, bool(ln.get("gift")),
                      [int(x) for x in (ln.get("options") or [])]))

    with conn:
        order = None
        if order_type == "dine_in":
            order = conn.execute(
                "SELECT * FROM meal_order WHERE status='open' AND order_type='dine_in' AND table_no=?",
                (table_no,)).fetchone()
        pickup_no = None
        if order is None:
            queue_no = next_queue_no(conn, date.fromisoformat(today))
            if order_type == "takeout":
                pickup_no = conn.execute(
                    "SELECT COALESCE(MAX(pickup_no),0)+1 FROM meal_order "
                    "WHERE order_type='takeout' AND date(created_at)=?", (today,)).fetchone()[0]
            cur = conn.execute(
                "INSERT INTO meal_order(order_type,table_no,pickup_no,note,created_by,guests,queue_no)"
                " VALUES (?,?,?,?,?,?,?)",
                (order_type, table_no, pickup_no, note, user_id, guests, queue_no))
            order_id, round_no = cur.lastrowid, 1
        else:
            order_id = order["id"]
            pickup_no = order["pickup_no"]
            queue_no = order["queue_no"]
            round_no = conn.execute(
                "SELECT COALESCE(MAX(round_no),0)+1 FROM order_line WHERE order_id=?",
                (order_id,)).fetchone()[0]
            if note:
                merged = f"{order['note']}；{note}" if order["note"] else note
                conn.execute("UPDATE meal_order SET note=? WHERE id=?", (merged, order_id))
            if guests and (not order["guests"] or guests > order["guests"]):   # 加點用到新座位 → 人數跟著增加
                conn.execute("UPDATE meal_order SET guests=? WHERE id=?", (guests, order_id))
        for it, qty, lnote, seat, gift, opts in clean:
            _insert_line(conn, order_id, round_no, it, qty, lnote, seat, gift, opts)
        for seat_no, info in (seats or {}).items():
            _save_seat(conn, order_id, int(seat_no), info.get("guest_name"), info.get("customer_id"), info.get("avoid"))
    return {"order_id": order_id, "round_no": round_no, "table_no": table_no,
            "pickup_no": pickup_no, "queue_no": queue_no, "is_addon": round_no > 1}


def _where(order):
    if order["order_type"] == "dine_in":
        return f"桌 {order['table_no']}"
    if order["source"] == "uber":
        return f"Uber {order['external_label'] or order['external_id'] or ''}".strip()
    return f"外帶 #{order['pickup_no']}"


def _ticket_status(statuses):
    return min(statuses, key=FLOW.index)


def kitchen_tickets(conn, station="kitchen"):
    """廚房（或吧台）看板：尚未出餐（新單、製作中）的單，依下單時間由舊到新。
    一張單 = 同一訂單的同一輪下單、同一個製作區。每個品項帶著座位、客人、忌口與所屬套餐。"""
    rows = conn.execute(
        """SELECT o.id AS order_id, o.order_type, o.table_no, o.pickup_no, o.note AS order_note,
                  o.source, o.external_label, o.external_id, o.queue_no,
                  l.id AS line_id, l.round_no, l.qty, l.note, l.origin, l.status, l.created_at, i.name,
                  l.seat_no, se.guest_name, se.avoid, se.customer_id, pi.name AS combo_name, l.options
           FROM order_line l JOIN meal_order o ON o.id=l.order_id JOIN item i ON i.id=l.item_id
           LEFT JOIN order_seat se ON se.order_id=l.order_id AND se.seat_no=l.seat_no
           LEFT JOIN order_line p ON p.id=l.parent_id LEFT JOIN item pi ON pi.id=p.item_id
           WHERE o.status='open' AND l.status IN ('new','preparing') AND l.is_combo=0 AND l.station=?
           ORDER BY l.created_at, l.id""", (station,)).fetchall()
    tickets = {}
    for r in rows:
        key = (r["order_id"], r["round_no"])
        t = tickets.setdefault(key, {
            "order_id": r["order_id"], "round_no": r["round_no"], "is_addon": r["round_no"] > 1,
            "where": _where(r), "order_note": r["order_note"], "created_at": r["created_at"],
            "queue_no": r["queue_no"], "station": station, "lines": [], "origins": [], "_st": []})
        t["lines"].append({"line_id": r["line_id"], "name": r["name"], "qty": r["qty"], "note": r["note"],
                           "seat_no": r["seat_no"], "guest": r["guest_name"], "avoid": r["avoid"],
                           "regular": bool(r["customer_id"]), "combo": r["combo_name"], "options": r["options"]})
        t["_st"].append(r["status"])
        if r["origin"] and r["origin"] not in t["origins"]:
            t["origins"].append(r["origin"])
    out = []
    for t in tickets.values():
        t["status"] = _ticket_status(t.pop("_st"))
        out.append(t)
    return out


def _sync_combos(conn, order_id):
    """套餐的狀態跟著它底下的品項走：最慢的那一個是多少，套餐就是多少；全部取消則套餐取消。"""
    for p in conn.execute("SELECT id FROM order_line WHERE order_id=? AND is_combo=1 AND status!='cancelled'",
                          (order_id,)).fetchall():
        sts = [r["status"] for r in conn.execute(
            "SELECT status FROM order_line WHERE parent_id=? AND status!='cancelled'", (p["id"],))]
        conn.execute("UPDATE order_line SET status=? WHERE id=?",
                     (_ticket_status(sts) if sts else "cancelled", p["id"]))


def advance_ticket(conn, order_id, round_no, station="kitchen"):
    """廚房／吧台：新單 → 製作中 → 完成出餐（待上菜）。只動自己這一區的品項。"""
    with conn:
        sts = [r["status"] for r in conn.execute(
            "SELECT status FROM order_line WHERE order_id=? AND round_no=? AND station=? AND is_combo=0 "
            "AND status IN ('new','preparing')", (order_id, round_no, station))]
        if not sts:
            raise OpsError("這張單已處理完成")
        cur = _ticket_status(sts)
        conn.execute(
            "UPDATE order_line SET status=? WHERE order_id=? AND round_no=? AND station=? AND is_combo=0 AND status=?",
            (NEXT[cur], order_id, round_no, station, cur))
        _sync_combos(conn, order_id)
    return NEXT[cur]


def serve_line(conn, line_id):
    """服務生：待上菜 → 已上菜。點套餐＝把套餐裡已完成的品項都上菜。"""
    with conn:
        row = conn.execute("SELECT * FROM order_line WHERE id=?", (line_id,)).fetchone()
        if not row:
            raise OpsError("找不到這個品項")
        if row["is_combo"]:
            cur = conn.execute("UPDATE order_line SET status='served' WHERE parent_id=? AND status='ready'", (line_id,))
            if cur.rowcount == 0:
                raise OpsError("套餐裡還沒有可以上的品項")
        else:
            if row["status"] != "ready":
                raise OpsError("這個品項還不能上菜")
            conn.execute("UPDATE order_line SET status='served' WHERE id=?", (line_id,))
        _sync_combos(conn, row["order_id"])


def cancel_line(conn, line_id):
    """取消品項：只有廚房尚未開始製作（新單）才能取消。套餐要整份取消。"""
    with conn:
        row = conn.execute(
            "SELECT l.*, o.status AS ostatus FROM order_line l "
            "JOIN meal_order o ON o.id=l.order_id WHERE l.id=?", (line_id,)).fetchone()
        if not row or row["ostatus"] != "open":
            raise OpsError("找不到可取消的品項")
        if row["parent_id"]:
            raise OpsError("套餐裡的品項請取消整份套餐")
        if row["paid_qty"]:
            raise OpsError("這個品項已結帳，無法取消")
        if row["is_combo"]:
            started = conn.execute("SELECT COUNT(*) FROM order_line WHERE parent_id=? AND status NOT IN ('new','cancelled')",
                                   (line_id,)).fetchone()[0]
            if started:
                raise OpsError("套餐裡已有品項開始製作，無法取消；請洽廚房或老闆")
            for ch in conn.execute("SELECT item_id, qty FROM order_line WHERE parent_id=? AND status!='cancelled'",
                                   (line_id,)).fetchall():
                _restore(conn, ch["item_id"], ch["qty"])
            conn.execute("UPDATE order_line SET status='cancelled' WHERE parent_id=?", (line_id,))
        elif row["status"] != "new":
            raise OpsError("廚房已開始製作，無法取消；請洽廚房或老闆")
        _restore(conn, row["item_id"], row["qty"])
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
        for ln in conn.execute("SELECT item_id, qty FROM order_line WHERE order_id=? AND status='new'",
                               (order_id,)).fetchall():
            _restore(conn, ln["item_id"], ln["qty"])
        conn.execute("UPDATE order_line SET status='cancelled' WHERE order_id=?", (order_id,))
        conn.execute("UPDATE meal_order SET status='cancelled' WHERE id=?", (order_id,))


def open_orders(conn):
    """進行中的訂單（客人還沒離開）：含計價品項（套餐帶著它的組成品項）、座位、合計與狀態。
    結帳完成不會讓訂單消失；要按「客人離開」(close_order) 才結束。"""
    result = []
    for o in conn.execute("SELECT * FROM meal_order WHERE status='open' ORDER BY id").fetchall():
        rows = [dict(r) for r in conn.execute(
            "SELECT l.*, i.name FROM order_line l JOIN item i ON i.id=l.item_id "
            "WHERE l.order_id=? ORDER BY l.round_no, l.id", (o["id"],))]
        children = {}
        for r in rows:
            if r["parent_id"]:
                children.setdefault(r["parent_id"], []).append(r)
        lines = [r for r in rows if not r["parent_id"]]          # 計價品項（一般品項＋套餐）
        for l in lines:
            l["remaining"] = 0 if l["status"] == "cancelled" else l["qty"] - l["paid_qty"]
            l["components"] = children.get(l["id"], [])
        active = [l for l in lines if l["status"] != "cancelled"]
        prep = [r for r in rows if not r["is_combo"] and r["status"] != "cancelled"]   # 實際要製作的品項
        unpaid = sum(l["remaining"] * l["unit_price"] for l in active)
        unfinished = sum(1 for r in prep if r["status"] in ("new", "preparing"))
        to_serve = sum(1 for r in prep if r["status"] == "ready")
        result.append({
            **dict(o), "where": _where(o), "queue": queue_label(o["queue_no"]), "lines": lines,
            "seats": order_seats(conn, o["id"]),
            "total": sum(l["qty"] * l["unit_price"] for l in active),
            "paid_total": sum(l["paid_qty"] * l["unit_price"] for l in active),
            "unpaid_total": unpaid,
            "unfinished": unfinished, "to_serve": to_serve,
            "paid_all": bool(active) and unpaid == 0,
            "can_close": unpaid == 0 and unfinished == 0 and to_serve == 0,
        })
    return result


def close_order(conn, order_id):
    """客人離開、訂單完成。要全部結清、所有餐點都上完才能完成；桌子才會變回空桌。"""
    with conn:
        order = _get_order(conn, order_id)
        if order["status"] != "open":
            raise OpsError("這張訂單已經完成或取消")
        od = next(x for x in open_orders(conn) if x["id"] == order_id)
        if od["unpaid_total"] > 0:
            raise OpsError(f"還有 ＄{od['unpaid_total']} 未結帳")
        if od["unfinished"] or od["to_serve"]:
            raise OpsError(f"還有 {od['unfinished'] + od['to_serve']} 道餐點還沒上完")
        status = "paid" if od["total"] > 0 else "cancelled"
        conn.execute("UPDATE meal_order SET status=?, closed_at=datetime('now','localtime') WHERE id=?",
                     (status, order_id))


def checkout_lines(conn, order_id, selections, pay_method, user_id=None, today=None, discount=0, note=None):
    """結帳指定品項（分開結帳）。selections = [(line_id, 份數), ...]。
    discount：這次結帳折抵的金額（0～小計）；note：優惠備註。實收＝小計－折扣。

    同一交易內：寫 sale / sale_line / 收入分錄、累加各品項已付份數。
    結清不會結束訂單；客人離開時再按完成 (close_order)。回傳 sale_id。
    """
    discount = int(discount or 0)
    note = (note or "").strip() or None
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
                "WHERE l.id=? AND l.order_id=? AND l.parent_id IS NULL", (line_id, order_id)).fetchone()
            if not l or l["status"] == "cancelled":
                raise OpsError("品項不存在或已取消")
            if qty > l["qty"] - l["paid_qty"]:
                raise OpsError("結帳份數超過未結帳份數")
            total += qty * l["unit_price"]
            rows.append((l, qty))
        if discount < 0 or discount > total:
            raise OpsError(f"折扣需在 ＄0～＄{total} 之間")
        cur = conn.execute("INSERT INTO sale(total,pay_method,created_by,discount,note) VALUES (?,?,?,?,?)",
                           (total - discount, pay_method, user_id, discount, note))
        sale_id = cur.lastrowid
        for l, qty in rows:
            conn.execute(
                "INSERT INTO sale_line(sale_id,item_id,qty,unit_price,unit_cost) VALUES (?,?,?,?,?)",
                (sale_id, l["item_id"], qty, l["unit_price"], l["cost"]))
            conn.execute("INSERT INTO order_payment_line(sale_id,order_id,order_line_id,qty) VALUES (?,?,?,?)",
                         (sale_id, order_id, l["id"], qty))
            conn.execute("UPDATE order_line SET paid_qty=paid_qty+? WHERE id=?", (qty, l["id"]))
        left = conn.execute(
            "SELECT COUNT(*) FROM order_line WHERE order_id=? AND parent_id IS NULL AND status!='cancelled' "
            "AND qty>paid_qty", (order_id,)).fetchone()[0]
        earlier = conn.execute(
            "SELECT COUNT(*) FROM order_payment_line WHERE order_id=? AND sale_id!=?",
            (order_id, sale_id)).fetchone()[0]
        split = "（分開結帳）" if (left > 0 or earlier) else ""
        promo = (f"，折扣 ${discount}" if discount else "") + (f"，{note}" if note else "")
        conn.execute(
            "INSERT INTO ledger(date,kind,amount,source,ref_type,ref_id,note,created_by)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (today, "income", total - discount, "pos", "sale", sale_id,
             f"餐點結帳 {_where(order)}（訂單 #{order_id}）{split}{promo}", user_id))
        # 結清不代表完成：訂單留在外場，等餐點上完、客人離開時按「完成」(close_order)
        conn.execute("UPDATE meal_order SET sale_id=? WHERE id=?", (sale_id, order_id))
    return sale_id


def checkout_order(conn, order_id, pay_method, user_id=None, today=None, discount=0, note=None):
    """結清這張訂單所有尚未結帳的品項（可加折扣與優惠備註）。"""
    rows = conn.execute(
        "SELECT id, qty-paid_qty AS rest FROM order_line "
        "WHERE order_id=? AND parent_id IS NULL AND status!='cancelled' AND qty>paid_qty", (order_id,)).fetchall()
    if not rows:
        raise OpsError("沒有可結帳的品項")
    return checkout_lines(conn, order_id, [(r["id"], r["rest"]) for r in rows], pay_method, user_id, today,
                          discount, note)


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
        # 桌位圖上要畫出的餐點（含狀態），依下單順序；套餐帶著組成品項
        "dishes": [{"name": l["name"], "qty": l["qty"], "status": l["status"], "note": l["note"],
                    "seat_no": l.get("seat_no"), "components": [c["name"] for c in l.get("components", [])]}
                   for l in active],
        "queue_no": od.get("queue_no"), "queue": od.get("queue"), "paid_all": od.get("paid_all"),
        "can_close": od.get("can_close"), "seats_info": od.get("seats", []),
        "note": od["note"],
        "opened_at": od["created_at"],
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
                       "counts": {}, "note": None, "dishes": [], **(_tile(od, now) if od else {})})
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
        cur = conn.execute("UPDATE order_line SET status='served' WHERE order_id=? AND status='ready' AND is_combo=0",
                           (order_id,))
        if cur.rowcount == 0:
            raise OpsError("目前沒有可上的菜")
        _sync_combos(conn, order_id)
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


# ───────────────────────── 外部平台訂單（Uber Eats） ─────────────────────────
class KitchenPaused(OpsError):
    """廚房暫停接單：外部訂單應被拒絕。"""


def _map_external_item(conn, source, ext_item):
    """平台品項 → 我們的菜單品項。順序：已記住的對應 → 品名完全相同的餐點 → 建立「未對應」暫存品項。
    回傳 (item, 是否為未對應)。暫存品項停售（不會出現在菜單），但訂單與結帳都能正常使用。"""
    ext_id = (ext_item.get("external_item_id") or "").strip()
    name = (ext_item.get("name") or "").strip()
    if ext_id:
        row = conn.execute(
            "SELECT i.* FROM external_item_map m JOIN item i ON i.id=m.item_id "
            "WHERE m.source=? AND m.external_item_id=?", (source, ext_id)).fetchone()
        if row:
            return dict(row), bool(row["name"].startswith("[Uber] "))
    row = conn.execute("SELECT * FROM item WHERE type='meal' AND active=1 AND name=?", (name,)).fetchone()
    placeholder = False
    if not row:
        label = f"[Uber] {name or ext_id or '未命名品項'}"
        row = conn.execute("SELECT * FROM item WHERE name=?", (label,)).fetchone()
        if not row:
            cur = conn.execute("INSERT INTO item(name,type,category,unit,price,cost,active) "
                               "VALUES (?,?,?,?,?,?,0)", (label, "meal", "meal", "份", 0, 0))
            row = conn.execute("SELECT * FROM item WHERE id=?", (cur.lastrowid,)).fetchone()
        placeholder = True
    if ext_id:
        conn.execute("INSERT OR IGNORE INTO external_item_map(source,external_item_id,item_id) VALUES (?,?,?)",
                     (source, ext_id, row["id"]))
    return dict(row), placeholder


def place_external_order(conn, source, external_id, lines, label=None, note=None):
    """建立外部平台（Uber Eats）訂單，直接進廚房。

    lines = [{"external_item_id", "name", "qty", "note"}]。同一張外部訂單重複送來不會重複建單。
    廚房暫停接單時丟出 KitchenPaused（呼叫端應拒絕該單）。
    回傳 {"order_id", "duplicate", "unmatched": [品名…]}。
    """
    if not external_id:
        raise OpsError("缺少外部訂單編號")
    if not lines:
        raise OpsError("外部訂單沒有品項")
    dup = conn.execute("SELECT id FROM meal_order WHERE source=? AND external_id=?",
                       (source, external_id)).fetchone()
    if dup:
        return {"order_id": dup["id"], "duplicate": True, "unmatched": []}
    ks = kitchen_status(conn)
    if not ks["accepting"]:
        raise KitchenPaused("廚房目前暫停接單" + (f"：{ks['reason']}" if ks["reason"] else ""))
    unmatched = []
    with conn:
        cur = conn.execute(
            "INSERT INTO meal_order(order_type,note,source,external_id,external_label,queue_no) VALUES (?,?,?,?,?,?)",
            ("takeout", (note or "").strip() or None, source, external_id, (label or "").strip() or None,
             next_queue_no(conn)))
        order_id = cur.lastrowid
        for ln in lines:
            qty = 1 if ln.get("qty") is None else int(ln["qty"])   # 沒填 = 1；明確填 0 或負數要擋下
            if qty <= 0:
                raise OpsError("數量需大於 0")
            item, placeholder = _map_external_item(conn, source, ln)
            lnote = (ln.get("note") or "").strip()
            if placeholder:
                unmatched.append(ln.get("name") or item["name"])
                lnote = (lnote + "；" if lnote else "") + "未對應菜單，請核對品項與價格"
            _insert_line(conn, order_id, 1, item, qty, lnote or None)
    return {"order_id": order_id, "duplicate": False, "unmatched": unmatched}


def cancel_external_order(conn, source, external_id):
    """平台通知取消。還沒開始製作 → 直接取消；已開始 → 保留並在整單備註加上醒目警告，交給人處理。
    回傳 "cancelled" / "flagged" / "unknown" / "closed"。"""
    row = conn.execute("SELECT * FROM meal_order WHERE source=? AND external_id=?", (source, external_id)).fetchone()
    if not row:
        return "unknown"
    if row["status"] != "open":
        return "closed"
    try:
        cancel_order(conn, row["id"])
        return "cancelled"
    except OpsError:
        with conn:
            warn = "⚠️ 平台已取消此單，請與廚房確認是否停止製作"
            conn.execute("UPDATE meal_order SET note=? WHERE id=?",
                         (f"{warn}；{row['note']}" if row["note"] else warn, row["id"]))
        return "flagged"


def record_external_event(conn, source, external_id, event_type, status, note=None, payload=None):
    with conn:
        conn.execute("INSERT INTO external_event(source,external_id,event_type,status,note,payload) VALUES (?,?,?,?,?,?)",
                     (source, external_id, event_type, status, note, payload))


def external_alerts(conn, source="uber", hours=3):
    """最近處理失敗的外部訂單數（外場頁用來提醒人工處理）。"""
    return conn.execute(
        "SELECT COUNT(*) FROM external_event WHERE source=? AND status='failed' "
        "AND received_at >= datetime('now','localtime',?)", (source, f"-{int(hours)} hours")).fetchone()[0]


def order_source(conn, order_id):
    row = conn.execute("SELECT source, external_id FROM meal_order WHERE id=?", (order_id,)).fetchone()
    return (row["source"], row["external_id"]) if row else (None, None)


# ───────────────────────── 廚房備料總表 ─────────────────────────
def prep_summary(conn, station="kitchen"):
    """廚房（或吧台）最上方：所有還沒出餐的品項，依品名加總（待接單＋製作中）。份數多的在前。"""
    rows = conn.execute(
        """SELECT i.name,
                  SUM(CASE WHEN l.status='new' THEN l.qty ELSE 0 END) AS new_qty,
                  SUM(CASE WHEN l.status='preparing' THEN l.qty ELSE 0 END) AS preparing_qty,
                  SUM(l.qty) AS total
           FROM order_line l JOIN meal_order o ON o.id=l.order_id JOIN item i ON i.id=l.item_id
           WHERE o.status='open' AND l.status IN ('new','preparing') AND l.is_combo=0 AND l.station=?
           GROUP BY i.id ORDER BY total DESC, i.name""", (station,)).fetchall()
    return [dict(r) for r in rows]


# ───────────────────────── 廚房呼叫外場 ─────────────────────────
CALL_MESSAGES = ["請來取餐", "需要人手", "食材不足", "請確認訂單"]          # 廚房 → 外場
KITCHEN_MESSAGES = ["請加快這桌", "客人有過敏，請注意", "客人要求取消", "請確認這桌的單"]   # 外場 → 廚房


def call_floor(conn, message, table_no=None, user_id=None, direction="to_floor"):
    """呼叫另一邊：預設廚房呼叫外場；direction="to_kitchen" 為外場通知廚房。回傳呼叫編號。"""
    if direction not in ("to_floor", "to_kitchen", "to_bar"):
        raise OpsError("呼叫方向錯誤")
    message = (message or "").strip()
    if not message:
        raise OpsError("請選擇或輸入要通知外場的內容")
    if len(message) > 100:
        raise OpsError("內容請在 100 字以內")
    with conn:
        cur = conn.execute("INSERT INTO kitchen_call(message,table_no,created_by,direction) VALUES (?,?,?,?)",
                           (message, (table_no or "").strip() or None, user_id, direction))
    return cur.lastrowid


def call_kitchen(conn, message, table_no=None, user_id=None, station="kitchen"):
    """外場通知廚房或吧台（對方會響鈴、跳出視窗）。"""
    return call_floor(conn, message, table_no, user_id, direction="to_bar" if station == "bar" else "to_kitchen")


def open_calls(conn, direction="to_floor"):
    """還沒回應的呼叫（預設：給外場的），舊的在前。"""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM kitchen_call WHERE acked_at IS NULL AND direction=? ORDER BY id", (direction,))]


def recent_calls(conn, limit=5, direction="to_floor"):
    """最近送出的呼叫與對方是否已收到。"""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM kitchen_call WHERE direction=? ORDER BY id DESC LIMIT ?", (direction, limit))]


def ack_call(conn, call_id, user_id=None):
    """外場按「收到」。已經有人回應過就不重複記錄。回傳 True＝這次回應成功。"""
    with conn:
        cur = conn.execute(
            "UPDATE kitchen_call SET acked_by=?, acked_at=datetime('now','localtime') "
            "WHERE id=? AND acked_at IS NULL", (user_id, call_id))
    return cur.rowcount == 1


# ───────────────────────── 排隊叫號看板 ─────────────────────────
QUEUE_GROUPS = [("cooking", "準備中"), ("ready", "可上菜／取餐"), ("dining", "用餐中")]


def queue_board(conn):
    """所有進行中的單（內用、外帶、Uber）依狀態分成三區：準備中、可上菜／取餐、用餐中（含已結帳待離開）。"""
    groups = {k: [] for k, _ in QUEUE_GROUPS}
    for od in open_orders(conn):
        if od["to_serve"]:
            g = "ready"
        elif od["unfinished"]:
            g = "cooking"
        else:
            g = "dining"
        kind = "dine_in" if od["order_type"] == "dine_in" else ("uber" if od["source"] == "uber" else "takeout")
        groups[g].append({"order_id": od["id"], "queue": od["queue"], "queue_no": od["queue_no"] or 0,
                          "where": od["where"], "kind": kind, "paid_all": od["paid_all"],
                          "can_close": od["can_close"], "created_at": od["created_at"]})
    for g in groups.values():
        g.sort(key=lambda x: x["queue_no"])
    return groups


# ───────────────────────── 製作區、套餐 ─────────────────────────
STATIONS = {"kitchen": "廚房", "bar": "吧台"}


def set_station(conn, item_id, station):
    if station not in STATIONS:
        raise OpsError("製作區只能是廚房或吧台")
    with conn:
        conn.execute("UPDATE item SET station=? WHERE id=?", (station, item_id))


def combo_components(conn, combo_id):
    """套餐的組成品項：[{id, name, station, qty}]；不是套餐則回傳空清單。"""
    return [dict(r) for r in conn.execute(
        "SELECT i.id, i.name, i.station, c.qty FROM combo_item c JOIN item i ON i.id=c.component_id "
        "WHERE c.combo_id=? ORDER BY i.station, i.name", (combo_id,))]


def set_combo(conn, combo_id, components):
    """設定套餐內容。components = [(品項 id, 份數), ...]；空清單＝取消套餐設定（變回單點）。"""
    combo = s.get_item(conn, combo_id)
    if combo["type"] != "meal":
        raise OpsError("只有餐點可以設成套餐")
    clean = {}
    for cid, qty in components:
        cid = int(cid)
        if cid == combo_id:
            raise OpsError("套餐不能包含自己")
        comp = s.get_item(conn, cid)
        if comp["type"] != "meal":
            raise OpsError(f"「{comp['name']}」不是餐點")
        if combo_components(conn, cid):
            raise OpsError(f"「{comp['name']}」本身是套餐，不能再放進套餐")
        if int(qty) <= 0:
            raise OpsError("份數需大於 0")
        clean[cid] = clean.get(cid, 0) + int(qty)
    with conn:
        conn.execute("DELETE FROM combo_item WHERE combo_id=?", (combo_id,))
        for cid, qty in clean.items():
            conn.execute("INSERT INTO combo_item(combo_id,component_id,qty) VALUES (?,?,?)", (combo_id, cid, qty))


# ───────────────────────── 熟客名單、座位 ─────────────────────────
def add_customer(conn, name, phone=None, avoid=None, note=None):
    name = (name or "").strip()
    if not name:
        raise OpsError("請輸入熟客名字")
    try:
        with conn:
            cur = conn.execute("INSERT INTO customer(name,phone,avoid,note) VALUES (?,?,?,?)",
                               (name, (phone or "").strip() or None, (avoid or "").strip() or None,
                                (note or "").strip() or None))
    except Exception as e:
        raise OpsError("這個名字已經在熟客名單裡") from e
    return cur.lastrowid


def update_customer(conn, customer_id, **fields):
    allowed = {k: v for k, v in fields.items() if k in ("name", "phone", "avoid", "note", "active")}
    if "name" in allowed and not (allowed["name"] or "").strip():
        raise OpsError("名字不可空白")
    if allowed:
        vals = [(v.strip() or None) if isinstance(v, str) else v for v in allowed.values()]
        with conn:
            conn.execute(f"UPDATE customer SET {', '.join(k + '=?' for k in allowed)} WHERE id=?",
                         (*vals, customer_id))


def list_customers(conn, active_only=True):
    q = "SELECT * FROM customer" + (" WHERE active=1" if active_only else "") + " ORDER BY name"
    return [dict(r) for r in conn.execute(q)]


def get_customer(conn, customer_id):
    row = conn.execute("SELECT * FROM customer WHERE id=?", (customer_id,)).fetchone()
    return dict(row) if row else None


def _save_seat(conn, order_id, seat_no, guest_name=None, customer_id=None, avoid=None):
    if customer_id:
        cust = get_customer(conn, customer_id)
        if not cust:
            raise OpsError("找不到這位熟客")
        guest_name = guest_name or cust["name"]
        if cust["avoid"] and cust["avoid"] not in (avoid or ""):     # 熟客的忌口自動帶入
            avoid = f"{cust['avoid']}；{avoid}" if avoid else cust["avoid"]
    guest_name = (guest_name or "").strip() or None
    avoid = (avoid or "").strip() or None
    if not guest_name and not avoid and not customer_id:
        conn.execute("DELETE FROM order_seat WHERE order_id=? AND seat_no=?", (order_id, seat_no))
        return
    conn.execute(
        "INSERT INTO order_seat(order_id,seat_no,guest_name,customer_id,avoid) VALUES (?,?,?,?,?) "
        "ON CONFLICT(order_id,seat_no) DO UPDATE SET guest_name=excluded.guest_name, "
        "customer_id=excluded.customer_id, avoid=excluded.avoid",
        (order_id, seat_no, guest_name, customer_id, avoid))


def set_seat(conn, order_id, seat_no, guest_name=None, customer_id=None, avoid=None):
    """記錄某個座位：誰來用餐（可選熟客）、不吃什麼。全部留白＝清除這個座位的資料。"""
    if not 1 <= int(seat_no) <= 30:
        raise OpsError("座位號碼錯誤")
    with conn:
        if _get_order(conn, order_id)["status"] != "open":
            raise OpsError("訂單已結束")
        _save_seat(conn, order_id, int(seat_no), guest_name, customer_id, avoid)


def order_seats(conn, order_id):
    return [dict(r) for r in conn.execute(
        "SELECT se.*, c.name AS customer_name FROM order_seat se LEFT JOIN customer c ON c.id=se.customer_id "
        "WHERE se.order_id=? ORDER BY se.seat_no", (order_id,))]


# ───────────────────────── 招待（贈品）、折扣試算 ─────────────────────────
DISCOUNT_PRESETS = [("none", "不打折", 1.0), ("p95", "95 折", 0.95), ("p9", "9 折", 0.9),
                    ("p85", "85 折", 0.85), ("p8", "8 折", 0.8), ("amount", "折抵金額", None)]


def discount_amount(subtotal, preset, amount=0):
    """折扣金額：依預設折數（四捨五入到元）或直接輸入的金額，不會超過小計。"""
    rate = dict((k, r) for k, _, r in DISCOUNT_PRESETS).get(preset)
    if preset == "amount":
        d = int(amount or 0)
    elif rate is None:
        raise OpsError("折扣方式錯誤")
    else:
        d = subtotal - int(round(subtotal * rate))
    return max(0, min(subtotal, d))


def set_gift(conn, line_id, reason=None):
    """把一個品項改成招待（贈品，不收錢）。已結帳的不能改；套餐整份招待。"""
    with conn:
        row = conn.execute(
            "SELECT l.*, o.status AS ostatus FROM order_line l JOIN meal_order o ON o.id=l.order_id WHERE l.id=?",
            (line_id,)).fetchone()
        if not row or row["ostatus"] != "open" or row["status"] == "cancelled":
            raise OpsError("找不到可以招待的品項")
        if row["parent_id"]:
            raise OpsError("套餐裡的品項請招待整份套餐")
        if row["paid_qty"]:
            raise OpsError("這個品項已結帳，不能改成招待")
        reason = (reason or "").strip()
        tag = "招待" + (f"：{reason}" if reason else "")
        note = f"{row['note']}；{tag}" if row["note"] else tag
        conn.execute("UPDATE order_line SET unit_price=0, gift=1, note=? WHERE id=?", (note, line_id))


# ───────────────────────── 售完、剩餘份數 ─────────────────────────
def _consume(conn, item, qty):
    """扣份數；售完或份數不夠就擋下（整筆訂單一起回復）。"""
    row = conn.execute("SELECT name, sold_out, portions_left FROM item WHERE id=?", (item["id"],)).fetchone()
    if row["sold_out"]:
        raise OpsError(f"「{row['name']}」已售完")
    if row["portions_left"] is not None:
        if qty > row["portions_left"]:
            raise OpsError(f"「{row['name']}」只剩 {row['portions_left']} 份")
        conn.execute("UPDATE item SET portions_left=portions_left-? WHERE id=?", (qty, item["id"]))


def _restore(conn, item_id, qty):
    """取消時把份數還回去。"""
    conn.execute("UPDATE item SET portions_left=portions_left+? WHERE id=? AND portions_left IS NOT NULL",
                 (qty, item_id))


def set_sold_out(conn, item_id, sold_out=True):
    """一鍵售完／恢復供應。恢復時若剩餘份數是 0，改回不限份數。"""
    with conn:
        conn.execute("UPDATE item SET sold_out=? WHERE id=?", (1 if sold_out else 0, item_id))
        if not sold_out:
            conn.execute("UPDATE item SET portions_left=NULL WHERE id=? AND portions_left=0", (item_id,))


def set_portions(conn, item_id, portions):
    """設定今天還剩幾份；None＝不限。賣到 0 會自動視為售完。"""
    if portions is not None and int(portions) < 0:
        raise OpsError("份數不可為負")
    with conn:
        conn.execute("UPDATE item SET portions_left=? WHERE id=?",
                     (None if portions is None else int(portions), item_id))


def availability(conn):
    """每個餐點能不能點：{item_id: {"ok": bool, "left": 剩幾份或 None, "why": 原因}}。
    套餐只要有一個組成品項售完，套餐也不能點。"""
    items = {r["id"]: dict(r) for r in conn.execute("SELECT id, name, sold_out, portions_left FROM item WHERE type='meal'")}
    out = {}
    for iid, it in items.items():
        ok, why, left = True, "", it["portions_left"]
        if it["sold_out"] or left == 0:
            ok, why = False, "售完"
        for comp in combo_components(conn, iid):
            c = items.get(comp["id"])
            if c and (c["sold_out"] or c["portions_left"] == 0):
                ok, why, left = False, f"{c['name']}售完", 0
            elif c and c["portions_left"] is not None:
                cap = c["portions_left"] // comp["qty"]
                left = cap if left is None else min(left, cap)
        out[iid] = {"ok": ok, "left": left, "why": why}
    return out


# ───────────────────────── 加價選項 ─────────────────────────
def parse_choices(text):
    """把「大份+20, 小份-10, 正常」這種寫法轉成 [(名稱, 價差)]。全形逗號、頓號、換行都可以分隔。"""
    import re
    out = []
    for raw in re.split(r"[,，、\n]+", text or ""):
        raw = raw.strip()
        if not raw:
            continue
        m = re.fullmatch(r"(.+?)\s*([+-]\s*\d+)?", raw)
        name = m.group(1).strip()
        delta = int(m.group(2).replace(" ", "")) if m.group(2) else 0
        out.append((name, delta))
    return out


def item_options(conn, item_id):
    """[{id, name, required, multi, choices: [{id, name, price_delta}]}]"""
    groups = []
    for g in conn.execute("SELECT * FROM option_group WHERE item_id=? ORDER BY sort, id", (item_id,)).fetchall():
        choices = [dict(c) for c in conn.execute(
            "SELECT id, name, price_delta FROM option_choice WHERE group_id=? ORDER BY sort, id", (g["id"],))]
        groups.append({"id": g["id"], "name": g["name"], "required": bool(g["required"]),
                       "multi": bool(g["multi"]), "choices": choices})
    return groups


def add_option_group(conn, item_id, name, choices, required=False, multi=False):
    """新增選項群組。choices 可以是 [(名稱, 價差)] 或「大份+20, 小份-10」這種文字。"""
    name = (name or "").strip()
    if not name:
        raise OpsError("請輸入選項群組名稱（例：份量、加料、冰量）")
    if isinstance(choices, str):
        choices = parse_choices(choices)
    if not choices:
        raise OpsError("請至少輸入一個選項")
    s.get_item(conn, item_id)
    with conn:
        n = conn.execute("SELECT COUNT(*) FROM option_group WHERE item_id=?", (item_id,)).fetchone()[0]
        gid = conn.execute("INSERT INTO option_group(item_id,name,required,multi,sort) VALUES (?,?,?,?,?)",
                           (item_id, name, 1 if required else 0, 1 if multi else 0, n)).lastrowid
        for i, (cname, delta) in enumerate(choices):
            conn.execute("INSERT INTO option_choice(group_id,name,price_delta,sort) VALUES (?,?,?,?)",
                         (gid, cname, int(delta), i))
    return gid


def delete_option_group(conn, group_id):
    with conn:
        conn.execute("DELETE FROM option_choice WHERE group_id=?", (group_id,))
        conn.execute("DELETE FROM option_group WHERE id=?", (group_id,))


def resolve_options(conn, item, option_ids):
    """檢查選項並算出加價：必選要選、單選只能選一個、不能選別的品項的選項。回傳 (加價總和, 顯示文字)。"""
    groups = item_options(conn, item["id"])
    by_choice = {c["id"]: (g, c) for g in groups for c in g["choices"]}
    picked = {}
    for oid in option_ids:
        if oid not in by_choice:
            raise OpsError(f"「{item['name']}」的選項不正確")
        g, c = by_choice[oid]
        picked.setdefault(g["id"], []).append(c)
    for g in groups:
        chosen = picked.get(g["id"], [])
        if g["required"] and not chosen:
            raise OpsError(f"「{item['name']}」請選擇{g['name']}")
        if not g["multi"] and len(chosen) > 1:
            raise OpsError(f"「{item['name']}」的{g['name']}只能選一個")
    texts, delta = [], 0
    for g in groups:
        for c in picked.get(g["id"], []):
            delta += c["price_delta"]
            d = c["price_delta"]
            texts.append(c["name"] + (f"(+{d})" if d > 0 else f"({d})" if d < 0 else ""))
    return delta, ("、".join(texts) or None)


# ───────────────────────── 報表 ─────────────────────────
def sales_report(conn, start, end):
    """餐點營業報表（start～end 含首尾）：營收、單數、客單價、來客數、折扣、招待、排行、時段、付款方式。"""
    s_, e_ = start.isoformat(), end.isoformat()
    sales = [dict(r) for r in conn.execute(
        """SELECT * FROM sale WHERE status='done' AND date(created_at) BETWEEN ? AND ?
           AND id IN (SELECT sale_id FROM order_payment_line)""", (s_, e_))]
    revenue = sum(x["total"] for x in sales)
    discount = sum(x["discount"] for x in sales)
    orders_ = [dict(r) for r in conn.execute(
        "SELECT * FROM meal_order WHERE status!='cancelled' AND date(created_at) BETWEEN ? AND ?", (s_, e_))]
    n_orders = len(orders_)
    guests = sum(o["guests"] or 0 for o in orders_)
    ranking = [dict(r) for r in conn.execute(
        """SELECT i.name, SUM(l.qty) AS qty, SUM(l.qty*l.unit_price) AS amount, SUM(CASE WHEN l.gift=1 THEN l.qty ELSE 0 END) AS gift_qty
           FROM order_line l JOIN meal_order o ON o.id=l.order_id JOIN item i ON i.id=l.item_id
           WHERE l.parent_id IS NULL AND l.status!='cancelled' AND o.status!='cancelled'
             AND date(o.created_at) BETWEEN ? AND ?
           GROUP BY i.id ORDER BY qty DESC, amount DESC, i.name""", (s_, e_))]
    gift_value = conn.execute(
        """SELECT COALESCE(SUM(l.qty*i.price),0) FROM order_line l JOIN meal_order o ON o.id=l.order_id
           JOIN item i ON i.id=l.item_id WHERE l.gift=1 AND l.parent_id IS NULL AND l.status!='cancelled'
           AND o.status!='cancelled' AND date(o.created_at) BETWEEN ? AND ?""", (s_, e_)).fetchone()[0]
    by_hour = {}
    for x in sales:
        h = int(x["created_at"][11:13])
        by_hour[h] = by_hour.get(h, 0) + x["total"]
    by_pay = {}
    for x in sales:
        by_pay[x["pay_method"]] = by_pay.get(x["pay_method"], 0) + x["total"]
    # 客單價＝營收 ÷ 有結帳的單數（還沒付款的單不算進分母，否則會被拉低）
    paid_orders = len({r[0] for r in conn.execute(
        "SELECT DISTINCT order_id FROM order_payment_line WHERE sale_id IN (%s)" % ",".join("?" * len(sales)),
        [x["id"] for x in sales])}) if sales else 0
    return {"revenue": revenue, "discount": discount, "gift_value": gift_value, "sales": len(sales),
            "orders": n_orders, "paid_orders": paid_orders, "guests": guests,
            "avg_order": round(revenue / paid_orders) if paid_orders else 0,
            "ranking": ranking, "by_hour": dict(sorted(by_hour.items())),
            "by_pay": dict(sorted(by_pay.items(), key=lambda kv: -kv[1]))}


# ───────────────────────── 交班對帳 ─────────────────────────
def current_shift(conn):
    row = conn.execute("SELECT * FROM shift WHERE closed_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def open_shift(conn, opening_cash, user_id=None, now=None):
    """開班：輸入抽屜裡的零用金。同時只能有一個班。"""
    if current_shift(conn):
        raise OpsError("已經有一個班還沒關，請先關班")
    if int(opening_cash) < 0:
        raise OpsError("零用金不可為負")
    with conn:
        if now:
            return conn.execute("INSERT INTO shift(opened_by,opened_at,opening_cash) VALUES (?,?,?)",
                                (user_id, now, int(opening_cash))).lastrowid
        return conn.execute("INSERT INTO shift(opened_by,opening_cash) VALUES (?,?)",
                            (user_id, int(opening_cash))).lastrowid


def shift_summary(conn, shift_id, now=None):
    """這個班到目前（或關班時）為止：各付款方式收了多少、抽屜應有現金。包含後台 POS 的現金收入；作廢的不算。"""
    sh = dict(conn.execute("SELECT * FROM shift WHERE id=?", (shift_id,)).fetchone())
    end = sh["closed_at"] or now or conn.execute("SELECT datetime('now','localtime')").fetchone()[0]
    rows = conn.execute(
        "SELECT pay_method, COUNT(*) n, SUM(total) amt FROM sale WHERE status='done' AND created_at>=? AND created_at<=? "
        "GROUP BY pay_method ORDER BY amt DESC", (sh["opened_at"], end)).fetchall()
    by_pay = {r["pay_method"]: {"count": r["n"], "amount": r["amt"]} for r in rows}
    cash = by_pay.get("現金", {}).get("amount", 0)
    return {**sh, "until": end, "by_pay": by_pay, "cash_sales": cash,
            "expected_cash": sh["opening_cash"] + cash,
            "total": sum(v["amount"] for v in by_pay.values())}


def close_shift(conn, counted_cash, user_id=None, note=None, now=None):
    """關班：輸入實際點收的現金。回傳 {expected_cash, counted_cash, diff}（diff 正＝多、負＝少）。"""
    sh = current_shift(conn)
    if not sh:
        raise OpsError("目前沒有開班")
    if int(counted_cash) < 0:
        raise OpsError("點收金額不可為負")
    summ = shift_summary(conn, sh["id"], now)
    with conn:
        conn.execute("UPDATE shift SET closed_by=?, closed_at=?, counted_cash=?, expected_cash=?, note=? WHERE id=?",
                     (user_id, summ["until"], int(counted_cash), summ["expected_cash"], (note or "").strip() or None,
                      sh["id"]))
    return {"expected_cash": summ["expected_cash"], "counted_cash": int(counted_cash),
            "diff": int(counted_cash) - summ["expected_cash"], "by_pay": summ["by_pay"]}


def recent_shifts(conn, limit=10):
    return [dict(r) for r in conn.execute("SELECT * FROM shift ORDER BY id DESC LIMIT ?", (limit,))]
