import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
AMAP_WEB_KEY = os.getenv("AMAP_WEB_KEY", "")
NAV_UPDATE_INTERVAL_SEC = int(os.getenv("NAV_UPDATE_INTERVAL_SEC", "2"))
NAV_OFFROUTE_THRESHOLD_M = int(os.getenv("NAV_OFFROUTE_THRESHOLD_M", "25"))
NAV_ARRIVE_THRESHOLD_M = int(os.getenv("NAV_ARRIVE_THRESHOLD_M", "12"))
NAV_CITY_DEFAULT = os.getenv("NAV_CITY_DEFAULT", "济南")
