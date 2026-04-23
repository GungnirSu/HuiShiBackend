# HuiVision 后端：导航模块完整设计稿（V1）

> 目标：在现有 FastAPI 后端基础上，完整构建“实时导航模块”。
>
> 约束：导航模块与障碍物功能并行运行，但导航模块不负责障碍物提醒业务。
>
> 适用阶段：第一版可用（MVP）到可联调版本。

---

## 1. 背景与边界

### 1.1 当前后端现状（已完成）

- 已有 `main.py` FastAPI 入口。
- 已有视觉识别接口 `/v1/vision/analyze`。
- 已有 SQLite 与 `VisionLog` 表。
- 尚无完整导航模块、导航状态管理、WebSocket 导航推送。

### 1.2 本模块职责（必须做）

导航模块负责：

1. 目的地搜索/解析（文本 -> 坐标）
2. 定位按钮所需的当前位置反查（坐标 -> 文本地址）
3. 路线规划（调用高德 Web API）
4. 导航会话管理（开始/停止/状态）
5. 实时导航引导（随位置更新）
6. 偏航检测与提示
7. 到达检测与结束
8. WebSocket 实时状态推送

### 1.3 本模块不做（明确排除）

- 障碍物识别
- 障碍物提醒文案
- 风险等级判断
- 语音合成引擎（可留接口）

---

## 2. 技术方案总览

采用：**模块解耦 + 状态共享 + WebSocket 实时同步**。

### 2.1 逻辑架构

```text
小程序导航页
  ├─ HTTP: 目的地搜索/开始导航/上传位置/停止导航
  └─ WS: 接收实时导航状态

FastAPI 后端
  ├─ routers/navigation.py      # 导航路由
  ├─ services/amap_service.py   # 高德接口封装
  ├─ services/navigation_service.py # 导航业务核心
  ├─ services/ws_manager.py     # WebSocket 连接管理
  ├─ state/navigation_store.py  # 内存状态中心
  ├─ schemas/navigation.py      # 请求/响应模型
  └─ workers/navigation_worker.py # 可选：后台周期任务
```

### 2.2 数据流（核心链路）

1. 用户输入目的地 -> `search_destination`
2. 用户点击定位 -> `reverse_geocode`
3. 开始导航 -> `start_navigation`（创建 session + 规划路线）
4. 前端每 2~3 秒上报当前位置 -> `update_location`
5. 后端计算导航步骤/偏航/到达 -> 更新状态
6. 后端通过 WebSocket 推送最新导航状态

---

## 3. 目录结构（建议直接落地）

```text
PythonProject/
├── main.py
├── core/
│   └── config.py
├── docs/
│   └── navigation-module-design.md
├── routers/
│   └── navigation.py
├── schemas/
│   └── navigation.py
├── services/
│   ├── amap_service.py
│   ├── navigation_service.py
│   └── ws_manager.py
├── state/
│   └── navigation_store.py
├── utils/
│   └── geo.py
└── workers/
    └── navigation_worker.py  # V1 可先空实现
```

---

## 4. 配置设计

## 4.1 `.env` 变量（新增）

- `AMAP_WEB_KEY=xxx`
- `NAV_UPDATE_INTERVAL_SEC=2`
- `NAV_OFFROUTE_THRESHOLD_M=25`
- `NAV_ARRIVE_THRESHOLD_M=12`
- `NAV_CITY_DEFAULT=北京`（可选）

### 4.2 `core/config.py` 扩展

在现有 `DASHSCOPE_API_KEY` 基础上，增加：

- 高德 key
- 导航阈值配置
- 默认城市

> 原则：所有可调策略放配置，不写死在业务代码。

---

## 5. 数据模型设计（Pydantic + 内存状态）

V1 导航建议先用内存状态管理（单机足够），后续再落库。

### 5.1 请求模型

1. `DestinationSearchRequest`
   - `keyword: str`
   - `city: Optional[str]`

2. `ReverseGeocodeRequest`
   - `lat: float`
   - `lng: float`

3. `StartNavigationRequest`
   - `destination_keyword: Optional[str]`
   - `destination_lat: Optional[float]`
   - `destination_lng: Optional[float]`
   - `origin_lat: float`
   - `origin_lng: float`
   - `mode: str = "walk"`

