import math
from typing import Iterable, Tuple


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2) ** 2
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def nearest_point_distance_m(lat: float, lng: float, points: Iterable[Tuple[float, float]]) -> float:
    distances = [haversine_m(lat, lng, p_lat, p_lng) for p_lat, p_lng in points]
    return min(distances) if distances else float("inf")
