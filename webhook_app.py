"""單一入口的 webhook 伺服器：Line 記帳 + Uber Eats。啟動：uvicorn webhook_app:app --port 8000

  /line/webhook   ← Line Messaging API（見 docs/line-setup.md）
  /uber/webhook   ← Uber Eats（見 docs/uber-setup.md）
  /health         ← 健康檢查
"""
from fastapi import FastAPI

import line_server
import uber_server

app = FastAPI(title="營運系統 Webhooks")
app.mount("/line", line_server.app)
app.mount("/uber", uber_server.app)


@app.get("/health")
def health():
    return {"ok": True}