4. `UpdateLocationRequest`
   - `session_id: str`
   - `lat: float`
   - `lng: float`
   - `heading: Optional[float]`
   - `speed: Optional[float]`

5. `StopNavigationRequest`
   - `session_id: str`

### 5.2 响应模型

1. `ApiResponse`
   - `code: int`
   - `message: str`
   - `data: Any`
   - `timestamp: int`

2. `NavigationState`
   - `session_id: str`
   - `is_navigating: bool`
   - `destination_name: str`
   - `destination_lat: float`
   - `destination_lng: float`
   - `current_lat: float`
   - `current_lng: float`
   - `distance_to_destination_m: float`
   - `duration_to_destination_s: int`
   - `current_instruction: str`
   - `is_off_route: bool`
   - `arrived: bool`
   - `updated_at: int`

3. `RouteStep`
   - `index: int`
   - `instruction: str`
   - `distance_m: int`
   - `polyline: str`

4. `RouteSummary`
   - `distance_m: int`
   - `duration_s: int`
   - `steps: List[RouteStep]`

---

## 6. API 设计（V1 必做）

统一前缀：`/api/navigation`

### 6.1 目的地搜索

- `POST /search`
- 功能：按关键词搜索 POI，返回候选地点。
- 用途：输入框联想/搜索结果页。

### 6.2 当前位置反查

- `POST /reverse-geocode`
- 功能：将定位按钮获得的经纬度转成人类可读地址。
- 用途：点击定位按钮后播报/显示“你当前位于…”

### 6.3 开始导航

- `POST /start`
- 功能：创建会话 + 调用路线规划 + 初始化状态。

### 6.4 上传位置

- `POST /location`
- 功能：更新实时位置，生成新引导语、偏航状态、到达状态。

### 6.5 查询当前状态

- `GET /status?session_id=xxx`
- 功能：页面重进/重连时同步最新状态。

### 6.6 停止导航

- `POST /stop`
- 功能：结束会话，推送停止状态。

### 6.7 WebSocket

- `WS /ws/navigation?session_id=xxx`
- 功能：实时推送导航状态。

---

## 7. 高德 API 封装设计

> 说明：你已决定不使用高德 MCP Server。这里使用高德 Web 服务 API。

在 `services/amap_service.py` 封装以下方法：

1. `search_poi(keyword, city)`
   - 对应关键词搜索

2. `geocode(address, city)`
   - 地址 -> 坐标

3. `reverse_geocode(lat, lng)`
   - 坐标 -> 地址

4. `route_walk(origin, destination)`
   - 步行路线规划

### 7.1 入参坐标格式

高德常用 `lng,lat` 字符串格式。内部建议统一对象结构：

```text
{"lat": 39.90, "lng": 116.39}
```

到调用层再转成高德格式，减少混淆。

### 7.2 错误处理规则

高德响应异常时：

- 业务层返回统一错误码（如 `4001` 地图服务失败）
- message 说明可读
- 打印后端日志用于排查

---

## 8. 导航核心逻辑设计（navigation_service）

### 8.1 `start_navigation`

流程：

1. 解析目的地（关键词 -> 坐标）
2. 调高德步行路线
3. 创建 `session_id`
4. 写入状态中心
5. 生成首条 `current_instruction`
6. 推送 `navigation_started` 事件

### 8.2 `update_location`

流程：

1. 根据 `session_id` 读取会话状态
2. 更新当前位置
3. 计算到终点距离
4. 判断是否偏航
5. 判断是否到达
6. 更新 `current_instruction`
7. 推送 `navigation_update` 事件

### 8.3 偏航判断（V1 简化版）

- 用“当前点到最近路线点距离 > 阈值”判定偏航。
- 阈值来自配置 `NAV_OFFROUTE_THRESHOLD_M`。

> V1 不做自动重算路线，只给出“已偏航，请调整方向”提示。

### 8.4 到达判断

- 当前点到终点距离 < `NAV_ARRIVE_THRESHOLD_M` 判定到达。
- 到达后：
  - `arrived = true`
  - `is_navigating = false`
  - 推送 `navigation_arrived`

---

## 9. WebSocket 设计

### 9.1 连接管理

`ws_manager.py` 维护：

- `session_id -> [WebSocket connections]`
- 连接、断开、广播能力

### 9.2 事件类型

统一字段：`event`, `payload`, `timestamp`

事件枚举：

