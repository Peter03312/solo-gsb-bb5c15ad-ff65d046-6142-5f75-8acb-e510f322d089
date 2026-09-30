FROM python:3.11-slim

# Shapely 2.x wheel 已内建 GEOS，无需 apt 安装 GEOS；保留 curl 便于容器内健康检查。
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# 持久化数据目录（compose 中挂载为卷）
RUN mkdir -p /data
ENV DZ_DB_PATH=/data/danger_zones.db

EXPOSE 8000

# 容器启动即初始化建表（应用 lifespan 内 create_all）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
