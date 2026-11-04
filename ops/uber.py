"""Uber Eats 串接：取得訂單、解析、接受／拒絕、通知備餐完成。

⚠️ 重要：本檔依 Uber 公開資訊與一般慣例撰寫，**尚未用真實的 Uber 憑證與真實訂單驗證**。
- 已確認（Uber 官方說明）：Webhook 以 `X-Uber-Signature`（用 client secret 對內容做 HMAC-SHA256，小寫十六進位）簽章；
  收到要回 200 且內容為空；之後必須在 11.5 分鐘內接受或拒絕，否則自動取消；`orders.notification` 表示新單、
  `orders.failure` 表示取消。
- 未能確認：各 API 的**確切網址路徑**、訂單 JSON 的欄位細節。網址路徑可用環境變數覆寫（見 UberConfig），
  訂單解析刻意寫得寬鬆，並把原始內容存進 external_event，方便拿到真實樣本後校正。
"""
import json
import os
import time
from dataclasses import dataclass, field

import requests

from . import orders


@dataclass
class UberConfig:
    client_id: str = field(default_factory=lambda: os.environ.get("UBER_CLIENT_ID", ""))
    client_secret: str = field(default_factory=lambda: os.environ.get("UBER_CLIENT_SECRET", ""))
    api_base: str = field(default_factory=lambda: os.environ.get("UBER_API_BASE", "https://api.uber.com"))
    token_url: str = field(default_factory=lambda: os.environ.get("UBER_TOKEN_URL", "https://auth.uber.com/oauth/v2/token"))
    scope: str = field(default_factory=lambda: os.environ.get("UBER_SCOPE", "eats.order eats.store.orders.read"))
    # 以下路徑為「依印象的預設值」，未經驗證；請對照 Uber 目前的 API 文件，必要時用環境變數覆寫
    order_path: str = field(default_factory=lambda: os.environ.get("UBER_ORDER_PATH", "/v2/eats/order/{id}"))
    accept_path: str = field(default_factory=lambda: os.environ.get("UBER_ACCEPT_PATH", "/v1/eats/orders/{id}/accept_pos_order"))
    deny_path: str = field(default_factory=lambda: os.environ.get("UBER_DENY_PATH", "/v1/eats/orders/{id}/deny_pos_order"))
    ready_path: str = field(default_factory=lambda: os.environ.get("UBER_READY_PATH", "/v1/eats/orders/{id}/ready"))
    deny_reason: str = field(default_factory=lambda: os.environ.get("UBER_DENY_REASON", "STORE_CLOSED"))

    @property
    def configured(self):
        return bool(self.client_id and self.client_secret)


class UberClient:
    def __init__(self, cfg=None, http=None):
        self.cfg = cfg or UberConfig()
        self.http = http or requests
        self._token, self._expires = None, 0

    def _auth(self):
        if self._token and time.time() < self._expires - 60:
            return {"Authorization": f"Bearer {self._token}"}
        r = self.http.post(self.cfg.token_url, data={
            "client_id": self.cfg.client_id, "client_secret": self.cfg.client_secret,
            "grant_type": "client_credentials", "scope": self.cfg.scope}, timeout=15)
        r.raise_for_status()
        data = r.json()
        self._token, self._expires = data["access_token"], time.time() + int(data.get("expires_in", 3600))
        return {"Authorization": f"Bearer {self._token}"}

    def _url(self, template, order_id):
        return self.cfg.api_base.rstrip("/") + template.format(id=order_id)

    def get_order(self, order_id, href=None):
        r = self.http.get(href or self._url(self.cfg.order_path, order_id), headers=self._auth(), timeout=15)
        r.raise_for_status()
        return r.json()

    def accept(self, order_id):
        r = self.http.post(self._url(self.cfg.accept_path, order_id), headers=self._auth(),
                           json={"reason": "accepted"}, timeout=15)
        r.raise_for_status()

    def deny(self, order_id, explanation="廚房目前暫停接單"):
        r = self.http.post(self._url(self.cfg.deny_path, order_id), headers=self._auth(),
                           json={"reason": {"type": self.cfg.deny_reason, "explanation": explanation}}, timeout=15)
        r.raise_for_status()

    def mark_ready(self, order_id):
        r = self.http.post(self._url(self.cfg.ready_path, order_id), headers=self._auth(), json={}, timeout=15)
        r.raise_for_status()


