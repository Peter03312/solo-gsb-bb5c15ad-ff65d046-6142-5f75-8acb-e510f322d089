"""端到端 API 测试，覆盖全部审计规则。"""
from __future__ import annotations

from app import database
from app.main import app

SQUARE = {
    "shell": [[0, 0], [100, 0], [100, 100], [0, 100]],
    "holes": [],
}
DAY = ("2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z")


def _zone(client, zone_id, polygon=SQUARE, start=DAY[0], end=DAY[1], name="z"):
    return client.post(
        "/api/v1/zones",
        json={
            "zone_id": zone_id,
            "name": name,
            "polygon": polygon,
            "effective_from": start,
            "effective_to": end,
        },
    )


def _batch(client, points):
    return client.post("/api/v1/points:batch", json={"points": points})


def _pt(pid, vid, ts, x, y):
    return {"point_id": pid, "vehicle_id": vid, "timestamp": ts, "x": x, "y": y}


def _cross(client, vid, zone_id=None):
    url = f"/api/v1/vehicles/{vid}/crossings"
    if zone_id:
        url += f"?zone_id={zone_id}"
    return client.get(url)


# ------------------------- 危险区登记与校验 -------------------------


def test_register_and_get_zone(client):
    r = _zone(client, "z1")
    assert r.status_code == 201
    body = r.json()
    assert body["zone_id"] == "z1"
    assert client.get("/api/v1/zones/z1").status_code == 200


def test_self_intersecting_polygon_rejected(client):
    # 蝴蝶结：(0,0)->(10,10)->(0,10)->(10,0) 自交
    bow = {"shell": [[0, 0], [10, 10], [0, 10], [10, 0]], "holes": []}
    r = _zone(client, "bad", polygon=bow)
    assert r.status_code == 422
    assert "自交" in r.json()["detail"] or "合法" in r.json()["detail"]


def test_hole_outside_rejected(client):
    # 洞中心在外边界之外
    poly = {"shell": SQUARE["shell"], "holes": [[[200, 200], [210, 200], [210, 210]]]}
    r = _zone(client, "bad", polygon=poly)
    assert r.status_code == 422
    assert "越出" in r.json()["detail"]


def test_hole_touching_outer_boundary_rejected(client):
    # 洞边与外边界共线相接
    poly = {"shell": SQUARE["shell"], "holes": [[[0, 40], [20, 40], [20, 60], [0, 60]]]}
    r = _zone(client, "bad", polygon=poly)
    assert r.status_code == 422


def test_overlapping_holes_rejected(client):
    h1 = [[20, 20], [60, 20], [60, 60], [20, 60]]
    h2 = [[40, 40], [80, 40], [80, 80], [40, 80]]
    r = _zone(client, "bad", polygon={"shell": SQUARE["shell"], "holes": [h1, h2]})
    assert r.status_code == 422


def test_invalid_period_rejected(client):
    r = _zone(client, "bad", start=DAY[1], end=DAY[0])
    assert r.status_code == 422
    r = _zone(client, "bad2", start="2024-01-01T00:00:00", end=DAY[1])
    assert r.status_code == 422  # 朴素时间戳拒绝


