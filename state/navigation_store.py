import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from schemas.navigation import NavigationSettings, NavigationState, RouteSummary
from core.config import (
    NAV_ARRIVE_THRESHOLD_M,
    NAV_CITY_DEFAULT,
    NAV_OFFROUTE_THRESHOLD_M,
    NAV_UPDATE_INTERVAL_SEC,
)


@dataclass
class NavigationSession:
    session_id: str
    origin_lat: float
    origin_lng: float
    destination_name: str
    destination_lat: float
    destination_lng: float
    mode: str = "walk"
    is_navigating: bool = True
    current_lat: float = 0.0
    current_lng: float = 0.0
    current_instruction: str = "正在规划路线"
    is_off_route: bool = False
    arrived: bool = False
    current_step_index: int = 0
    route_summary: Optional[RouteSummary] = None
    settings: NavigationSettings = field(default_factory=NavigationSettings)
    updated_at: int = field(default_factory=lambda: int(time.time()))

    def to_state(self) -> NavigationState:
        return NavigationState(
            session_id=self.session_id,
            is_navigating=self.is_navigating,
            destination_name=self.destination_name,
            destination_lat=self.destination_lat,
            destination_lng=self.destination_lng,
            current_lat=self.current_lat,
            current_lng=self.current_lng,
            distance_to_destination_m=0,
            duration_to_destination_s=0,
            current_instruction=self.current_instruction,
            is_off_route=self.is_off_route,
            arrived=self.arrived,
            updated_at=self.updated_at,
            route_summary=self.route_summary,
            settings=self.settings,
        )


class NavigationStore:
    def __init__(self) -> None:
        self._sessions: Dict[str, NavigationSession] = {}

    @staticmethod
    def default_settings() -> NavigationSettings:
        return NavigationSettings(
            update_interval_sec=NAV_UPDATE_INTERVAL_SEC,
            offroute_threshold_m=NAV_OFFROUTE_THRESHOLD_M,
            arrive_threshold_m=NAV_ARRIVE_THRESHOLD_M,
            default_city=NAV_CITY_DEFAULT,
        )

    def create_session(
        self,
        session_id: str,
        origin_lat: float,
        origin_lng: float,
        destination_name: str,
        destination_lat: float,
        destination_lng: float,
        mode: str,
        settings: Optional[NavigationSettings] = None,
    ) -> NavigationSession:
        merged_settings = self.default_settings()
        if settings is not None:
            merged_settings = NavigationSettings(
                update_interval_sec=settings.update_interval_sec or merged_settings.update_interval_sec,
                offroute_threshold_m=settings.offroute_threshold_m or merged_settings.offroute_threshold_m,
                arrive_threshold_m=settings.arrive_threshold_m or merged_settings.arrive_threshold_m,
                default_city=settings.default_city or merged_settings.default_city,
            )
        session = NavigationSession(
            session_id=session_id,
            origin_lat=origin_lat,
            origin_lng=origin_lng,
            destination_name=destination_name,
            destination_lat=destination_lat,
            destination_lng=destination_lng,
            mode=mode,
            settings=merged_settings,
            current_lat=origin_lat,
            current_lng=origin_lng,
        )
        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[NavigationSession]:
        return self._sessions.get(session_id)

    def update_session(self, session: NavigationSession) -> None:
        session.updated_at = int(time.time())
        self._sessions[session.session_id] = session

    def remove_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


navigation_store = NavigationStore()
