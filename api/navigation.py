import time
import uuid
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from core.config import AMAP_KEY

router = APIRouter()

# 核心设计：全局路线缓存。
# 键为 routeId，值为该路线的所有分步导航指令列表，供下一个 /navigation/instruction 接口查询
_route_cache = {}


# --- 1. 根据对接文档定义 Pydantic 入参模型 ---
class LocationIn(BaseModel):
    latitude: float
    longitude: float
    speed: float
    accuracy: float


class RouteRequest(BaseModel):
    provider: str
    destination: str
    currentLocation: LocationIn


import math


# ============================================================
# 指令合成辅助：把高德 step 拼成"你在哪 + 怎么走 + 下一步"的完整情境句
# 盲人用户需要的是情境，而不是高德原始的"步行65米向右前方转"这类裸指令
# ============================================================
def _step_road(step: dict) -> str:
    return (step.get("road") or "").strip()


def compose_where(cur_road: str, origin_desc: str = "") -> str:
    """你现在在哪：优先用当前路段名，其次用规划起点的逆地编码描述。"""
    if cur_road:
        return f"你现在在{cur_road}"
    if origin_desc:
        return f"你现在在{origin_desc}附近"
    return "你现在在当前路段"


def compose_move(steps: list, idx: int, distance_to_turn: float, dest_name: str) -> str:
    """怎么走：直行多少米 + 转向动作 + 进入下一条路；最后一步则播到达。"""
    dist_m = int(round(distance_to_turn))
    if idx >= len(steps) - 1:
        return f"前方{dist_m}米到达目的地{dest_name}"
    action = (steps[idx].get("action") or "").strip() or "继续前行"
    next_road = _step_road(steps[idx + 1])
    if next_road:
        return f"沿当前方向直行，还有{dist_m}米后{action}进入{next_road}"
    return f"沿当前方向直行，还有{dist_m}米后{action}"


def compose_instruction(steps: list, idx: int, distance_to_turn: float,
                        dest_name: str, origin_desc: str = "") -> str:
    """完整情境句 = 你在哪 + 怎么走。"""
    where = compose_where(_step_road(steps[idx]), origin_desc)
    move = compose_move(steps, idx, distance_to_turn, dest_name)
    return f"{where}，{move}"


async def reverse_geocode(client: httpx.AsyncClient, lng: float, lat: float) -> str:
    """逆地理编码取可读起点描述；失败降级为空串，绝不阻断主流程。"""
    try:
        url = f"https://restapi.amap.com/v3/geocode/regeo?location={lng},{lat}&key={AMAP_KEY}"
        r = await client.get(url, timeout=5.0)
        data = r.json()
        if data.get("status") == "1" and data.get("regeocode"):
            addr = data["regeocode"].get("formatted_address")
            # 高德无结果时 formatted_address 可能是 [] 或 ""
            if isinstance(addr, str) and addr:
                return addr
    except Exception as e:
        print(f"[route] regeo 失败(降级空串): {e}")
    return ""


