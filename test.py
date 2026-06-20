import httpx, socket, os, time
print("proxy env:", {k: v for k, v in os.environ.items() if 'proxy' in k.lower()})
try:
    print("DNS gateway ->", socket.gethostbyname("nls-gateway-cn-shanghai.aliyuncs.com"))
except Exception as e:
    print("DNS FAIL:", e)
for url in ["https://nls-gateway-cn-shanghai.aliyuncs.com/stream/v1/tts",
            "https://dashscope.aliyuncs.com",
            "https://www.aliyun.com"]:
    t = time.time()
    try:
        r = httpx.get(url, timeout=15)
        print(f"OK   {url} -> {r.status_code}  {(time.time()-t)*1000:.0f}ms")
    except Exception as e:
        print(f"FAIL {url} -> {type(e).__name__}: {e}")