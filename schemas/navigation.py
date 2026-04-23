from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class NavigationSettings(BaseModel):
    update_interval_sec: Optional[int] = Field(default=None, ge=1, le=10)
    offroute_threshold_m: Optional[int] = Field(default=None, ge=5, le=100)
    arrive_threshold_m: Optional[int] = Field(default=None, ge=3, le=50)
    default_city: Optional[str] = Field(default=None, max_length=50)


class DestinationSearchRequest(BaseModel):
    keyword: str = Field(..., min_length=1, max_length=100)
    city: Optional[str] = Field(default=None, max_length=50)


class ReverseGeocodeRequest(BaseModel):
    lat: float
    lng: float


class StartNavigationRequest(BaseModel):
    destination_keyword: Optional[str] = Field(default=None, max_length=100)
    destination_lat: Optional[float] = None
    destination_lng: Optional[float] = None
    origin_lat: float
    origin_lng: float
    mode: str = Field(default="walk", pattern="^(walk|drive|ride)$")
    settings: Optional[NavigationSettings] = None


class UpdateLocationRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    lat: float
    lng: float
    heading: Optional[float] = None
    speed: Optional[float] = None


class StopNavigationRequest(BaseModel):
    session_id: str = Field(..., min_length=1)


class RouteStep(BaseModel):
    index: int
    instruction: str
    distance_m: int = 0
    polyline: Optional[str] = None


class RouteSummary(BaseModel):
    distance_m: int = 0
    duration_s: int = 0
    steps: List[RouteStep] = Field(default_factory=list)


class NavigationState(BaseModel):
    session_id: str
    is_navigating: bool
    destination_name: str
    destination_lat: float
    destination_lng: float
    current_lat: float
    current_lng: float
    distance_to_destination_m: float
    duration_to_destination_s: int
    current_instruction: str
    is_off_route: bool
    arrived: bool
    current_step_index: int = 0
    updated_at: int
    route_summary: Optional[RouteSummary] = None
    settings: Optional[NavigationSettings] = None


class ApiResponse(BaseModel):
    code: int
    message: str
    data: Any
    timestamp: int


class RouteSearchItem(BaseModel):
    name: str
    address: Optional[str] = None
    location: Optional[str] = None
    adcode: Optional[str] = None


class WebSocketEvent(BaseModel):
    event: str
    payload: Dict[str, Any]
    timestamp: int
