"""HTTP 路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from . import services
from .database import get_session
from .schemas import (
    BatchPointsIn,
    BatchPointsOut,
    CrossingsOut,
    ZoneCreate,
    ZoneOut,
)

router = APIRouter(prefix="/api/v1")


@router.post("/zones", response_model=ZoneOut, status_code=status.HTTP_201_CREATED)
def register_zone(
    payload: ZoneCreate, response: Response, session: Session = Depends(get_session)
) -> ZoneOut:
    zone_out, created = services.create_zone(session, payload)
    if not created:
        response.status_code = status.HTTP_200_OK
        response.headers["X-Idempotent-Replay"] = "true"
    return zone_out


@router.get("/zones", response_model=list[ZoneOut])
def list_zones(session: Session = Depends(get_session)) -> list[ZoneOut]:
    return services.list_zones(session)


@router.get("/zones/{zone_id}", response_model=ZoneOut)
def get_zone(zone_id: str, session: Session = Depends(get_session)) -> ZoneOut:
    return services.get_zone(session, zone_id)


@router.post("/points:batch", response_model=BatchPointsOut)
def submit_points(
    payload: BatchPointsIn, session: Session = Depends(get_session)
) -> BatchPointsOut:
    accepted, duplicates, pids = services.submit_points(session, payload)
    return BatchPointsOut(accepted=accepted, duplicates=duplicates, point_ids=pids)


@router.get("/vehicles/{vehicle_id}/crossings", response_model=CrossingsOut)
def vehicle_crossings(
    vehicle_id: str,
    zone_id: str | None = None,
    session: Session = Depends(get_session),
) -> CrossingsOut:
    return services.compute_crossings(session, vehicle_id, zone_id)
