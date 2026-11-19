"""電子發票（店內這一段）。

做的事：
- 結帳時記下買受人資料：一般（列印證明聯）、手機條碼載具、自然人憑證載具、統一編號、捐贈碼，並檢查格式。
- 從「字軌」（財政部配給的發票號碼區間，例如 AB 12345000～12345049）依序取號，與結帳同一筆交易寫入。
- 作廢銷貨單時一併作廢發票（只能在同一期內；跨期要開折讓單，交給會計）。
- 每張開立／作廢都放進上傳佇列（invoice_event），之後由加值中心的轉接器 (Provider) 送出。

還沒做的事：真的上傳到財政部。加值中心還沒選，目前佇列只會累積「待上傳」。
選好業者後實作一個 Provider（send(event) 成功回傳 None、失敗丟例外），再呼叫 sync()。
"""
import re
import secrets
from datetime import date

from .services import OpsError

TAX_RATE = 0.05
# 這些付款方式由平台自己開發票給客人（店家不重複開）
EXEMPT_PAY = {"Uber Eats"}

CARRIER_TYPES = {
    "print": "一般（列印證明聯）",
    "mobile": "手機條碼載具",
    "cdc": "自然人憑證",
    "ban": "統一編號（公司）",
    "donate": "捐贈",
}


# ───────────────────────── 格式檢查 ─────────────────────────
def valid_ban(ban):
    """統一編號檢查碼（財政部規則：權數 1,2,1,2,1,2,4,1，乘積各位數相加後可被 5 整除；第 7 碼為 7 時另一種算法也算對）。"""
    if not re.fullmatch(r"\d{8}", ban or ""):
        return False
    weights = [1, 2, 1, 2, 1, 2, 4, 1]
    total = 0
    for d, w in zip(ban, weights):
        p = int(d) * w
        total += p // 10 + p % 10
    if total % 5 == 0:
        return True
    return ban[6] == "7" and (total + 1) % 5 == 0


def valid_mobile_barcode(code):
    return bool(re.fullmatch(r"/[0-9A-Z.+\-]{7}", code or ""))


def valid_cdc(code):
    return bool(re.fullmatch(r"[A-Z]{2}\d{14}", code or ""))


def valid_donate_code(code):
    return bool(re.fullmatch(r"\d{3,7}", code or ""))


def normalize_buyer(info):
    """把結帳畫面填的資料整理成 {kind, value}，格式錯誤丟 OpsError。info 為 None 視為一般。"""
    info = info or {}
    kind = info.get("kind") or "print"
    value = (info.get("value") or "").strip().upper()
    if kind not in CARRIER_TYPES:
        raise OpsError("發票類型錯誤")
    if kind == "print":
        return {"kind": "print", "value": None}
    checks = {
        "mobile": (valid_mobile_barcode, "手機條碼格式不對（斜線開頭共 8 碼，例：/ABC+123）"),
        "cdc": (valid_cdc, "自然人憑證格式不對（2 個英文字母＋14 個數字）"),
        "ban": (valid_ban, "統一編號不正確（8 位數字，請再核對一次）"),
        "donate": (valid_donate_code, "捐贈碼格式不對（3～7 位數字）"),
    }
    ok, msg = checks[kind]
    if not ok(value):
        raise OpsError(msg)
    return {"kind": kind, "value": value}


# ───────────────────────── 期別與字軌 ─────────────────────────
def period_of(d):
    """發票期別：民國年 + 雙月的偶數月，例如 2025/11、12 月 → 11412。"""
    return f"{d.year - 1911:03d}{((d.month + 1) // 2) * 2:02d}"


def period_label(period):
    y, m = int(period[:3]), int(period[3:])
    return f"{y} 年 {m - 1}–{m} 月"


def _setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def enabled(conn):
    return _setting(conn, "einvoice_enabled", "0") == "1"


