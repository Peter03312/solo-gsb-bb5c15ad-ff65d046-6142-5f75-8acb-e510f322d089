"""pytest 全局夹具：每个测试使用独立临时 SQLite 文件库。"""
from __future__ import annotations

import os
import tempfile

import pytest

# 在导入 app 之前指定测试数据库路径（config 在导入时读取环境变量）。
_TMP_DB_DIR = tempfile.mkdtemp(prefix="dz-test-")
os.environ["DZ_DB_PATH"] = os.path.join(_TMP_DB_DIR, "test.db")
os.environ["DZ_MERGE_EPSILON"] = "1e-9"
os.environ["DZ_GEOM_EPSILON"] = "1e-7"

from fastapi.testclient import TestClient  # noqa: E402

from app import database  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    database.init_db()
    # 清空全部数据，保证用例独立
    with database.SessionLocal() as s:
        s.query(database.PointRow).delete()
        s.query(database.ZoneRow).delete()
        s.commit()
    with TestClient(app) as c:
        yield c
