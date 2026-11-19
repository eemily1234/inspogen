"""SQLite 每日備份：用 SQLite 內建的 backup API（資料庫正在使用時也能得到一致的備份），保留最近 N 天。

單次備份：  python deploy/backup.py --once
常駐每日備份（docker compose 的 backup 服務使用）：  python deploy/backup.py
環境變數：OPS_DB（資料庫路徑）、BACKUP_DIR（預設 /backups）、BACKUP_KEEP_DAYS（預設 30）、BACKUP_HOUR（預設 3，台灣時間請搭配 TZ）
"""
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path


def backup(db_path, backup_dir, keep_days=30, today=None):
    """備份到 backup_dir/ops-YYYY-MM-DD.db，並刪除超過 keep_days 天的舊備份。回傳備份檔路徑。"""
    today = today or date.today()
    out_dir = Path(backup_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"ops-{today.isoformat()}.db"
    tmp = target.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    check = sqlite3.connect(tmp)
    try:
        ok = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if ok != "ok":
        tmp.unlink()
        raise RuntimeError(f"備份檔檢查失敗：{ok}")
    os.replace(tmp, target)
    cutoff = today - timedelta(days=keep_days)
    for f in out_dir.glob("ops-*.db"):
        try:
            day = date.fromisoformat(f.stem[len("ops-"):])
        except ValueError:
            continue
        if day < cutoff:
            f.unlink()
    return target


def _seconds_until(hour, now=None):
    now = now or datetime.now()
    nxt = now.replace(hour=hour, minute=30, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return (nxt - now).total_seconds()


def main():
    db_path = os.environ.get("OPS_DB", "ops.db")
    out = os.environ.get("BACKUP_DIR", "/backups")
    keep = int(os.environ.get("BACKUP_KEEP_DAYS", "30"))
    hour = int(os.environ.get("BACKUP_HOUR", "3"))
    if "--once" in sys.argv:
        print("已備份：", backup(db_path, out, keep))
        return
    while True:
        try:
            print(datetime.now().isoformat(timespec="seconds"), "已備份：", backup(db_path, out, keep), flush=True)
        except Exception as e:   # 備份失敗要看得到，但不能讓常駐程式掛掉
            print("備份失敗：", e, flush=True)
        time.sleep(_seconds_until(hour))


if __name__ == "__main__":
    main()
