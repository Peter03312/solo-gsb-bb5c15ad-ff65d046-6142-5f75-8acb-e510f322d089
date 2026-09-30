"""业务服务层：危险区登记、轨迹点批量写入、穿越审计计算。"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import geometry
from .config import MERGE_EPSILON
from .database import PointRow, ZoneRow
from .schemas import (
    BatchPointsIn,
    CrossingsOut,
    CrossingInterval,
    ZoneCreate,
    ZoneCrossing,
    ZoneOut,
)
from .time_utils import parse_utc, to_z

_ZERO_EPS = 1e-9  # 小于 1 纳秒的区间视为零时长接触，不计穿越


class ConflictError(Exception):
    """409：重传内容冲突 / 同车次同刻多点。"""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class NotFoundError(Exception):
    """404。"""


# ----------------------------- 危险区 -----------------------------


def _row_to_out(row: ZoneRow) -> ZoneOut:
    data = json.loads(row.geometry_json)
    return ZoneOut(
        zone_id=row.zone_id,
        name=row.name,
        polygon={"shell": data["shell"], "holes": data["holes"]},
        effective_from=row.period_start,
        effective_to=row.period_end,
    )


def create_zone(session: Session, payload: ZoneCreate) -> tuple[ZoneOut, bool]:
    """登记危险区。同 ID 同内容重传幂等返回；同 ID 异内容 409。"""
    start = parse_utc(payload.effective_from, field="effective_from")
    end = parse_utc(payload.effective_to, field="effective_to")
    if not start < end:
        raise ValueError("生效时段必须满足 effective_from < effective_to（左闭右开）")

    polygon = geometry.build_polygon(
        [(float(x), float(y)) for x, y in payload.polygon.shell],
        [[(float(x), float(y)) for x, y in h] for h in payload.polygon.holes],
    )
    zone_id = payload.zone_id or uuid.uuid4().hex
    geom_json = geometry.polygon_to_json(polygon)

    existing = session.get(ZoneRow, zone_id)
    if existing is not None:
        same = (
            existing.geometry_json == geom_json
            and existing.period_start == to_z(start)
            and existing.period_end == to_z(end)
            and existing.name == payload.name
        )
        if same:
            return _row_to_out(existing), False
        raise ConflictError("zone_id_conflict", f"危险区 {zone_id} 已存在且内容不同")

    row = ZoneRow(
        zone_id=zone_id,
        name=payload.name,
        geometry_json=geom_json,
        period_start=to_z(start),
        period_end=to_z(end),
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError as exc:  # 并发下被抢先插入
        session.rollback()
        existing = session.get(ZoneRow, zone_id)
        if existing is not None and existing.geometry_json == geom_json:
            return _row_to_out(existing), False
        raise ConflictError("zone_id_conflict", f"危险区 {zone_id} 已存在且内容不同") from exc
    return _row_to_out(row), True


def list_zones(session: Session) -> list[ZoneOut]:
    rows = session.scalars(select(ZoneRow).order_by(ZoneRow.zone_id)).all()
    return [_row_to_out(r) for r in rows]


def get_zone(session: Session, zone_id: str) -> ZoneOut:
    row = session.get(ZoneRow, zone_id)
    if row is None:
        raise NotFoundError(f"危险区不存在: {zone_id}")
    return _row_to_out(row)


# ----------------------------- 轨迹点 -----------------------------


def _content_hash(vehicle_id: str, ts: str, x: float, y: float) -> str:
    raw = f"{vehicle_id}|{ts}|{x!r}|{y!r}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def submit_points(session: Session, payload: BatchPointsIn) -> tuple[int, int, list[str]]:
    """批量写入轨迹点。

    - 同 (point_id) 同内容：幂等跳过；异内容：整批拒绝（409）。
    - 同车次同刻不同点 / 批内重复点 ID 冲突：整批拒绝。
    - 任何错误都不写入部分数据（单事务回滚）。
    返回 (新写入数, 幂等重复数, 涉及点 ID 列表)。
    """
    # ---- 批内预检：先解析时间，再查重；全部通过才写库 ----
    parsed: list[tuple[str, str, datetime, float, float]] = []
    for p in payload.points:
        ts = parse_utc(p.timestamp, field="timestamp")
        parsed.append((p.point_id, p.vehicle_id, ts, float(p.x), float(p.y)))

    # 批内：同 point_id 必须内容一致
    by_pid: dict[str, tuple[str, str, datetime, float, float]] = {}
    for item in parsed:
        pid, vehicle_id, ts, x, y = item
        if pid in by_pid:
            if by_pid[pid] != item:
                raise ConflictError(
                    "point_id_conflict",
                    f"批次内点 ID {pid} 出现多次但内容不一致",
                )
        else:
            by_pid[pid] = item

    # 批内：同车次同刻只能有一个点
    seen_vt: dict[tuple[str, str], str] = {}
    for pid, vehicle_id, ts, _x, _y in parsed:
        key = (vehicle_id, to_z(ts))
        if key in seen_vt and seen_vt[key] != pid:
            raise ConflictError(
                "same_vehicle_same_timestamp",
                f"车次 {vehicle_id} 在 {to_z(ts)} 存在不同点 ID（{seen_vt[key]} 与 {pid}）",
            )
        seen_vt[key] = pid

    unique_items = list(by_pid.values())
    pids = [item[0] for item in unique_items]

    existing_rows = {
        r.point_id: r
        for r in session.scalars(select(PointRow).where(PointRow.point_id.in_(pids))).all()
    }

    to_insert: list[PointRow] = []
    duplicates = 0
    for pid, vehicle_id, ts, x, y in unique_items:
        ts_iso = to_z(ts)
        row = existing_rows.get(pid)
        if row is not None:
            same = (
                row.vehicle_id == vehicle_id
                and row.ts_iso == ts_iso
                and row.x == x
                and row.y == y
            )
            if not same:
                raise ConflictError(
                    "point_id_conflict",
                    f"点 ID {pid} 已存在但重传内容不同",
                )
            duplicates += 1
            continue
        to_insert.append(
            PointRow(
                point_id=pid,
                vehicle_id=vehicle_id,
                ts_iso=ts_iso,
                x=x,
                y=y,
                content_hash=_content_hash(vehicle_id, ts_iso, x, y),
            )
        )

    # 与库中已有数据校验“同车次同刻不同点”。
    if unique_items:
        vehicles = {item[1] for item in unique_items}
        vrows = session.scalars(
            select(PointRow).where(PointRow.vehicle_id.in_(vehicles))
        ).all()
        vt_index: dict[tuple[str, str], str] = {(r.vehicle_id, r.ts_iso): r.point_id for r in vrows}
        incoming_pids = set(pids)
        for pid, vehicle_id, ts, _x, _y in unique_items:
            key = (vehicle_id, to_z(ts))
            other = vt_index.get(key)
            if other is not None and other != pid and other not in incoming_pids:
                raise ConflictError(
                    "same_vehicle_same_timestamp",
                    f"车次 {vehicle_id} 在 {to_z(ts)} 已存在不同点 ID {other}（本次 {pid}）",
                )

    try:
        session.add_all(to_insert)
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ConflictError("concurrent_conflict", "并发提交冲突，请重试")

    return len(to_insert), duplicates, pids


# ----------------------------- 穿越审计 -----------------------------


def compute_crossings(
    session: Session, vehicle_id: str, zone_id: str | None = None
) -> CrossingsOut:
    zone_rows = session.scalars(select(ZoneRow).order_by(ZoneRow.zone_id)).all()
    if zone_id is not None:
        zone_rows = [r for r in zone_rows if r.zone_id == zone_id]
        if not zone_rows:
            raise NotFoundError(f"危险区不存在: {zone_id}")

    point_rows = session.scalars(
        select(PointRow)
        .where(PointRow.vehicle_id == vehicle_id)
        .order_by(PointRow.ts_iso, PointRow.point_id)
    ).all()
    # 入库已保证同车次时刻唯一；排序后相邻点之间线性移动（乱序上传在此重建）。
    track = [(parse_utc(r.ts_iso), r.x, r.y) for r in point_rows]

    zone_results: list[ZoneCrossing] = []
    for zrow in zone_rows:
        zgeom = geometry.ZoneGeometry(geometry.polygon_from_json(zrow.geometry_json))
        zstart = parse_utc(zrow.period_start)
        zend = parse_utc(zrow.period_end)

        raw: list[tuple[float, float]] = []  # 以 UNIX 秒表示的覆盖区间
        for (t0, x0, y0), (t1, x1, y1) in zip(track, track[1:]):
            span = (t1 - t0).total_seconds()
            if span <= 0:
                continue
            for a, b in zgeom.covered_intervals(x0, y0, x1, y1):
                seg_start = t0.timestamp() + a * span
                seg_end = t0.timestamp() + b * span
                lo = max(seg_start, zstart.timestamp())
                hi = min(seg_end, zend.timestamp())
                if hi - lo > _ZERO_EPS:  # 零时长接触不算穿越
                    raw.append((lo, hi))

        merged = geometry.merge_intervals(raw, epsilon=MERGE_EPSILON)
        intervals = [
            CrossingInterval(
                start=to_z(datetime.fromtimestamp(a, tz=timezone.utc)),
                end=to_z(datetime.fromtimestamp(b, tz=timezone.utc)),
            )
            for a, b in merged
        ]
        if intervals:
            total = round(sum(b - a for a, b in merged), 6)
            zone_results.append(
                ZoneCrossing(
                    zone_id=zrow.zone_id, total_seconds=total, intervals=intervals
                )
            )

    return CrossingsOut(vehicle_id=vehicle_id, zones=zone_results)
