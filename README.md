# 工地作业车危险区穿越审计服务

用 Python（FastAPI + SQLAlchemy + Shapely + SQLite）构建的 HTTP 服务，用于：

1. **登记危险区**：平面米制坐标的多边形，**允许洞**；配套一个 UTC **左闭右开**生效时段 `[effective_from, effective_to)`。
2. **批量提交轨迹点**：车次（vehicle_id）、点 ID（point_id）、UTC 时刻、平面坐标（米）。
3. **穿越审计**：相邻轨迹点之间按时间与坐标**线性插值**移动，只在“轨迹覆盖 **且** 区域生效”的时间段计算穿越，按危险区返回穿越起止区间列表与累计秒数。

## 语义规则

- **边界规则**：外边界**计入**危险区；洞的内部不计入，**洞及其边界也不计入**。
- **左闭右开**：危险区生效时段、每个穿越区间均为 `[start, end)`。
- **零时长接触不算穿越**：轨迹仅在一个瞬时点接触区域（如擦角点）不计。
- **区间合并**：同一危险区的相接区间（相邻轨迹段连续覆盖，间隙 ≤ 1ns）合并为一个区间。
- **乱序上传**：轨迹点可分多批、任意顺序提交；审计时按车次、时刻排序重建轨迹。
- **幂等与冲突**：
  - 同一 `point_id` 以**完全相同内容**（车次/时刻/坐标）重传 → 幂等跳过（响应 `duplicates` 计数）；
  - 同一 `point_id` 内容不同 → `409 point_id_conflict`；
  - 同一车次同一时刻出现不同点 → `409 same_vehicle_same_timestamp`；
- **明确拒绝（HTTP 422，整批不写入任何部分数据）**：
  - 自交多边形、面积为 0 的环；
  - 洞越出外边界、洞与外边界相接、洞之间相交/相接；
  - 非有限坐标（`NaN`/`Infinity`）、朴素（不带时区）时刻、非法时段（`from >= to`）；
  - 空批次。
- **持久化**：数据落在 SQLite 文件（Docker 卷）中，服务/容器重启后记录仍可查询。

## 目录结构

```
app/
  config.py       # 环境变量配置
  database.py     # SQLite 引擎、表模型、建表初始化
  schemas.py      # 请求/响应模型与入参校验
  time_utils.py   # UTC 时刻解析/格式化
  geometry.py     # 带洞多边形校验、点归属、线段覆盖区间
  services.py     # 业务逻辑（登记、批量写入、穿越计算）
  api.py          # HTTP 路由
  main.py         # 应用入口（启动建表、异常处理）
tests/            # pytest 端到端测试（20 个用例）
Dockerfile
docker-compose.yml
```

## 一键启动（Docker Compose）

需要本机具备 Docker 与 Docker Compose 插件：

```bash
docker compose up --build -d
```

服务启动时自动完成依赖安装与数据库初始化（建表）。停止与查看日志：

```bash
docker compose logs -f
docker compose down            # 停止；数据保留在卷 danger-zone-data 中
docker compose down -v         # 同时清除数据卷
```

## 本地启动（不用 Docker）

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export DZ_DB_PATH=./data/danger_zones.db          # 可选，默认 /data/danger_zones.db
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

交互式文档（Swagger UI）：<http://127.0.0.1:8000/docs>

## 配置（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DZ_DB_PATH` | `/data/danger_zones.db` | SQLite 数据库文件路径；重启后数据保留 |
| `DATABASE_URL` | （未设置时用 `DZ_DB_PATH` 拼装） | 完整 SQLAlchemy 连接串，优先级高于 `DZ_DB_PATH` |
| `DZ_MERGE_EPSILON` | `1e-9` | 同区相接区间合并的最大间隙（秒） |
| `DZ_GEOM_EPSILON` | `1e-7` | 边界浮点判定容差（米） |

## 地址与端口

- 默认监听：`0.0.0.0:8000`，Compose 映射到宿主机 `127.0.0.1:8000`（`docker-compose.yml` 的 `ports` 可改）。
- 所有业务接口前缀：`/api/v1`。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/v1/zones` | 登记危险区（201 新建 / 200 幂等重传 / 409 同 ID 异内容） |
| `GET` | `/api/v1/zones` | 列出全部危险区 |
| `GET` | `/api/v1/zones/{zone_id}` | 查询单个危险区 |
| `POST` | `/api/v1/points:batch` | 批量提交轨迹点（整批原子） |
| `GET` | `/api/v1/vehicles/{vehicle_id}/crossings?zone_id=` | 查询某车次穿越审计（可按区域过滤） |
| `GET` | `/health` | 健康检查 |

## 调用示例

### 1. 登记危险区（外框 100m×100m，中央 20m×20m 洞，全天生效）

```bash
curl -X POST http://127.0.0.1:8000/api/v1/zones \
  -H 'Content-Type: application/json' \
  -d '{
    "zone_id": "yard-A",
    "name": "A区基坑",
    "polygon": {
      "shell": [[0,0],[100,0],[100,100],[0,100]],
      "holes": [[[40,40],[60,40],[60,60],[40,60]]]
    },
    "effective_from": "2024-01-01T00:00:00Z",
    "effective_to":   "2024-01-02T00:00:00Z"
  }'
```

- `shell`：外边界顶点，无需显式闭合；
- `holes`：洞列表（可为空）；顶点无需显式闭合；
- 时间必须是带 `Z`（或 `+00:00`）的 ISO 8601 UTC 时刻。

### 2. 批量提交轨迹点（可乱序、可分批）

```bash
curl -X POST http://127.0.0.1:8000/api/v1/points:batch \
  -H 'Content-Type: application/json' \
  -d '{"points": [
    {"point_id":"p1","vehicle_id":"truck-7","timestamp":"2024-01-01T00:00:00Z","x":0,"y":50},
    {"point_id":"p2","vehicle_id":"truck-7","timestamp":"2024-01-01T00:00:40Z","x":40,"y":50},
    {"point_id":"p3","vehicle_id":"truck-7","timestamp":"2024-01-01T00:01:40Z","x":100,"y":50},
    {"point_id":"p4","vehicle_id":"truck-7","timestamp":"2024-01-01T00:01:00Z","x":60,"y":50}
  ]}'
# {"accepted":4,"duplicates":0,"point_ids":["p1","p2","p3","p4"]}
```

用相同载荷再调用一次即为幂等重传：`{"accepted":0,"duplicates":4,...}`。

### 3. 查询穿越审计

```bash
curl "http://127.0.0.1:8000/api/v1/vehicles/truck-7/crossings?zone_id=yard-A"
```

```json
{
  "vehicle_id": "truck-7",
  "zones": [
    {
      "zone_id": "yard-A",
      "total_seconds": 80.0,
      "intervals": [
        {"start": "2024-01-01T00:00:00Z", "end": "2024-01-01T00:00:40Z"},
        {"start": "2024-01-01T00:01:00Z", "end": "2024-01-01T00:01:40Z"}
      ]
    }
  ]
}
```

说明：车辆以 1m/s 横穿，0–40s 在区域内，40–60s 位于洞内（含洞边界）不计，60–100s 回到区域内，共 80 秒，两个区间分别返回。不带 `zone_id` 时按所有危险区分别返回（无穿越的区域不出现）。

## 运行测试

```bash
pip install -r requirements-dev.txt
python3 -m pytest -q
```

覆盖：几何合法性拒绝、洞边界语义、幂等/冲突、同车同刻冲突、整批原子性、
生效时段裁剪（左闭右开）、外边界计入、零时长擦边、相接区间合并、乱序重建、重启持久化。
