"""
生成前端 static/audio/ 下需要的固定提示语 mp3。

使用方法：
  1. 启动后端：python main.py
  2. 另开终端运行：python tools/generate_hints.py

特性：
  - 已存在且 >=1.5KB 的文件自动跳过（不会覆盖好文件）
  - 每条间隔 1.5s，避开阿里云 NLS TTS 的 5 QPS 限流
  - 失败重试 3 次
  - 下载完校验 mp3 大小，过小当失败重试
  - 失败的不会留下 0 字节空文件
"""

import os
import sys
import time
import httpx

# ---- 可配置 ----
BACKEND_BASE = os.environ.get("HUIHOU_BASE", "http://127.0.0.1:8000")
OUT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "miniprogram", "static", "audio")
)
SLEEP_BETWEEN = 1.5         # 每条请求间隔（秒）
MAX_RETRIES = 3
MIN_VALID_BYTES = 1500      # 小于这个值判定为失败（正常 1-2 秒中文 mp3 都 5KB+）
VOICE = "female"
SPEED = 1.0

# ---- 要生成的清单（key 必须与 utils/tts.ts 的 HINTS 一致） ----
ITEMS = [
    ("recognizing.mp3",       "识别中"),
    ("cancelled.mp3",         "已取消"),
    ("not_heard.mp3",         "没听清"),
    ("not_understood.mp3",    "没听懂"),
    ("analyze_failed.mp3",    "识别失败，请稍后重试"),
    ("stt_failed.mp3",        "语音识别失败"),
    ("mode_default.mp3",      "已切换到默认模式"),
    ("mode_travel.mp3",       "已切换到出行模式"),
    ("mode_guide.mp3",        "已切换到导游模式"),
    ("mode_repair.mp3",       "已切换到维修模式"),
    ("mode_nav.mp3",          "已切换到导航模式"),
]


def is_already_done(path: str) -> bool:
    """文件存在且尺寸合理就视为已完成，不重新生成"""
    return os.path.exists(path) and os.path.getsize(path) >= MIN_VALID_BYTES


def fetch_one(client: httpx.Client, filename: str, text: str) -> bool:
    path = os.path.join(OUT_DIR, filename)

    if is_already_done(path):
        print(f"[skip] {filename:<22} {os.path.getsize(path):>6} bytes  (已存在)")
        return True

    # 若存在但太小，先删掉避免误判
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # 1. 调后端 /speech/tts
            resp = client.post(
                f"{BACKEND_BASE}/speech/tts",
                json={"text": text, "voice": VOICE, "speed": SPEED},
                timeout=30.0,
            )
            if resp.status_code != 200:
                print(f"[err]  {filename:<22} attempt {attempt}: tts HTTP {resp.status_code} {resp.text[:120]}")
                time.sleep(2.0)
                continue

            audio_url = resp.json().get("audioUrl")
            if not audio_url:
                print(f"[err]  {filename:<22} attempt {attempt}: 后端返回缺 audioUrl")
                time.sleep(2.0)
                continue

            # 2. 拉取后端落盘的 mp3
            dl = client.get(f"{BACKEND_BASE}{audio_url}", timeout=15.0)
            dl.raise_for_status()
            content = dl.content

            if len(content) < MIN_VALID_BYTES:
                print(f"[warn] {filename:<22} attempt {attempt}: 仅 {len(content)} bytes，疑似限流，重试")
                time.sleep(3.0)
                continue

            with open(path, "wb") as f:
                f.write(content)
            print(f"[ok]   {filename:<22} {len(content):>6} bytes  text=\"{text}\"")
            return True

        except Exception as e:
            print(f"[err]  {filename:<22} attempt {attempt}: {e}")
            time.sleep(2.0)

    print(f"[FAIL] {filename:<22} 三次重试全部失败")
    return False


def main():
    print(f"后端: {BACKEND_BASE}")
    print(f"输出: {OUT_DIR}")
    print(f"共 {len(ITEMS)} 个，每条间隔 {SLEEP_BETWEEN}s，最多重试 {MAX_RETRIES} 次")
    print("-" * 72)

    if not os.path.exists(OUT_DIR):
        os.makedirs(OUT_DIR)

    # 先 ping 一下后端，免得跑半天发现没启
    try:
        with httpx.Client() as c:
            c.get(f"{BACKEND_BASE}/docs", timeout=3.0).raise_for_status()
    except Exception as e:
        print(f"!!! 后端不可达 ({e})，请先启动 python main.py")
        sys.exit(2)

    pass_cnt = 0
    fail_list = []
    with httpx.Client() as client:
        for i, (filename, text) in enumerate(ITEMS):
            ok = fetch_one(client, filename, text)
            if ok:
                pass_cnt += 1
            else:
                fail_list.append(filename)
            # 最后一条不用 sleep
            if i < len(ITEMS) - 1:
                time.sleep(SLEEP_BETWEEN)

    print("-" * 72)
    print(f"完成：{pass_cnt}/{len(ITEMS)} 成功")
    if fail_list:
        print(f"失败：{', '.join(fail_list)}")
        print("提示：可再跑一次脚本，已生成的会自动跳过，只补缺失的")
        sys.exit(1)


if __name__ == "__main__":
    main()
