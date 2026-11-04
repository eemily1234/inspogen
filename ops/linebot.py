"""Line 記帳：解析老闆傳來的文字，呼叫 services，回傳要回覆的文字。

不含任何網路呼叫，方便測試。Webhook 與回覆在 line_server.py。
只有「已綁定的老闆帳號」可以記帳；其他人一律只收到綁定說明，不洩漏任何資料。
"""
import re
import secrets
import unicodedata
from datetime import date, datetime, timedelta

from . import services as s

HELP = """📒 Line 記帳指令

支出 午餐 120
進貨 魚油 10 4000 A1 2027-01-01
　（品名 數量 總金額 [批號] [效期]；保健品批號、效期必填）
庫存　　　→ 全部庫存與低庫存
庫存 魚油 → 單一品項與批次
今日　　　→ 今日收支
本月　　　→ 本月收支
復原　　　→ 撤銷上一筆記帳"""

BIND_HELP = "尚未綁定。請老闆在系統「使用者」頁按「產生 Line 綁定碼」，再傳：綁定 123456"


def _norm(text):
    return unicodedata.normalize("NFKC", text or "").strip()


def _amount(tok):
    t = tok.replace(",", "").replace("$", "").replace("元", "")
    if not re.fullmatch(r"\d+", t):
        return None
    return int(t)


def _qty(tok):
    m = re.fullmatch(r"(\d+)[^\d\s]*", tok)  # 允許「10包」「3瓶」
    return int(m.group(1)) if m else None


def _money(n):
    return f"${n:,}"


# ───────────────────────── 綁定 ─────────────────────────
def create_bind_code(conn, user_id, now=None):
    """由網頁（老闆）產生 10 分鐘有效的 6 位數綁定碼。"""
    now = now or datetime.now()
    u = conn.execute("SELECT role FROM user WHERE id=? AND active=1", (user_id,)).fetchone()
    if not u or u["role"] != s.OWNER:
        raise s.OpsError("只有老闆可以綁定 Line 記帳")
    code = f"{secrets.randbelow(10**6):06d}"
    with conn:
        conn.execute("DELETE FROM bind_code WHERE user_id=? OR expires_at < ?",
                     (user_id, now.isoformat(timespec="seconds")))
        conn.execute("INSERT INTO bind_code(code,user_id,expires_at) VALUES (?,?,?)",
                     (code, user_id, (now + timedelta(minutes=10)).isoformat(timespec="seconds")))
    return code


def _bind(conn, line_user_id, code, now):
    row = conn.execute("SELECT * FROM bind_code WHERE code=?", (code,)).fetchone()
    if not row or row["expires_at"] < now.isoformat(timespec="seconds"):
        return "綁定碼無效或已過期，請在系統重新產生。"
    with conn:
        conn.execute("INSERT OR REPLACE INTO line_user(line_user_id,user_id) VALUES (?,?)",
                     (line_user_id, row["user_id"]))
        conn.execute("DELETE FROM bind_code WHERE code=?", (code,))
    return "✅ 綁定成功！傳「說明」查看記帳指令。"


def _bound_user(conn, line_user_id):
    r = conn.execute(
        "SELECT u.* FROM line_user l JOIN user u ON u.id=l.user_id "
        "WHERE l.line_user_id=? AND u.active=1 AND u.role='owner'", (line_user_id,)).fetchone()
    return dict(r) if r else None


# ───────────────────────── 品名比對 ─────────────────────────
def _find_item(conn, name, products_only=True):
    items = [i for i in s.list_items(conn) if (i["type"] == "product" or not products_only)]
    exact = [i for i in items if i["name"] == name]
    if exact:
        return exact[0]
    part = [i for i in items if name in i["name"]]
    if len(part) == 1:
        return part[0]
    if len(part) > 1:
        raise s.OpsError("有多個符合：" + "、".join(i["name"] for i in part) + "，請輸入完整品名")
    raise s.OpsError(f"找不到商品「{name}」，請先在系統新增")


# ───────────────────────── 指令 ─────────────────────────
def _cmd_expense(conn, user, line_user_id, toks):
    if not toks:
        raise s.OpsError("格式：支出 說明 金額，例如：支出 午餐 120")
    if len(toks) >= 2 and _amount(toks[-1]) is not None:
        amount, note = _amount(toks[-1]), " ".join(toks[:-1])
    elif len(toks) >= 2 and _amount(toks[0]) is not None:
        amount, note = _amount(toks[0]), " ".join(toks[1:])
    else:
        raise s.OpsError("看不出金額。格式：支出 說明 金額，例如：支出 午餐 120")
    lid = s.add_expense(conn, amount, note, user["id"], source="line")
    _log(conn, line_user_id, "expense", lid)
    return f"✅ 已記支出：{note} {_money(amount)}\n回「復原」可撤銷"


