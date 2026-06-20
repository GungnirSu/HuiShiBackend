"""
全局配置：所有密钥统一从环境变量读取（.env 已 gitignore）。
任一必需密钥缺失时直接抛错，避免运行时才发现。
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"缺少必需的环境变量 {name}。请在项目根目录的 .env 中配置，参考 .env.example。"
        )
    return value


# 通义千问（DashScope）—— 视觉与文本大模型
API_KEY = _required("DASHSCOPE_API_KEY")

# 阿里云 NLS 语音服务（STT / TTS）
APP_KEY = _required("ALIYUN_NLS_APP_KEY")
ACCESS_KEY_ID = _required("ALIYUN_ACCESS_KEY_ID")
ACCESS_KEY_SECRET = _required("ALIYUN_ACCESS_KEY_SECRET")

# 高德地图（步行路径规划 + 地理编码）
AMAP_KEY = _required("AMAP_KEY")