@router.post("/navigation/route")
async def plan_route(payload: RouteRequest):
    """
    作用: 根据目的地和当前位置，规划导航路线
    输入: 符合对接文档规范的 JSON
    输出: {"routeId": "...", "firstInstruction": "..."}
    """
    t_start = time.time()
    print(f"[route] start ts={t_start:.3f} dest=\"{payload.destination}\" origin=({payload.currentLocation.latitude:.5f},{payload.currentLocation.longitude:.5f})")
    # 按照对接文档，若 provider 为 none 或不支持，直接拦截
    if payload.provider != "amap":
        raise HTTPException(status_code=400, detail="目前后端仅支持 amap (高德地图) 服务提供商")

    origin_lat = payload.currentLocation.latitude
    origin_lng = payload.currentLocation.longitude

    async with httpx.AsyncClient() as client:
        # ==========================================
        # 步骤 1：地理编码 —— 将目的地名称转为经纬度
        # ==========================================
        geo_url = f"https://restapi.amap.com/v3/geocode/geo?address={payload.destination}&key={AMAP_KEY}"
        try:
            t_geo_start = time.time()
            geo_response = await client.get(geo_url, timeout=5.0)
            geo_data = geo_response.json()
            print(f"[route] geocode done +{(time.time() - t_start)*1000:.0f}ms (call_only={(time.time() - t_geo_start)*1000:.0f}ms)")

            if geo_data.get("status") != "1" or not geo_data.get("geocodes"):
                print(f"[route] !!! 地理编码失败 dest=\"{payload.destination}\" data={geo_data}")
                raise HTTPException(status_code=400, detail=f"无法解析目的地名称: {payload.destination}")

            # 高德返回的坐标格式为 "经度,纬度" 字符串
            dest_location = geo_data["geocodes"][0]["location"]
            dest_lng, dest_lat = dest_location.split(",")
        except Exception as e:
            if isinstance(e, HTTPException): raise e
            raise HTTPException(status_code=500, detail=f"高德地理编码接口异常: {str(e)}")

        # ==========================================
        # 步骤 2：路径规划 —— 发起步行路径规划请求
        # ==========================================
        # 高德要求 origin 和 destination 格式均为 "经度,纬度"
        direction_url = (
            f"https://restapi.amap.com/v3/direction/walking"
            f"?origin={origin_lng},{origin_lat}"
            f"&destination={dest_lng},{dest_lat}"
            f"&key={AMAP_KEY}"
        )

        try:
            t_dir_start = time.time()
            dir_response = await client.get(direction_url, timeout=5.0)
            dir_data = dir_response.json()
            print(f"[route] direction done +{(time.time() - t_start)*1000:.0f}ms (call_only={(time.time() - t_dir_start)*1000:.0f}ms)")

            if dir_data.get("status") != "1" or "route" not in dir_data:
                print(f"[route] !!! 路径规划失败 data={dir_data}")
                raise HTTPException(status_code=400, detail="高德地图路线规划失败，两点间可能无法步行到达")

            # 提取导航线路段
            paths = dir_data["route"]["paths"]
            if not paths:
                raise HTTPException(status_code=400, detail="未找到可行走路线")

            # 获取高德返回的完整分步指令列表 (steps) 与整体里程/耗时
            steps = paths[0]["steps"]
            total_distance = float(paths[0].get("distance", 0) or 0)
            total_duration = float(paths[0].get("duration", 0) or 0)

            # 起点可读描述：逆地理编码（失败降级空串，不阻断主流程）
            origin_desc = await reverse_geocode(client, origin_lng, origin_lat)

            # ==========================================
            # 步骤 3：生成本地 routeId 并缓存后续完整路径
            # ==========================================
            route_id = f"route_{uuid.uuid4().hex[:12]}"

            # 缓存整条路线的具体步骤，以便接下来的 /navigation/instruction 接口轮询使用
            _route_cache[route_id] = {
                "destination": payload.destination,
                "dest_lng": float(dest_lng),
                "dest_lat": float(dest_lat),
                "steps": steps,            # 每一步的 instruction / road / action / polyline
                "origin_desc": origin_desc,
                "total_distance": total_distance,
                "total_duration": total_duration,
            }

            # 合成完整开场句：你在哪 + 路线概览 + 第一步怎么走
            minutes = max(1, round(total_duration / 60)) if total_duration else 0
            where = f"你现在在{origin_desc}" if origin_desc else "你现在在当前位置"
            if total_distance:
                route_summary = (
                    f"{where}，目的地{payload.destination}，"
                    f"全程约{int(round(total_distance))}米、预计{minutes}分钟。"
                )
            else:
                route_summary = f"{where}，目的地{payload.destination}。"
            first_step_dist = float(steps[0].get("distance", 0) or 0) if steps else 0
            first_move = compose_move(steps, 0, first_step_dist, payload.destination) if steps else "开始导航"
            first_instruction = route_summary + first_move

            # 序列化步骤数据，包含 polyline 供前端地图绘制
            serialized_steps = []
            for s in steps:
                serialized_steps.append({
                    "instruction": (s.get("instruction") or "").strip(),
                    "polyline": (s.get("polyline") or "").strip(),
                    "road": (s.get("road") or "").strip() or None,
                    "action": (s.get("action") or "").strip() or None,
                    "distance": float(s.get("distance", 0) or 0),
                    "duration": float(s.get("duration", 0) or 0),
                })

            print(f"[route] DONE total={(time.time() - t_start)*1000:.0f}ms route_id={route_id} steps={len(steps)} origin=\"{origin_desc[:20]}\" first=\"{first_instruction[:50]}\"")
            return {
                "routeId": route_id,
                "firstInstruction": first_instruction,
                "routeSummary": route_summary,
                "originDescription": origin_desc,
                "totalDistance": int(round(total_distance)),
                "totalDuration": int(round(total_duration)),
                "origin": {
                    "latitude": origin_lat,
                    "longitude": origin_lng,
                    "description": origin_desc,
                },
                "destination": {
                    "latitude": float(dest_lat),
                    "longitude": float(dest_lng),
                    "name": payload.destination,
                },
                "steps": serialized_steps,
            }

        except Exception as e:
            if isinstance(e, HTTPException): raise e
            print(f"[route] !!! 异常 after {(time.time() - t_start)*1000:.0f}ms: {e}")
            raise HTTPException(status_code=500, detail=f"高德路径规划接口异常: {str(e)}")