def test_non_finite_coords_rejected(client):
    raw = (
        '{"polygon": {"shell": [[0, 0], [100, 0], [100, NaN], [0, 100]], "holes": []},'
        ' "zone_id": "bad", "effective_from": "%s", "effective_to": "%s"}' % DAY
    )
    r = client.post("/api/v1/zones", content=raw, headers={"content-type": "application/json"})
    assert r.status_code == 422
    import json as _json

    raw_pts = _json.dumps(
        {"points": [_pt("p1", "v1", DAY[0], float("inf"), 1.0)]}, allow_nan=True
    )
    r = client.post(
        "/api/v1/points:batch",
        content=raw_pts,
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422


def test_zone_idempotent_and_conflict(client):
    assert _zone(client, "z1").status_code == 201
    same = _zone(client, "z1")
    assert same.status_code == 200
    assert same.headers.get("x-idempotent-replay") == "true"
    # 异内容（不同生效时段）冲突
    conflict = _zone(client, "z1", start="2024-02-01T00:00:00Z", end="2024-02-02T00:00:00Z")
    assert conflict.status_code == 409


# ------------------------- 轨迹点写入规则 -------------------------


def test_point_idempotent_replay(client):
    p = _pt("p1", "v1", "2024-01-01T00:00:00Z", -10, 50)
    assert _batch(client, [p]).json() == {
        "accepted": 1,
        "duplicates": 0,
        "point_ids": ["p1"],
    }
    r = _batch(client, [p])
    assert r.status_code == 200
    assert r.json()["accepted"] == 0 and r.json()["duplicates"] == 1


def test_same_point_id_different_content_conflicts_and_no_partial_write(client):
    _batch(client, [_pt("p1", "v1", "2024-01-01T00:00:00Z", -10, 50)])
    # p1 改坐标 -> 冲突；同批 p2 不得被部分写入
    r = _batch(
        client,
        [
            _pt("p1", "v1", "2024-01-01T00:00:00Z", 999, 999),
            _pt("p2", "v1", "2024-01-01T00:00:05Z", -9, 50),
        ],
    )
    assert r.status_code == 409
    with database.SessionLocal() as s:
        assert s.get(database.PointRow, "p2") is None


def test_same_vehicle_same_timestamp_conflict(client):
    _batch(client, [_pt("p1", "v1", "2024-01-01T00:00:00Z", -10, 50)])
    r = _batch(client, [_pt("p9", "v1", "2024-01-01T00:00:00Z", -5, 50)])
    assert r.status_code == 409
    # 批内同车次同刻不同点
    r = _batch(
        client,
        [
            _pt("a", "v2", "2024-01-01T00:00:00Z", 0, 0),
            _pt("b", "v2", "2024-01-01T00:00:00Z", 1, 1),
        ],
    )
    assert r.status_code == 409
    with database.SessionLocal() as s:
        assert s.get(database.PointRow, "a") is None
        assert s.get(database.PointRow, "b") is None


# ------------------------- 穿越计算 -------------------------


def test_basic_crossing_entire_day(client):
    _zone(client, "z1")
    # 1 秒 1 米：t=0 x=-10；t=120 x=110。进入 [10,110]，即 [00:00:10,00:01:50)
    points = [
        _pt("p0", "v1", "2024-01-01T00:00:00Z", -10, 50),
        _pt("p1", "v1", "2024-01-01T00:02:00Z", 110, 50),
    ]
    _batch(client, points)
    r = _cross(client, "v1", "z1")
    assert r.status_code == 200
    zone = r.json()["zones"][0]
    assert zone["total_seconds"] == 100.0
    assert zone["intervals"] == [
        {"start": "2024-01-01T00:00:10Z", "end": "2024-01-01T00:01:50Z"}
    ]


def test_period_clipping_left_closed_right_open(client):
    # 区域仅在 [00:00:30, 00:01:30) 生效；穿越覆盖 [00:00:10,00:01:50)
    _zone(client, "z1", start="2024-01-01T00:00:30Z", end="2024-01-01T00:01:30Z")
    _batch(
        client,
        [
            _pt("p0", "v1", "2024-01-01T00:00:00Z", -10, 50),
            _pt("p1", "v1", "2024-01-01T00:02:00Z", 110, 50),
        ],
    )
    zone = _cross(client, "v1", "z1").json()["zones"][0]
    assert zone["total_seconds"] == 60.0
    assert zone["intervals"] == [
        {"start": "2024-01-01T00:00:30Z", "end": "2024-01-01T00:01:30Z"}
    ]


def test_outer_boundary_counts(client):
    _zone(client, "z1")
    # 沿外边界 y=0 行走 10 秒、100 米：外边界计入
    _batch(
        client,
        [
            _pt("p0", "v1", "2024-01-01T00:00:00Z", 0, 0),
            _pt("p1", "v1", "2024-01-01T00:00:10Z", 100, 0),
        ],
    )
    zone = _cross(client, "v1", "z1").json()["zones"][0]
    assert zone["total_seconds"] == 10.0


def test_hole_interior_and_boundary_excluded(client):
    poly = {"shell": SQUARE["shell"], "holes": [[[40, 40], [60, 40], [60, 60], [40, 60]]]}
    _zone(client, "z1", polygon=poly)
    # x: 0 -> 100, 1m/s；洞内段 x∈(40,60) 与洞边界都不计
    _batch(
        client,
        [
            _pt("p0", "v1", "2024-01-01T00:00:00Z", 0, 50),
            _pt("p1", "v1", "2024-01-01T00:01:40Z", 100, 50),
        ],
    )
    zone = _cross(client, "v1", "z1").json()["zones"][0]
    assert zone["total_seconds"] == 80.0
    assert zone["intervals"] == [
        {"start": "2024-01-01T00:00:00Z", "end": "2024-01-01T00:00:40Z"},
        {"start": "2024-01-01T00:01:00Z", "end": "2024-01-01T00:01:40Z"},
    ]


def test_hole_boundary_travel_excluded(client):
    poly = {"shell": SQUARE["shell"], "holes": [[[40, 40], [60, 40], [60, 60], [40, 60]]]}
    _zone(client, "z1", polygon=poly)
    # 全程贴洞边界行走，不计穿越
    _batch(
        client,
        [
            _pt("p0", "v1", "2024-01-01T00:00:00Z", 40, 40),
            _pt("p1", "v1", "2024-01-01T00:00:10Z", 60, 40),
        ],
    )
    assert _cross(client, "v1", "z1").json()["zones"] == []


def test_zero_duration_touch_not_crossing(client):
    _zone(client, "z1")
    # 直线恰好擦过角点 (0,0)：t=10 接触一瞬，其余在外
    _batch(
        client,
        [
            _pt("p0", "v1", "2024-01-01T00:00:00Z", -100, -100),
            _pt("p1", "v1", "2024-01-01T00:00:20Z", 100, -100),
        ],
    )
    assert _cross(client, "v1", "z1").json()["zones"] == []


def test_adjacent_segments_merge(client):
    _zone(client, "z1")
    # 三个相接段都在区域内（点在内部，相接无间隙）-> 合并为一个区间
    _batch(
        client,
        [
            _pt("p0", "v1", "2024-01-01T00:00:00Z", 10, 50),
            _pt("p1", "v1", "2024-01-01T00:00:05Z", 20, 50),
            _pt("p2", "v1", "2024-01-01T00:00:10Z", 30, 50),
        ],
    )
    zone = _cross(client, "v1", "z1").json()["zones"][0]
    assert zone["total_seconds"] == 10.0
    assert len(zone["intervals"]) == 1


def test_out_of_order_upload_reconstructed(client):
    _zone(client, "z1")
    # 乱序上传（中间点最后到），结果仍按时刻重建
    _batch(client, [_pt("p2", "v1", "2024-01-01T00:02:00Z", 110, 50)])
    _batch(client, [_pt("p0", "v1", "2024-01-01T00:00:00Z", -10, 50)])
    _batch(client, [_pt("p1", "v1", "2024-01-01T00:01:00Z", 50, 50)])
    zone = _cross(client, "v1", "z1").json()["zones"][0]
    assert zone["total_seconds"] == 100.0


def test_persistence_survives_engine_restart(client):
    _zone(client, "z1")
    _batch(
        client,
        [
            _pt("p0", "v1", "2024-01-01T00:00:00Z", -10, 50),
            _pt("p1", "v1", "2024-01-01T00:02:00Z", 110, 50),
        ],
    )
    # 关闭连接池全部连接（模拟重启后重新打开同一文件库），记录仍可查询
    database.engine.dispose()
    from fastapi.testclient import TestClient

    with TestClient(app) as c2:
        r = c2.get("/api/v1/vehicles/v1/crossings?zone_id=z1")
        assert r.json()["zones"][0]["total_seconds"] == 100.0
