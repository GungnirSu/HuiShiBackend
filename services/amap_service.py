import os
from typing import Any, Dict, List, Optional, Tuple

import httpx

from core.config import AMAP_WEB_KEY, NAV_CITY_DEFAULT

AMAP_BASE_URL = "https://restapi.amap.com/v3"


class AmapServiceError(RuntimeError):
    pass


class AmapService:
    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = api_key or AMAP_WEB_KEY or os.getenv("AMAP_WEB_KEY", "")
        if not self.api_key:
            raise AmapServiceError("AMAP_WEB_KEY 未配置")

    async def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        params = {**params, "key": self.api_key}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{AMAP_BASE_URL}{path}", params=params)
            resp.raise_for_status()
            data = resp.json()
            if str(data.get("status")) != "1":
                raise AmapServiceError(data.get("info", "高德接口调用失败"))
            return data

    async def search_poi(self, keyword: str, city: Optional[str] = None) -> List[Dict[str, Any]]:
        data = await self._get(
            "/place/text",
            {
                "keywords": keyword,
                "city": city or NAV_CITY_DEFAULT,
                "offset": 10,
                "page": 1,
                "extensions": "base",
            },
        )
        return data.get("pois", [])

    async def geocode(self, address: str, city: Optional[str] = None) -> Optional[Dict[str, Any]]:
        data = await self._get(
            "/geocode/geo",
            {
                "address": address,
                "city": city or NAV_CITY_DEFAULT,
            },
        )
        geocodes = data.get("geocodes", [])
        return geocodes[0] if geocodes else None

    async def reverse_geocode(self, lat: float, lng: float) -> Optional[Dict[str, Any]]:
        data = await self._get(
            "/geocode/regeo",
            {
                "location": f"{lng},{lat}",
                "extensions": "base",
            },
        )
        return data.get("regeocode")

    async def route_walk(self, origin: Tuple[float, float], destination: Tuple[float, float]) -> Dict[str, Any]:
        origin_str = f"{origin[1]},{origin[0]}"
        destination_str = f"{destination[1]},{destination[0]}"
        data = await self._get(
            "/direction/walking",
            {
                "origin": origin_str,
                "destination": destination_str,
                "extensions": "base",
            },
        )
        return data
