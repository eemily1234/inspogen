"""忘記 PIN 時，在跑系統的那台電腦上重設。

    python reset_pin.py            列出所有帳號
    python reset_pin.py 老闆        替「老闆」設定新的 PIN（會請你輸入兩次，畫面不顯示）

會同時解除該帳號「輸錯太多次」的鎖定。資料庫位置與系統相同（環境變數 OPS_DB，預設為目前資料夾的 ops.db）。
"""
import getpass
import os
import sys

from ops import db, services as s

ROLE = {"owner": "老闆", "staff": "店員"}


def main(argv):
    if not os.path.exists(db.DB_PATH):
        print(f"找不到資料庫：{os.path.abspath(db.DB_PATH)}")
        print("請先 cd 到系統的資料夾（例如 cd ~/inspogen）再執行。")
        return 1
    conn = db.connect()
    users = s.list_users(conn)
    if len(argv) < 2:
        print("目前的帳號：")
        for u in users:
            print(f"  {u['name']}（{'廚房' if u['kitchen'] else ROLE.get(u['role'], u['role'])}）")
        print("\n重設方式：python reset_pin.py 帳號名稱")
        return 0
    name = argv[1]
    pin = getpass.getpass(f"「{name}」的新 PIN（至少 {s.MIN_PIN_LENGTH} 位數字）：")
    if getpass.getpass("再輸入一次：") != pin:
        print("兩次輸入不一樣，沒有變更。")
        return 1
    try:
        s.reset_pin(conn, name, pin)
    except s.OpsError as e:
        print(f"沒有變更：{e}")
        return 1
    print(f"已重設「{name}」的 PIN，可以用新的 PIN 登入了。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
