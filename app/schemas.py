"""HTTP 请求/响应模型（Pydantic v2）。"""
from __future__ import annotations

import math
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

# 平面米制坐标，单位米。
Coord = tuple[float, float]
Ring = list[Annotated[tuple[float, float], "至少 3 个不同顶点"]]


def _check_finite_ring(ring: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if len(ring) < 3:
        raise ValueError("环至少需要 3 个点")
    for x, y in ring:
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError("坐标必须为有限数值")
    if len(set(ring)) < 3:
        raise ValueError("环至少需要 3 个不同顶点")
    return ring


class PolygonIn(BaseModel):
    """带洞多边形：shell 为外边界（顶点无需显式闭合），holes 为洞列表。"""

    shell: list[tuple[float, float]]
    holes: list[list[tuple[float, float]]] = Field(default_factory=list)

    @field_validator("shell")
    @classmethod
    def _shell(cls, v: list[tuple[float, float]]) -> list[tuple[float, float]]:
        return _check_finite_ring(v)

    @field_validator("holes")
    @classmethod
    def _holes(cls, v: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
        for hole in v:
            _check_finite_ring(hole)
        return v


class ZoneCreate(BaseModel):
    zone_id: str | None = Field(default=None, max_length=64)
    name: str = Field(default="", max_length=256)
    polygon: PolygonIn
    # UTC 左闭右开生效时段：[effective_from, effective_to)
    effective_from: str = Field(description="ISO 8601 UTC 时刻，如 2024-01-01T00:00:00Z")
    effective_to: str = Field(description="ISO 8601 UTC 时刻（不含）")


class ZoneOut(BaseModel):
    zone_id: str
    name: str
    polygon: PolygonIn
    effective_from: str
    effective_to: str


class TrackPointIn(BaseModel):
    point_id: str = Field(min_length=1, max_length=128)
    vehicle_id: str = Field(min_length=1, max_length=128)
    timestamp: str = Field(description="ISO 8601 UTC 时刻")
    x: float
    y: float

    @field_validator("x", "y")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("坐标必须为有限数值")
        return v


class BatchPointsIn(BaseModel):
    points: list[TrackPointIn] = Field(min_length=1)


class BatchPointsOut(BaseModel):
    accepted: int
    duplicates: int
    point_ids: list[str]


class CrossingInterval(BaseModel):
    # 左闭右开 [start, end)
    start: str
    end: str


class ZoneCrossing(BaseModel):
    zone_id: str
    total_seconds: float
    intervals: list[CrossingInterval]


class CrossingsOut(BaseModel):
    vehicle_id: str
    zones: list[ZoneCrossing]
