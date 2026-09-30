"""FastAPI 应用入口：uvicorn app.main:app。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from . import services
from .database import init_db
from .geometry import GeometryError


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 启动即建表初始化；SQLite 文件挂卷，重启后数据仍可查询。
    init_db()
    yield


app = FastAPI(
    title="工地作业车危险区穿越审计服务",
    version="1.0.0",
    description=(
        "登记带洞平面米制多边形危险区及其 UTC 左闭右开生效时段，"
        "批量接收轨迹点并审计作业车对危险区的穿越区间与累计秒数。"
    ),
    lifespan=lifespan,
)


@app.exception_handler(services.ConflictError)
def _conflict_handler(_request: Request, exc: services.ConflictError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": str(exc), "code": exc.code},
    )


@app.exception_handler(services.NotFoundError)
def _not_found_handler(_request: Request, exc: services.NotFoundError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": str(exc)},
    )


@app.exception_handler(GeometryError)
def _geometry_handler(_request: Request, exc: GeometryError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": str(exc), "code": "invalid_geometry"},
    )


@app.exception_handler(ValueError)
def _value_handler(_request: Request, exc: ValueError) -> JSONResponse:
    # 时段/时刻等业务校验错误（GeometryError 已被更具体的处理器先行处理）。
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": str(exc)},
    )


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


from .api import router as _api_router  # noqa: E402

app.include_router(_api_router, tags=["api"])