- `navigation_started`
- `navigation_update`
- `navigation_offroute`
- `navigation_arrived`
- `navigation_stopped`
- `navigation_error`

### 9.3 推送节流建议

- 位置更新每 2~3 秒一次
- 若指令未变化，可降低推送频率
- 避免 UI 高频抖动

---

## 10. 与前端的联调协议（简版）

### 10.1 搜索框联想

前端在输入后调用 `/search`，展示候选 POI 列表，用户选择目标。

### 10.2 定位按钮

前端获取经纬度 -> 调 `/reverse-geocode` -> 播报/展示“当前位于xxx”。

### 10.3 导航进行中

- 前端调用 `/start` 获取 `session_id`
- 前端建立 `WS /ws/navigation`
- 前端每 2~3 秒调 `/location`
- UI 按 WS 事件更新导航文案

---

## 11. 开发实施步骤（你按这个顺序做）

### 阶段 A：搭骨架

1. 新建 `routers/schemas/services/state/utils` 目录
2. 增加配置项（高德 key + 阈值）
3. `main.py` 引入导航路由

### 阶段 B：高德能力打通

1. 实现 `amap_service.search_poi`
2. 实现 `amap_service.reverse_geocode`
3. 实现 `amap_service.route_walk`
4. 用 Postman 测通

### 阶段 C：导航会话与状态

1. 实现 `navigation_store`
2. 实现 `start / location / status / stop`
3. 实现偏航/到达判断

### 阶段 D：WebSocket 实时化

1. 实现 `ws_manager`
2. 实现 `/ws/navigation`
3. 在 `start/location/stop` 中触发推送

### 阶段 E：联调与验收

1. 小程序输入目的地、定位、开始导航
2. 位置更新后可看到引导变化
3. 偏航和到达状态正确

---

## 12. 验收标准（Done Definition）

满足以下即算导航模块完成 V1：

1. 可按关键词搜索并选定目的地。
2. 定位按钮可返回当前位置文字描述。
3. 可创建导航会话并获取路线摘要。
4. 连续上报位置时，导航引导可实时变化。
5. 偏航时有明确提示。
6. 到达时自动结束并通知前端。
7. WebSocket 能持续推送导航状态。
8. 导航模块不依赖障碍物模块即可独立运行。

---

## 13. 风险与规避

1. **坐标系问题（WGS84 vs GCJ02）**
   - 统一在接入层转换，内部统一格式。

2. **高德 Key 错配/域名白名单问题**
   - 提前配置并写入环境说明。

3. **前端定位频率过高导致压力**
   - 控制上报间隔 2~3 秒。

4. **WebSocket 断连**
   - 前端自动重连 + 断连后补拉 `/status`。

5. **状态丢失（仅内存）**
   - V1 可接受；V2 可接 Redis/DB 持久化。

---

## 14. V2 可扩展项（先不做）

- 偏航自动重算路线
- 多路线策略（最短时间/最少转弯）
- 语音播报队列与去重
- 导航历史轨迹回放
- Redis 状态中心
- 多用户并发会话隔离增强

---

## 15. 对你提供的 PDF 的可参考结论

你的 PDF（uniapp + 高德）对本项目**有帮助但仅限前端地图集成层**，主要可参考：

1. 高德 Key 申请与微信小程序类型区分
2. 小程序合法域名配置
3. 定位、逆地理编码、地图组件基础用法
4. 常见坑（地图不显示、坐标偏移、标记过多）

不建议直接照搬的部分：

- 仅展示地图的页面代码
- 未涉及“后端会话 + 实时状态 + WebSocket”的实现思路

结论：**可部分参考（配置与前端交互），但导航核心仍应按本设计稿的后端架构执行。**

---

## 16. 下一步执行清单（你确认后我按此带你改）

1. 先改 `core/config.py`（新增导航配置）
2. 新增 `schemas/navigation.py`（请求响应模型）
3. 新增 `services/amap_service.py`（高德封装）
4. 新增 `state/navigation_store.py`（会话状态）
5. 新增 `services/navigation_service.py`（核心逻辑）
6. 新增 `services/ws_manager.py`（WS 管理）
7. 新增 `routers/navigation.py`（路由）
8. 调整 `main.py` 挂载导航路由 + WS
9. 本地联调（HTTP + WS）

> 你回复“按步骤 1 开始”，我就先只带你做第 1 步，并解释每一处修改原因。