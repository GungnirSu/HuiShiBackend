import os
import json
import time
import uuid
import httpx
from fastapi import APIRouter, UploadFile, File, HTTPException
from aliyunsdkcore.client import AcsClient
from aliyunsdkcore.request import CommonRequest

from core.config import APP_KEY, ACCESS_KEY_ID, ACCESS_KEY_SECRET

router = APIRouter()

# 确保本地静态文件夹存在，用来存放生成的给前端下载的 TTS 音频文件
STATIC_DIR = "static"
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR)

# 全局本地 Token 缓存
_token_cache = {"token": None, "expire_time": 0}


def get_aliyun_nls_token() -> str:
    current_time = time.time()
    if _token_cache["token"] and (_token_cache["expire_time"] - current_time > 600):
        return _token_cache["token"]

    try:
        client = AcsClient(ACCESS_KEY_ID, ACCESS_KEY_SECRET, "cn-shanghai")
        request = CommonRequest()
        request.set_domain('nls-meta.cn-shanghai.aliyuncs.com')
        request.set_version('2019-02-28')
        request.set_action_name('CreateToken')

        response = client.do_action_with_exception(request)
        result = json.loads(response.decode('utf-8'))
        if "Token" in result:
            _token_cache["token"] = result["Token"]["Id"]
            _token_cache["expire_time"] = result["Token"]["ExpireTime"]
            return _token_cache["token"]
        else:
            raise Exception(f"阿里云 Token 刷新失败: {result}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"语音组件鉴权失败: {str(e)}")


@router.post("/speech/stt")
async def speech_to_text(audio: UploadFile = File(...)):
    """
    对接文档要求：/speech/stt 语音转文字
    输入：File: audio (录音临时文件)
    输出：{"text": "识别的文本内容"}
    """
    t_start = time.time()
    print(f"[stt] start ts={t_start:.3f} filename={audio.filename}")

    token = get_aliyun_nls_token()
    t_token = time.time()
    print(f"[stt] token ready +{(t_token - t_start)*1000:.0f}ms")

    audio_content = await audio.read()
    t_read = time.time()
    print(f"[stt] audio read +{(t_read - t_start)*1000:.0f}ms size={len(audio_content)/1024:.1f}KB")

    if not audio_content:
        print(f"[stt] empty audio, return")
        return {"text": ""}

    # 自动识别前端传入的文件格式
    format_param = "pcm"
    if audio.filename.endswith(".wav"):
        format_param = "wav"
    elif audio.filename.endswith(".mp3"):
        format_param = "mp3"

    # 拼接带有格式参数的阿里云请求 URL
    url = (
        f"http://nls-gateway-cn-shanghai.aliyuncs.com/stream/v1/asr"
        f"?appkey={APP_KEY}"
        f"&format={format_param}"
        f"&sample_rate=16000"
    )

    headers = {
        "X-NLS-Token": token,
        "Content-Type": "audio/mp3" if format_param == "mp3" else "audio/pcm"
    }

    async with httpx.AsyncClient() as client:
        try:
            t_call = time.time()
            response = await client.post(url, headers=headers, content=audio_content, timeout=15.0)
            t_done = time.time()
            res_json = response.json()
            print(f"[stt] aliyun done +{(t_done - t_start)*1000:.0f}ms (call_only={(t_done - t_call)*1000:.0f}ms) status={response.status_code}")

            # 严格适配文档输出格式
            if response.status_code == 200 and res_json.get("status") == 20000000:
                recognized = res_json.get("result", "")
                # ★ 关键调试输出：把 STT 识别到的原文打出来
                print(f"[stt] >>> RESULT: \"{recognized}\"")
                print(f"[stt] DONE total={(time.time() - t_start)*1000:.0f}ms")
                return {"text": recognized}
            else:
                print(f"[stt] !!! 识别失败 res_json={res_json}")
                return {"text": ""}
        except Exception as e:
            print(f"[stt] !!! 异常 after {(time.time() - t_start)*1000:.0f}ms: {e}")
            return {"text": ""}


@router.post("/speech/tts")
async def text_to_speech(payload: dict):
    """
    对接文档要求：/speech/tts 文字转语音
    输入：{"text": "...", "speed": 1.0, "voice": "default"}
    输出：{"audioUrl": "音频文件 URL 地址"}
    """
    t_start = time.time()
    text = payload.get("text")
    print(f"[tts] start ts={t_start:.3f} text=\"{(text or '')[:60]}\" len={len(text or '')}")
    if not text:
        raise HTTPException(status_code=400, detail="文本内容不能为空")

    speed_factor = payload.get("speed", 1.0)
    voice_param = payload.get("voice", "default")

    token = get_aliyun_nls_token()
    t_token = time.time()
    print(f"[tts] token ready +{(t_token - t_start)*1000:.0f}ms")
    url = "https://nls-gateway-cn-shanghai.aliyuncs.com/stream/v1/tts"

    # 1. 语速转换映射：阿里云 RESTful 语速范围为 -500 到 500，0 代表 1.0 倍速
    # 我们将前端传的 1.0 映射为 0，1.2 映射为 100，以此类推
    speech_rate = int((speed_factor - 1.0) * 500)
    speech_rate = max(-500, min(500, speech_rate))

    # 2. 音色映射
    # 注意：阿里云 NLS 里 aijia(艾佳) 其实是「标准女声」，曾被误当成男声用，
    # 导致选男声仍是女声。xiaoyun/xiaogang 是基础档的女声/男声标准搭档。
    voice = "xiaoyun"  # 默认温柔女声，适合视障引导
    if voice_param == "male":
        voice = "xiaogang"  # 标准男声（与 xiaoyun 同档位，确保账号可用）
    elif voice_param == "female":
        voice = "xiaoyun"

    data = {
        "appkey": APP_KEY,
        "token": token,
        "text": text,
        "format": "mp3",
        "sample_rate": 16000,
        "voice": voice,
        "speech_rate": speech_rate,
        "volume": 50,
        "pitch_rate": 0
    }

    async with httpx.AsyncClient() as client:
        try:
            t_call = time.time()
            response = await client.post(url, headers={"Content-Type": "application/json"}, json=data, timeout=10.0)
            t_done = time.time()
            print(f"[tts] aliyun done +{(t_done - t_start)*1000:.0f}ms (call_only={(t_done - t_call)*1000:.0f}ms) status={response.status_code} bytes={len(response.content)}")

            if response.status_code == 200 and "audio" in response.headers.get("content-type", ""):
                # 3. 生成本地唯一的音频文件名并保存
                filename = f"tts_{uuid.uuid4().hex}.mp3"
                filepath = os.path.join(STATIC_DIR, filename)

                with open(filepath, "wb") as f:
                    f.write(response.content)

                # 4. 返回相对路径 URL。前端通过拼接 ${API_URL}/static/tts_xxx.mp3 即可直接拉取播放
                print(f"[tts] DONE total={(time.time() - t_start)*1000:.0f}ms file={filename}")
                return {"audioUrl": f"/static/{filename}"}
            else:
                err_msg = response.text if response.status_code == 200 else f"HTTP {response.status_code}"
                print(f"[tts] !!! 失败 err={err_msg[:200]}")
                raise HTTPException(status_code=500, detail=f"阿里云TTS生成失败: {err_msg}")
        except HTTPException:
            raise
        except Exception as e:
            print(f"[tts] !!! 异常 after {(time.time() - t_start)*1000:.0f}ms: {e}")
            raise HTTPException(status_code=500, detail=f"TTS中转组件异常: {str(e)}")