def calculate_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    使用 Haversine 公式计算两点间距离
    """
    rad_lat1 = math.radians(lat1)
    rad_lat2 = math.radians(lat2)
    a = rad_lat1 - rad_lat2
    b = math.radians(lng1) - math.radians(lng2)

    s = 2 * math.asin(math.sqrt(
        math.pow(math.sin(a / 2), 2) +
        math.cos(rad_lat1) * math.cos(rad_lat2) * math.pow(math.sin(b / 2), 2)
    ))
    # 地球半径为 6378137 米
    return s * 6378137


# --- 2. 补充对接文档要求的入参模型 ---
class InstructionRequest(BaseModel):
    provider: str
    routeId: str
    location: LocationIn


@router.post("/navigation/instruction")
async def get_navigation_instruction(payload: InstructionRequest):
    """
    作用: 根据当前位置，获取下一个转向指令和距离
    输入: 符合对接文档规范的 JSON
    输出: {"instruction": "...", "distanceToTurn": ...}
    """
    t_start = time.time()
    route_id = payload.routeId
    print(f"[instr] start ts={t_start:.3f} route_id={route_id} loc=({payload.location.latitude:.5f},{payload.location.longitude:.5f})")

    # 1. 校验路由缓存是否存在
    if route_id not in _route_cache:
        print(f"[instr] !!! 路由缓存未命中 route_id={route_id}")
        raise HTTPException(status_code=404, detail="未找到对应路线ID或导航已过期，请重新规划路线")

    route_data = _route_cache[route_id]
    steps = route_data["steps"]

    user_lat = payload.location.latitude
    user_lng = payload.location.longitude

    # 2. 动态匹配算法：寻找离用户当前位置最近的导航步骤 (Step)
    # 高德返回的 step 中含有 polyline，格式如 "117.0567,36.6712;117.0569,36.6715"
    closest_step_index = 0
    min_distance = float('inf')

    for i, step in enumerate(steps):
        # 抓取当前步骤的起点坐标进行距离比对
        first_coord = step["polyline"].split(";")[0]
        step_lng, step_lat = map(float, first_coord.split(","))

        dist = calculate_distance(user_lat, user_lng, step_lat, step_lng)
        if dist < min_distance:
            min_distance = dist
            closest_step_index = i

    # 3. 计算当前距离转向点（即当前步骤终点）的剩余距离
    current_step = steps[closest_step_index]
    last_coord = current_step["polyline"].split(";")[-1]
    next_lng, next_lat = map(float, last_coord.split(","))

    distance_to_turn = calculate_distance(user_lat, user_lng, next_lat, next_lng)

    # 特殊情况处理：如果是最后一步，转向点距离直接等同于到终点的距离
    if closest_step_index == len(steps) - 1:
        distance_to_turn = calculate_distance(user_lat, user_lng, route_data["dest_lat"], route_data["dest_lng"])

    # 4. 合成"你在哪 + 怎么走 + 下一步"的完整情境句
    result_dist = round(float(distance_to_turn), 1)
    origin_desc = route_data.get("origin_desc", "")
    dest_name = route_data["destination"]
    result_instr = compose_instruction(steps, closest_step_index, result_dist, dest_name, origin_desc)

    cur_road = _step_road(current_step)
    is_last = closest_step_index >= len(steps) - 1
    maneuver = "到达目的地" if is_last else ((current_step.get("action") or "").strip() or "继续前行")
    next_road = "" if is_last else _step_road(steps[closest_step_index + 1])

    print(f"[instr] DONE total={(time.time() - t_start)*1000:.0f}ms step={closest_step_index}/{len(steps)} dist_to_turn={result_dist}m instr=\"{result_instr[:50]}\"")
    return {
        "instruction": result_instr,
        "distanceToTurn": result_dist,
        "currentRoad": cur_road,
        "maneuver": maneuver,
        "nextRoad": next_road,
        "currentStepIndex": closest_step_index,
        "totalSteps": len(steps),
    }