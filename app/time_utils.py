"""UTC 时刻工具：全部以 tz-aware datetime 参与计算，序列化为 ...Z。"""
from __future__ import annotations

from datetime import datetime, timezone


def parse_utc(value: str, *, field: str = "timestamp") -> datetime:
    """解析 ISO 8601 时刻；必须显式带 UTC 时区（Z 或数值偏移），拒绝朴素时间。"""
    text = value
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} 不是合法的 ISO 8601 时刻: {value!r}") from exc
    if dt.tzinfo is None:
        raise ValueError(f"{field} 必须带 UTC 时区标识（Z 或 +00:00）: {value!r}")
    return dt.astimezone(timezone.utc)


def to_z(dt: datetime) -> str:
    """格式化为 UTC 的 ...Z（微秒为 0 时省略小数部分）。"""
    if dt.tzinfo is None:
        raise ValueError("内部错误：序列化的时刻缺少时区")
    dt = dt.astimezone(timezone.utc)
    if dt.microsecond:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