def _cmd_purchase(conn, user, line_user_id, toks):
    usage = "格式：進貨 品名 數量 總金額 [批號] [效期]\n例：進貨 魚油 10 4000 A1 2027-01-01"
    if len(toks) < 3:
        raise s.OpsError(usage)
    qty, total = _qty(toks[1]), _amount(toks[2])
    if qty is None or total is None:
        raise s.OpsError("數量與金額需為數字。\n" + usage)
    batch = toks[3] if len(toks) > 3 else None
    expiry = toks[4] if len(toks) > 4 else None
    item = _find_item(conn, toks[0])
    mid = s.purchase(conn, item["id"], qty, total, batch, expiry, user["id"], note="Line 記帳")
    _log(conn, line_user_id, "purchase", mid)
    left = s.stock_level(conn, item["id"])
    extra = f"　批號 {batch}" if batch else ""
    extra += f"　效期 {expiry}" if expiry else ""
    return (f"✅ 已進貨：{item['name']} ×{qty}，{_money(total)}{extra}\n"
            f"庫存 {left}{item['unit']}　單位成本 {_money(round(total / qty))}\n回「復原」可撤銷")


def _cmd_stock(conn, toks):
    if toks:
        item = _find_item(conn, " ".join(toks))
        lines = [f"📦 {item['name']}：{s.stock_level(conn, item['id'])}{item['unit']}"
                 f"（安全庫存 {item['safety_stock']}）"]
        for b in s.batches(conn, item["id"]):
            lines.append(f"・批號 {b['batch_no'] or '無'} / 效期 {b['expiry'] or '無'} × {b['qty']}")
        return "\n".join(lines)
    rows = s.stock_overview(conn)
    if not rows:
        return "目前沒有商品。"
    lines = ["📦 庫存"] + [f"{'⚠️' if r['low'] else '・'}{r['name']} {r['qty']}{r['unit']}" for r in rows]
    exp = s.expiring(conn, days=30)
    if exp:
        lines.append("\n⏰ 30 天內到期：")
        lines += [f"・{e['name']} 批號{e['batch_no']} {e['expiry']} × {e['qty']}" for e in exp]
    return "\n".join(lines)


def _cmd_report(conn, label, start, end):
    r = s.report(conn, start, end)
    return (f"📊 {label}\n收入 {_money(r['income'])}\n進貨成本 {_money(r['purchase_cost'])}\n"
            f"支出 {_money(r['expense'])}\n毛利 {_money(r['gross_profit'])}\n"
            f"現金淨額 {_money(r['net'])}")


def _log(conn, line_user_id, kind, ref_id):
    with conn:
        conn.execute("INSERT INTO line_log(line_user_id,kind,ref_id) VALUES (?,?,?)",
                     (line_user_id, kind, ref_id))


def _cmd_undo(conn, user, line_user_id):
    row = conn.execute(
        "SELECT * FROM line_log WHERE line_user_id=? AND undone=0 ORDER BY id DESC LIMIT 1",
        (line_user_id,)).fetchone()
    if not row:
        return "沒有可復原的記帳。"
    if row["kind"] == "expense":
        s.undo_expense(conn, row["ref_id"], user["id"])
        what = "支出"
    else:
        s.undo_purchase(conn, row["ref_id"], user["id"])
        what = "進貨"
    with conn:
        conn.execute("UPDATE line_log SET undone=1 WHERE id=?", (row["id"],))
    return f"↩️ 已復原上一筆{what}。"


def handle_text(conn, line_user_id, text, today=None, now=None):
    """處理一則文字訊息，回傳回覆文字。業務錯誤以友善訊息回覆，不會丟例外。"""
    now = now or datetime.now()
    today = today or date.today()
    text = _norm(text)
    if not text:
        return HELP
    toks = text.split()
    cmd, args = toks[0], toks[1:]
    try:
        if cmd == "綁定":
            if len(args) != 1:
                return "格式：綁定 123456"
            return _bind(conn, line_user_id, args[0], now)
        user = _bound_user(conn, line_user_id)
        if not user:
            return BIND_HELP
        if cmd in ("說明", "help", "?", "幫助"):
            return HELP
        if cmd == "支出":
            return _cmd_expense(conn, user, line_user_id, args)
        if cmd == "進貨":
            return _cmd_purchase(conn, user, line_user_id, args)
        if cmd == "庫存":
            return _cmd_stock(conn, args)
        if cmd == "今日":
            return _cmd_report(conn, "今日", today, today)
        if cmd == "本月":
            return _cmd_report(conn, "本月", today.replace(day=1), today)
        if cmd == "復原":
            return _cmd_undo(conn, user, line_user_id)
        return "看不懂這個指令。\n\n" + HELP
    except s.OpsError as e:
        return f"❌ {e}"