def set_enabled(conn, on):
    with conn:
        conn.execute("INSERT INTO setting(key,value) VALUES ('einvoice_enabled',?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("1" if on else "0",))


def add_track(conn, period, prefix, start_no, end_no):
    """新增字軌（號碼區間）。period 例：11412；prefix 兩個大寫英文字母；號碼 8 位數。"""
    period = str(period).strip()
    prefix = (prefix or "").strip().upper()
    if not re.fullmatch(r"\d{3}(02|04|06|08|10|12)", period):
        raise OpsError("期別格式：民國年＋偶數月，例 11412（11–12 月）")
    if not re.fullmatch(r"[A-Z]{2}", prefix):
        raise OpsError("字軌是兩個英文字母，例 AB")
    start_no, end_no = int(start_no), int(end_no)
    if not (0 <= start_no <= end_no <= 99999999):
        raise OpsError("號碼區間不正確（起號要小於等於迄號，8 位數以內）")
    with conn:
        clash = conn.execute(
            "SELECT 1 FROM invoice_track WHERE period=? AND prefix=? AND NOT (end_no<? OR start_no>?)",
            (period, prefix, start_no, end_no)).fetchone()
        if clash:
            raise OpsError("這段號碼和已登記的字軌重疊")
        cur = conn.execute("INSERT INTO invoice_track(period,prefix,start_no,end_no,next_no) VALUES (?,?,?,?,?)",
                           (period, prefix, start_no, end_no, start_no))
    return cur.lastrowid


def list_tracks(conn, period=None):
    q = "SELECT *, end_no-next_no+1 AS remaining FROM invoice_track"
    args = ()
    if period:
        q += " WHERE period=?"
        args = (period,)
    return [dict(r) for r in conn.execute(q + " ORDER BY period DESC, id", args)]


def remaining(conn, today=None):
    p = period_of(today or date.today())
    row = conn.execute("SELECT COALESCE(SUM(end_no-next_no+1),0) FROM invoice_track WHERE period=? "
                       "AND next_no<=end_no", (p,)).fetchone()
    return row[0]


def _next_number(conn, period):
    t = conn.execute("SELECT * FROM invoice_track WHERE period=? AND next_no<=end_no ORDER BY id LIMIT 1",
                     (period,)).fetchone()
    if not t:
        raise OpsError(f"{period_label(period)} 的發票號碼用完了或還沒登記，請到 設定 → 電子發票 新增字軌")
    conn.execute("UPDATE invoice_track SET next_no=next_no+1 WHERE id=?", (t["id"],))
    return f"{t['prefix']}{t['next_no']:08d}"


# ───────────────────────── 開立／作廢（在結帳的交易裡呼叫） ─────────────────────────
def should_issue(conn, pay_method, amount):
    return enabled(conn) and pay_method not in EXEMPT_PAY and amount > 0


def issue_in_tx(conn, sale_id, amount, buyer, today):
    """在呼叫端已開啟的交易裡開發票。buyer 需先經 normalize_buyer。回傳發票 dict。"""
    period = period_of(today)
    number = _next_number(conn, period)
    sales_amount = round(amount / (1 + TAX_RATE))
    tax = amount - sales_amount
    random_code = f"{secrets.randbelow(10000):04d}"
    kind, value = buyer["kind"], buyer["value"]
    cur = conn.execute(
        "INSERT INTO invoice(sale_id,number,period,issued_on,amount,sales_amount,tax,buyer_ban,carrier_type,"
        "carrier_id,donate_code,random_code) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (sale_id, number, period, today.isoformat(), amount, sales_amount, tax,
         value if kind == "ban" else None, kind if kind in ("mobile", "cdc") else None,
         value if kind in ("mobile", "cdc") else None, value if kind == "donate" else None, random_code))
    conn.execute("INSERT INTO invoice_event(invoice_id,kind) VALUES (?, 'issue')", (cur.lastrowid,))
    conn.execute("UPDATE sale SET invoice_no=? WHERE id=?", (number, sale_id))
    return {"number": number, "random_code": random_code, "amount": amount, "kind": kind,
            "print": kind in ("print", "ban")}


def void_in_tx(conn, sale_id, today, reason="銷貨作廢"):
    """在作廢銷貨的交易裡一併作廢發票。跨期不能作廢（要開折讓單）。沒有發票就什麼都不做。"""
    inv = conn.execute("SELECT * FROM invoice WHERE sale_id=? AND status='issued'", (sale_id,)).fetchone()
    if not inv:
        return None
    if inv["period"] != period_of(today):
        raise OpsError(f"發票 {inv['number']} 是 {period_label(inv['period'])} 開的，已跨期不能作廢；"
                       "請改開折讓單（請洽會計）")
    conn.execute("UPDATE invoice SET status='void', void_reason=?, voided_at=datetime('now','localtime') "
                 "WHERE id=?", (reason, inv["id"]))
    conn.execute("INSERT INTO invoice_event(invoice_id,kind) VALUES (?, 'void')", (inv["id"],))
    return inv["number"]


def for_sale(conn, sale_id):
    r = conn.execute("SELECT * FROM invoice WHERE sale_id=?", (sale_id,)).fetchone()
    return dict(r) if r else None


def list_invoices(conn, day=None, limit=100):
    day = (day or date.today()).isoformat()
    return [dict(r) for r in conn.execute(
        "SELECT * FROM invoice WHERE issued_on=? ORDER BY id DESC LIMIT ?", (day, limit))]


def describe_buyer(inv):
    if inv.get("buyer_ban"):
        return f"統編 {inv['buyer_ban']}"
    if inv.get("carrier_type"):
        return f"{CARRIER_TYPES[inv['carrier_type']]} {inv['carrier_id']}"
    if inv.get("donate_code"):
        return f"捐贈 {inv['donate_code']}"
    return "列印證明聯"


# ───────────────────────── 上傳佇列 ─────────────────────────
def pending_events(conn, limit=200):
    return [dict(r) for r in conn.execute(
        "SELECT e.*, i.number, i.sale_id FROM invoice_event e JOIN invoice i ON i.id=e.invoice_id "
        "WHERE e.status!='sent' ORDER BY e.id LIMIT ?", (limit,))]


def queue_counts(conn):
    rows = conn.execute("SELECT status, COUNT(*) AS n FROM invoice_event GROUP BY status").fetchall()
    c = {"pending": 0, "sent": 0, "failed": 0}
    c.update({r["status"]: r["n"] for r in rows})
    return c


def sync(conn, provider, limit=200):
    """把待上傳的開立／作廢依序交給 provider.send(event)。某張開立失敗時，同一張的作廢不會先送。"""
    sent = failed = 0
    blocked = set()
    for ev in pending_events(conn, limit):
        if ev["invoice_id"] in blocked:
            continue
        try:
            provider.send(ev)
        except Exception as e:  # 業者 API 的任何錯誤：記下來，下次再試
            blocked.add(ev["invoice_id"])
            with conn:
                conn.execute("UPDATE invoice_event SET status='failed', attempts=attempts+1, last_error=? "
                             "WHERE id=?", (str(e)[:500], ev["id"]))
            failed += 1
            continue
        with conn:
            conn.execute("UPDATE invoice_event SET status='sent', attempts=attempts+1, last_error=NULL, "
                         "sent_at=datetime('now','localtime') WHERE id=?", (ev["id"],))
        sent += 1
    return {"sent": sent, "failed": failed}
