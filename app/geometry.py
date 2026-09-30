"""带洞平面多边形的校验与穿越覆盖计算。

约定（平面米制坐标）：
- 外边界及其内部计入危险区；
- 洞的内部不计入，洞的边界也不计入；
- 自交多边形、洞越界（含洞与外边界相接）一律拒绝。
"""
from __future__ import annotations

import json
import math
from collections.abc import Iterable

from shapely.geometry import (
    GeometryCollection,
    LinearRing,
    LineString,
    MultiLineString,
    MultiPoint,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry

from .config import GEOM_EPSILON

RingCoords = list[tuple[float, float]]
_PARAM_EPS = 1e-10  # 交点参数去抖


class GeometryError(ValueError):
    """危险区几何不合法。"""


def _normalize_ring(coords: RingCoords) -> RingCoords:
    """去掉首尾重复的闭合点；LinearRing 会自行闭合。"""
    ring = [(float(x), float(y)) for x, y in coords]
    if len(ring) >= 2 and ring[0] == ring[-1]:
        ring = ring[:-1]
    if len(ring) < 3:
        raise GeometryError("环至少需要 3 个不同顶点")
    return ring


def _build_ring(coords: RingCoords, label: str) -> LinearRing:
    ring = _normalize_ring(coords)
    lr = LinearRing(ring)
    if not lr.is_simple:
        raise GeometryError(f"{label}存在自交（相邻顶点重合或边互相穿越）")
    if Polygon(ring).area <= 0.0:
        raise GeometryError(f"{label}围成的面积为 0（退化为线或点）")
    return lr


def build_polygon(shell: RingCoords, holes: list[RingCoords]) -> Polygon:
    """校验并构建带洞多边形；任何不合法情形都抛 GeometryError。"""
    shell_ring = _build_ring(shell, "外边界")
    shell_poly = Polygon(shell_ring)
    if not shell_poly.is_valid:
        raise GeometryError(f"外边界不是合法简单多边形: {shell_poly.is_valid_reason()}")

    hole_rings: list[LinearRing] = []
    for i, hole in enumerate(holes):
        label = f"洞 #{i + 1}"
        hole_ring = _build_ring(hole, label)
        hole_poly = Polygon(hole_ring)
        if not hole_poly.is_valid:
            raise GeometryError(f"{label}不是合法简单多边形")
        # 洞必须完整落在外边界内部，边界相接也算越界（会产生零宽通道）。
        if not shell_poly.contains_properly(hole_poly):
            raise GeometryError(f"{label}越出外边界或与外边界相接")
        hole_rings.append(hole_ring)

    # 洞之间互不相交（相接同样会导致退化）。
    for i in range(len(hole_rings)):
        for j in range(i + 1, len(hole_rings)):
            if not hole_rings[i].disjoint(hole_rings[j]):
                raise GeometryError(f"洞 #{i + 1} 与洞 #{j + 1} 相交或相接")

    polygon = Polygon(shell_ring, hole_rings)
    if not polygon.is_valid:
        raise GeometryError(f"带洞多边形不合法: {polygon.is_valid_reason()}")
    return polygon


def polygon_to_json(polygon: Polygon) -> str:
    data = {
        "shell": list(polygon.exterior.coords[:-1]),
        "holes": [list(ring.coords[:-1]) for ring in polygon.interiors],
    }
    return json.dumps(data, separators=(",", ":"))


def polygon_from_json(payload: str) -> Polygon:
    data = json.loads(payload)
    return Polygon(
        _normalize_ring(data["shell"]),
        [_normalize_ring(h) for h in data["holes"]],
    )


class ZoneGeometry:
    """危险区几何及其归属/覆盖判定。"""

    def __init__(self, polygon: Polygon):
        self.polygon = polygon
        self.boundary = polygon.boundary
        self.exterior = polygon.exterior

    def contains(self, x: float, y: float) -> bool:
        """点是否计入危险区（外边界含，洞及其边界不含）。"""
        pt = Point(x, y)
        if not self.polygon.covers(pt):
            return False  # 外部或洞内
        # 在边界附近时区分外边界与洞边界（同时吸收浮点抖动）。
        if self.boundary.dwithin(pt, GEOM_EPSILON):
            return bool(self.exterior.dwithin(pt, GEOM_EPSILON))
        return True

    def covered_intervals(
        self, x0: float, y0: float, x1: float, y1: float
    ) -> list[tuple[float, float]]:
        """线段 (p0->p1) 被危险区覆盖的参数区间（参数 t∈[0,1]，左闭右开）。"""
        dx, dy = x1 - x0, y1 - y0
        length2 = dx * dx + dy * dy
        if length2 == 0.0:
            return [(0.0, 1.0)] if self.contains(x0, y0) else []

        line = LineString([(x0, y0), (x1, y1)])
        cuts = {0.0, 1.0}

        def add_coord(cx: float, cy: float) -> None:
            t = ((cx - x0) * dx + (cy - y0) * dy) / length2
            if -_PARAM_EPS < t < 0.0:
                t = 0.0
            elif 1.0 < t < 1.0 + _PARAM_EPS:
                t = 1.0
            if 0.0 <= t <= 1.0:
                cuts.add(t)

        inter = line.intersection(self.boundary)
        for geom in _iter_simple(inter):
            if geom.is_empty:
                continue
            if isinstance(geom, Point):
                add_coord(geom.x, geom.y)
            elif isinstance(geom, MultiPoint):
                for g in geom.geoms:
                    add_coord(g.x, g.y)
            elif isinstance(geom, LineString):
                # 线段与某条环边重合：取重合段端点作为切点。
                for cx, cy in geom.coords:
                    add_coord(cx, cy)
            elif isinstance(geom, MultiLineString):
                for g in geom.geoms:
                    for cx, cy in g.coords:
                        add_coord(cx, cy)
            else:  # pragma: no cover - 合法多边形与线段相交只会出现上述类型
                raise GeometryError(f"未预期的相交几何类型: {geom.geom_type}")

        params = sorted(cuts)
        # 合并数值上几乎相等的切点。
        merged: list[float] = []
        for t in params:
            if not merged or t - merged[-1] > _PARAM_EPS:
                merged.append(t)
            # 否则视为同一个切点，保留先出现者。

        intervals: list[tuple[float, float]] = []
        for a, b in zip(merged, merged[1:]):
            mid = line.interpolate((a + b) * 0.5, normalized=True)
            if self.contains(mid.x, mid.y):
                intervals.append((a, b))
        return intervals


def _iter_simple(geom: BaseGeometry) -> Iterable[BaseGeometry]:
    """把可能的集合几何拍平（Polygon/集合在本场景不应出现，做防御处理）。"""
    if isinstance(geom, GeometryCollection):
        for g in geom.geoms:
            yield from _iter_simple(g)
    else:
        yield geom


def merge_intervals(
    intervals: list[tuple[float, float]], epsilon: float
) -> list[tuple[float, float]]:
    """同危险区相接区间合并（间隙 <= epsilon 视为相接）。"""
    if not intervals:
        return []
    ordered = sorted(intervals)
    result: list[tuple[float, float]] = []
    cur_a, cur_b = ordered[0]
    for a, b in ordered[1:]:
        if a <= cur_b + epsilon:
            cur_b = max(cur_b, b)
        else:
            result.append((cur_a, cur_b))
            cur_a, cur_b = a, b
    result.append((cur_a, cur_b))
    return result
