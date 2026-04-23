from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from schemas.navigation import (
    ApiResponse,
    DestinationSearchRequest,
    NavigationSettings,
    ReverseGeocodeRequest,
    StartNavigationRequest,
    StopNavigationRequest,
    UpdateLocationRequest,
)
from services.navigation_service import navigation_service
from services.ws_manager import ws_manager
from state.navigation_store import navigation_store

router = APIRouter(prefix="/api/navigation", tags=["navigation"])


@router.post("/search")
async def search_destination(payload: DestinationSearchRequest):
    try:
        data = await navigation_service.search_destination(payload.keyword, payload.city)
        return ApiResponse(code=0, message="ok", data=data, timestamp=0)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/reverse-geocode")
async def reverse_geocode(payload: ReverseGeocodeRequest):
    try:
        data = await navigation_service.reverse_geocode(payload.lat, payload.lng)
        return ApiResponse(code=0, message="ok", data=data, timestamp=0)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/start")
async def start_navigation(payload: StartNavigationRequest):
    try:
        settings = payload.settings or navigation_store.default_settings()
        state = await navigation_service.start_navigation(
            origin_lat=payload.origin_lat,
            origin_lng=payload.origin_lng,
            destination_keyword=payload.destination_keyword,
            destination_lat=payload.destination_lat,
            destination_lng=payload.destination_lng,
            mode=payload.mode,
            settings=settings,
        )
        return ApiResponse(code=0, message="ok", data=state.model_dump(), timestamp=state.updated_at)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/location")
async def update_location(payload: UpdateLocationRequest):
    try:
        state = await navigation_service.update_location(
            session_id=payload.session_id,
            lat=payload.lat,
            lng=payload.lng,
            heading=payload.heading,
            speed=payload.speed,
        )
        return ApiResponse(code=0, message="ok", data=state.model_dump(), timestamp=state.updated_at)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/status")
async def get_status(session_id: str):
    try:
        state = navigation_service.get_status(session_id)
        return ApiResponse(code=0, message="ok", data=state.model_dump(), timestamp=state.updated_at)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/stop")
async def stop_navigation(payload: StopNavigationRequest):
    try:
        state = await navigation_service.stop_navigation(payload.session_id)
        return ApiResponse(code=0, message="ok", data=state.model_dump(), timestamp=state.updated_at)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.websocket("/ws")
async def navigation_ws(websocket: WebSocket):
    session_id = websocket.query_params.get("session_id")
    if not session_id:
        await websocket.close(code=1008)
        return
    await ws_manager.connect(session_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(session_id, websocket)
    except Exception:
        ws_manager.disconnect(session_id, websocket)
