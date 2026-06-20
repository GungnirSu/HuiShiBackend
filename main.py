import time
import uuid
import base64
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
import dashscope
from core.config import API_KEY
from models.database import SessionLocal, VisionLog, init_db

from api.speech import router as speech_router
from api.navigation import router as nav_router
from api.travel import router as travel_router

# main.py 头部新增导入
import os
from fastapi.staticfiles import StaticFiles

# 初始化 FastAPI 应用
app = FastAPI(title="HuiVision 慧视后端", version="1.1.0")

app.include_router(speech_router)
app.include_router(nav_router)
app.include_router(travel_router)

if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")

# 任务 4.2: 启动时初始化数据库表
init_db()
dashscope.api_key = API_KEY


@app.post("/v1/vision/analyze")
async def analyze_scene(file: UploadFile = File(...)):
    """
    核心接口：接收图片并流式返回 AI 描述，同时记录性能指标到数据库
    满足任务书 1.1 (API 链路) 与 4.2 (数据记录)
    """
    request_id = str(uuid.uuid4())  # 生成本次请求的唯一 ID [cite: 87]
    start_time = time.time()
    print(f"[vision] start ts={start_time:.3f} req_id={request_id}")

    # 读取图片并转为 Base64
    content = await file.read()
    if not content:
        print(f"[vision] !!! 空图片")
        raise HTTPException(status_code=400, detail="图片上传失败")
    base64_image = base64.b64encode(content).decode("utf-8")
    print(f"[vision] file read +{(time.time() - start_time)*1000:.0f}ms size={len(content)/1024:.1f}KB b64={len(base64_image)/1024:.1f}KB")

    async def event_generator():
        full_content = ""  # 存储 AI 返回的完整文本
        first_token_time = None  # 记录首字生成的时间点

        t_model_start = time.time()
        print(f"[vision] model call start +{(t_model_start - start_time)*1000:.0f}ms")
        # --- 修复 NameError 的关键：确保在这里定义 responses ---
        responses = dashscope.MultiModalConversation.call(
            model='qwen-vl-plus',
            messages=[{'role': 'user', 'content': [
                {'image': f'data:image/jpeg;base64,{base64_image}'},
                {'text': '你是一位视障人士向导。请简洁描述正前方2米内的障碍物及方位。'}
            ]}],
            stream=True
        )

        # 迭代流式响应 [cite: 17]
        for response in responses:
            if response.status_code == 200:
                # 获取当前累加的文本
                current_full_text = response.output.choices[0].message.content[0]['text']
                # 计算本次新增的字符 (增量处理)
                new_content = current_full_text[len(full_content):]
                full_content = current_full_text

                # 记录首字延迟
                if not first_token_time and new_content:
                    first_token_time = time.time()
                    print(f"[vision] first token +{(first_token_time - start_time)*1000:.0f}ms")

                if new_content:
                    yield new_content
            else:
                print(f"[vision] !!! 流式错误 status={response.status_code} msg={response.message}")
                yield f"Error: {response.message}"

        # --- 任务 1.1 & 4.2: 计算量化指标并存入数据库 ---
        end_time = time.time()
        # 计算指标：首字延迟应 < 800ms，总延迟应 < 1500ms [cite: 22, 24]
        first_latency = (first_token_time - start_time) * 1000 if first_token_time else 0
        total_latency = (end_time - start_time) * 1000

        # 写入 SQLite 数据库 [cite: 87]
        db = SessionLocal()
        try:
            log_entry = VisionLog(
                request_id=request_id,
                image_path=file.filename,  # 存储图片元数据
                ai_result=full_content,
                first_token_latency=first_latency,
                total_latency=total_latency
            )
            db.add(log_entry)
            db.commit()
            print(f"[vision] DONE total={total_latency:.0f}ms first_token={first_latency:.0f}ms req_id={request_id}")
        except Exception as e:
            print(f"数据库写入失败: {e}")
        finally:
            db.close()

    return StreamingResponse(event_generator(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)