# ───────────────────────── 訂單解析（寬鬆；待真實樣本校正） ─────────────────────────
def _text(*vals):
    for v in vals:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def parse_uber_order(o):
    """把 Uber 訂單 JSON 轉成 {external_id, label, note, lines}。找不到的欄位以空值處理，不丟例外。"""
    if isinstance(o, dict) and isinstance(o.get("order"), dict):   # 有些回應外面多包一層
        o = o["order"]
    ext_id = _text(o.get("id"), o.get("order_id"))
    label = _text(o.get("display_id"), (ext_id or "")[-4:].upper())
    cart = o.get("cart") if isinstance(o.get("cart"), dict) else {}
    items = cart.get("items") or o.get("items") or []
    lines = []
    for it in items:
        notes = []
        if _text(it.get("special_instructions")):
            notes.append(_text(it.get("special_instructions")))
        for grp in it.get("selected_modifier_groups") or []:
            for sel in grp.get("selected_items") or []:
                t = _text(sel.get("title"), sel.get("name"))
                if t:
                    q = int(sel.get("quantity") or 1)
                    notes.append(f"{t}×{q}" if q > 1 else t)
        ext_item = it.get("id") or it.get("external_data")
        lines.append({
            "external_item_id": str(ext_item) if ext_item else None,
            "name": _text(it.get("title"), it.get("name")),
            "qty": 1 if it.get("quantity") is None else int(it["quantity"]),
            "note": "、".join(notes) or None,
        })
    return {"external_id": ext_id, "label": label,
            "note": _text(o.get("special_instructions"), cart.get("special_instructions")),
            "lines": lines}


# ───────────────────────── 處理 webhook 事件 ─────────────────────────
def handle_event(conn, client, event):
    """處理一個 Uber webhook 事件。回傳 (status, note)。所有結果都會寫進 external_event。"""
    etype = event.get("event_type") or ""
    meta = event.get("meta") or {}
    ext_id = _text(meta.get("resource_id"), event.get("resource_id"), event.get("id"))
    href = _text(event.get("resource_href"), meta.get("resource_href"))
    raw = json.dumps(event, ensure_ascii=False)[:20000]
    try:
        if etype.endswith("failure") or etype.endswith("cancel"):
            result = orders.cancel_external_order(conn, "uber", ext_id)
            orders.record_external_event(conn, "uber", ext_id, etype, "cancelled", result, raw)
            return "cancelled", result
        if not etype.endswith("notification"):
            orders.record_external_event(conn, "uber", ext_id, etype, "ok", "忽略的事件類型", raw)
            return "ok", "ignored"
        if not ext_id:
            raise orders.OpsError("事件缺少訂單編號")
        # 重複通知（Uber 可能重送）：已建過單就不再處理
        if conn.execute("SELECT 1 FROM meal_order WHERE source='uber' AND external_id=?", (ext_id,)).fetchone():
            orders.record_external_event(conn, "uber", ext_id, etype, "duplicate", None, raw)
            return "duplicate", None
        detail = client.get_order(ext_id, href)
        parsed = parse_uber_order(detail)
        parsed["external_id"] = parsed["external_id"] or ext_id
        try:
            res = orders.place_external_order(conn, "uber", parsed["external_id"], parsed["lines"],
                                              parsed["label"], parsed["note"])
        except orders.KitchenPaused as e:
            client.deny(ext_id, str(e))
            orders.record_external_event(conn, "uber", ext_id, etype, "denied", str(e), json.dumps(detail, ensure_ascii=False)[:20000])
            return "denied", str(e)
        client.accept(ext_id)
        note = ("未對應品項：" + "、".join(res["unmatched"])) if res["unmatched"] else None
        orders.record_external_event(conn, "uber", ext_id, etype, "ok", note, json.dumps(detail, ensure_ascii=False)[:20000])
        return "ok", note
    except Exception as e:   # 任何錯誤都要留下紀錄並讓外場看得到，不能默默吞掉
        orders.record_external_event(conn, "uber", ext_id, etype, "failed", f"{type(e).__name__}: {e}", raw)
        return "failed", str(e)


def notify_ready(conn, client, order_id):
    """廚房完成出餐後通知 Uber（只對 Uber 訂單）。失敗只記錄，不影響店內作業。回傳 True/False/None（非 Uber 單）。"""
    source, ext_id = orders.order_source(conn, order_id)
    if source != "uber":
        return None
    try:
        client.mark_ready(ext_id)
        orders.record_external_event(conn, "uber", ext_id, "ready", "ok")
        return True
    except Exception as e:
        orders.record_external_event(conn, "uber", ext_id, "ready", "failed", f"{type(e).__name__}: {e}")
        return False
