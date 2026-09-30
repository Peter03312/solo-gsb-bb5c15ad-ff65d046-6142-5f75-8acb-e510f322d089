"""运行期配置，全部来自环境变量，带默认值。"""
from __future__ import annotations

import os


def _database_url() -> str:
    # 同时兼容 DATABASE_URL 与 DZ_DATABASE_URL；默认使用挂载卷中的 SQLite 文件，
    # 重启后数据仍可查询。
    url = os.getenv("DATABASE_URL") or os.getenv("DZ_DATABASE_URL")
    if url:
        return url
    path = os.getenv("DZ_DB_PATH", "/data/danger_zones.db")
    if path == ":memory:":
        return "sqlite://"
    return f"sqlite:///{path}"


DATABASE_URL: str = _database_url()

# 同危险区相接区间合并时，判定“相接”允许的最大间隙（秒）。
MERGE_EPSILON: float = float(os.getenv("DZ_MERGE_EPSILON", "1e-9"))

# 几何判定上的距离容差（米），用于边界浮点抖动场景下把点判定到环上。
GEOM_EPSILON: float = float(os.getenv("DZ_GEOM_EPSILON", "1e-7"))
