import time
import uuid
from typing import Dict, List, Optional, Tuple

from core.config import NAV_ARRIVE_THRESHOLD_M, NAV_OFFROUTE_THRESHOLD_M
from schemas.navigation import NavigationSettings, NavigationState, RouteStep, RouteSummary
from services.amap_service import AmapService, AmapServiceError
from services.ws_manager import ws_manager
from state.navigation_store import NavigationSession, navigation_store
from utils.geo import haversine_m, nearest_point_distance_m


class NavigationService:
    def __init__(self, amap_service: Optional[AmapService] = None) -> None:
        self.amap = amap_service or AmapService()

    async def search_destination(self, keyword: str, city: Optional[str] = None):
        return await self.amap.search_poi(keyword=keyword, city=city)

    async def reverse_geocode(self, lat: float, lng: float):
        return await self.amap.reverse_geocode(lat=lat, lng=lng)

    async def start_navigation(
        self,
        origin_lat: float,
        origin_lng: float,
        destination_keyword: Optional[str] = None,
        destination_lat: Optional[float] = None,
        destination_lng: Optional[float] = None,
        mode: str = "walk",
        settings: Optional[NavigationSettings] = None,
    ) -> NavigationState:
        if destination_lat is None or destination_lng is None:
            if not destination_keyword:
                raise ValueError("必须提供目的地关键词或坐标")
            poi_list = await self.search_destination(destination_keyword, settings.default_city if settings else None)
            if not poi_list:
                raise ValueError("未找到目的地")
            first_poi = poi_list[0]
            location = first_poi.get("location", "")
            if not location:
                raise ValueError("目的地缺少坐标信息")
            lng_str, lat_str = location.split(",")
            destination_lat = float(lat_str)
            destination_lng = float(lng_str)
            destination_name = first_poi.get("name", destination_keyword)
        else:
            destination_name = destination_keyword or "目标地点"

        session_id = str(uuid.uuid4())
        session = navigation_store.create_session(
            session_id=session_id,
            origin_lat=origin_lat,
            origin_lng=origin_lng,
            destination_name=destination_name,
            destination_lat=destination_lat,
            destination_lng=destination_lng,
            mode=mode,
            settings=settings,
        )

        route_summary = await self._build_route_summary(origin_lat, origin_lng, destination_lat, destination_lng)
        session.route_summary = route_summary
        session.current_step_index = 0
        session.current_instruction = self._current_instruction(session, 0)
        navigation_store.update_session(session)

        state = self._build_state(session, distance_to_destination=route_summary.distance_m)
        await ws_manager.broadcast(session_id, self._event("navigation_started", state.model_dump()))
        return state

    async def update_location(
        self,
        session_id: str,
        lat: float,
        lng: float,
        heading: Optional[float] = None,
        speed: Optional[float] = None,
    ) -> NavigationState:
        session = navigation_store.get_session(session_id)
        if not session:
            raise ValueError("导航会话不存在")

        session.current_lat = lat
        session.current_lng = lng
        session.updated_at = int(time.time())

        distance_to_destination = haversine_m(lat, lng, session.destination_lat, session.destination_lng)
        arrive_threshold = session.settings.arrive_threshold_m or NAV_ARRIVE_THRESHOLD_M

        session.arrived = distance_to_destination <= arrive_threshold
        session.is_off_route = False

        if session.arrived:
            session.current_instruction = "已到达目的地"
            session.is_navigating = False
        else:
            session.is_off_route = self._detect_offroute(lat, lng, session)
            if session.is_off_route:
                session.current_instruction = "您已偏离路线，请调整方向"
            else:
                session.current_step_index = self._update_step_index(session, lat, lng)
                session.current_instruction = self._current_instruction(session, session.current_step_index)

        navigation_store.update_session(session)
        state = self._build_state(session, distance_to_destination=distance_to_destination)

        event_name = "navigation_update"
        if session.is_off_route:
            event_name = "navigation_offroute"
        if session.arrived:
            event_name = "navigation_arrived"
        await ws_manager.broadcast(session_id, self._event(event_name, state.model_dump()))
        return state

    async def stop_navigation(self, session_id: str) -> NavigationState:
        session = navigation_store.get_session(session_id)
        if not session:
            raise ValueError("导航会话不存在")
        session.is_navigating = False
        session.current_instruction = "导航已停止"
        navigation_store.update_session(session)
        state = self._build_state(session, distance_to_destination=0)
        await ws_manager.broadcast(session_id, self._event("navigation_stopped", state.model_dump()))
        return state

    def get_status(self, session_id: str) -> NavigationState:
        session = navigation_store.get_session(session_id)
        if not session:
            raise ValueError("导航会话不存在")
        distance = haversine_m(session.current_lat, session.current_lng, session.destination_lat, session.destination_lng)
        return self._build_state(session, distance_to_destination=distance)

    async def _build_route_summary(self, origin_lat: float, origin_lng: float, destination_lat: float, destination_lng: float) -> RouteSummary:
        try:
            route_data = await self.amap.route_walk((origin_lat, origin_lng), (destination_lat, destination_lng))
            path = (route_data.get("route", {}).get("paths") or [{}])[0]
            distance_m = int(float(path.get("distance", 0)))
            duration_s = int(float(path.get("duration", 0)))
            steps = []
            for idx, step in enumerate(path.get("steps", [])):
                steps.append(
                    RouteStep(
                        index=idx,
                        instruction=step.get("instruction", ""),
                        distance_m=int(float(step.get("distance", 0))),
                        polyline=step.get("polyline"),
                    )
                )
            return RouteSummary(distance_m=distance_m, duration_s=duration_s, steps=steps)
        except AmapServiceError:
            return RouteSummary(
                distance_m=int(haversine_m(origin_lat, origin_lng, destination_lat, destination_lng)),
                duration_s=0,
                steps=[RouteStep(index=0, instruction="请沿当前方向前进", distance_m=0, polyline=None)],
            )

    def _detect_offroute(self, lat: float, lng: float, session: NavigationSession) -> bool:
        points = self._route_points(session)
        if not points:
            return False
        threshold = session.settings.offroute_threshold_m or NAV_OFFROUTE_THRESHOLD_M
        nearest = nearest_point_distance_m(lat, lng, points)
        return nearest > threshold

    def _update_step_index(self, session: NavigationSession, lat: float, lng: float) -> int:
        points = self._route_points(session)
        if not points or not session.route_summary or not session.route_summary.steps:
            return 0

        threshold = session.settings.offroute_threshold_m or NAV_OFFROUTE_THRESHOLD_M
        best_index = session.current_step_index
        best_distance = float('inf')

        for idx in range(session.current_step_index, len(points)):
            distance = haversine_m(lat, lng, points[idx][0], points[idx][1])
            if distance < best_distance:
                best_distance = distance
                best_index = idx
            if distance <= threshold:
                return min(idx + 1, len(session.route_summary.steps) - 1)

        return best_index

    def _current_instruction(self, session: NavigationSession, step_index: int) -> str:
        if not session.route_summary or not session.route_summary.steps:
            return "请继续前进"

        step_index = max(0, min(step_index, len(session.route_summary.steps) - 1))
        step = session.route_summary.steps[step_index]
        if session.destination_lat and session.destination_lng:
            distance_to_destination = haversine_m(session.current_lat, session.current_lng, session.destination_lat, session.destination_lng)
            if distance_to_destination <= 50:
                return "前方接近目的地，请留意到达提示"
        return step.instruction or "请沿当前路线继续前进"

    def _route_points(self, session: NavigationSession) -> List[Tuple[float, float]]:
        points: List[Tuple[float, float]] = []
        if not session.route_summary:
            return points
        for step in session.route_summary.steps:
            if not step.polyline:
                continue
            raw_points = [p for p in step.polyline.split(';') if p]
            for point in raw_points:
                try:
                    lng_str, lat_str = point.split(',')
                    points.append((float(lat_str), float(lng_str)))
                except ValueError:
                    continue
        if not points:
            points.append((session.destination_lat, session.destination_lng))
        return points

    def _build_state(self, session: NavigationSession, distance_to_destination: float) -> NavigationState:
        return NavigationState(
            session_id=session.session_id,
            is_navigating=session.is_navigating,
            destination_name=session.destination_name,
            destination_lat=session.destination_lat,
            destination_lng=session.destination_lng,
            current_lat=session.current_lat,
            current_lng=session.current_lng,
            distance_to_destination_m=distance_to_destination,
            duration_to_destination_s=session.route_summary.duration_s if session.route_summary else 0,
            current_instruction=session.current_instruction,
            is_off_route=session.is_off_route,
            arrived=session.arrived,
            current_step_index=session.current_step_index,
            updated_at=session.updated_at,
            route_summary=session.route_summary,
            settings=session.settings,
        )

    def _event(self, event: str, payload: Dict) -> Dict:
        return {"event": event, "payload": payload, "timestamp": int(time.time())}


navigation_service = NavigationService()
