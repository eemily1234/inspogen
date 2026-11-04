from datetime import date, datetime

import pytest

from deploy import backup as bk
from ops import db, services as s


def _db(tmp_path):
    path = str(tmp_path / "live.db")
    c = db.connect(path)                      # WAL 模式
    s.create_user(c, "老闆", s.OWNER, "1234")
    s.add_item(c, "咖哩飯", "meal", 150)
    return path, c


def test_backup_is_consistent_while_db_is_open_and_restorable(tmp_path):
    path, c = _db(tmp_path)
    out = bk.backup(path, tmp_path / "bk", today=date(2026, 11, 4))
    assert out.name == "ops-2026-11-04.db"
    c.execute("INSERT INTO item(name,type,category,price) VALUES ('紅茶','meal','meal',40)")   # 備份後才新增
    c.commit()
    restored = db.connect(str(out))
    assert [r["name"] for r in restored.execute("SELECT name FROM item ORDER BY id")] == ["咖哩飯"]
    assert s.authenticate(restored, "老闆", "1234")                                              # 備份可直接當資料庫使用


def test_old_backups_are_pruned_but_unrelated_files_kept(tmp_path):
    path, _ = _db(tmp_path)
    d = tmp_path / "bk"
    d.mkdir()
    for name in ("ops-2026-09-01.db", "ops-2026-10-10.db", "ops-2026-10-31.db", "notes.txt", "ops-bad.db"):
        (d / name).write_bytes(b"x")
    bk.backup(path, d, keep_days=30, today=date(2026, 11, 4))
    left = sorted(p.name for p in d.iterdir())
    assert left == ["notes.txt", "ops-2026-10-10.db", "ops-2026-10-31.db", "ops-2026-11-04.db", "ops-bad.db"]


def test_backup_twice_same_day_overwrites_and_leaves_no_temp_files(tmp_path):
    path, _ = _db(tmp_path)
    bk.backup(path, tmp_path / "bk", today=date(2026, 11, 4))
    bk.backup(path, tmp_path / "bk", today=date(2026, 11, 4))
    assert [p.name for p in (tmp_path / "bk").iterdir()] == ["ops-2026-11-04.db"]


def test_seconds_until_next_run():
    assert bk._seconds_until(3, datetime(2026, 11, 4, 2, 30, 0)) == 3600       # 今天 03:30 還沒到
    assert bk._seconds_until(3, datetime(2026, 11, 4, 3, 30, 0)) == 24 * 3600  # 剛好過了 → 明天
    assert bk._seconds_until(3, datetime(2026, 11, 4, 23, 30, 0)) == 4 * 3600
