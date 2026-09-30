"""SQLAlchemy 引擎、会话与建表初始化（SQLite，文件持久化）。"""
from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import String, create_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)

from .config import DATABASE_URL

_connect_args: dict[str, object] = {}
if DATABASE_URL.startswith("sqlite"):
    # check_same_thread=False：FastAPI 线程池中跨线程使用会话；文件库依然串行落盘。
    _connect_args = {"check_same_thread": False}

    # 文件库时确保父目录存在（初始化由这里完成，容器卷挂载后可直接启动）。
    if DATABASE_URL not in ("sqlite://", "sqlite:///:memory:"):
        prefix = "sqlite:///"
        path = DATABASE_URL[len(prefix):]
        if path:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)

engine = create_engine(DATABASE_URL, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class ZoneRow(Base):
    __tablename__ = "zones"

    zone_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    # 规范化后的几何：{"shell": [[x,y],...], "holes": [[[x,y],...],...]}
    geometry_json: Mapped[str] = mapped_column(String, nullable=False)
    period_start: Mapped[str] = mapped_column(String(40), nullable=False)  # ISO Z
    period_end: Mapped[str] = mapped_column(String(40), nullable=False)    # ISO Z


class PointRow(Base):
    __tablename__ = "points"

    # point_id 全局唯一；幂等/冲突均以 (vehicle_id, point_id) 维度判定。
    point_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    vehicle_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    ts_iso: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    x: Mapped[float]
    y: Mapped[float]
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


def init_db() -> None:
    """建表（IF NOT EXISTS 语义），容器启动与本地启动都会调用。"""
    Base.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    """FastAPI 依赖：每请求一个会话，异常回滚。"""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
