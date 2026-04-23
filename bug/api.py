import time
import dashscope
from core.config import API_KEY

dashscope.api_key = API_KEY


def test_latency():
    start_time = time.time()
    print("正在发起请求...")

    responses = dashscope.MultiModalConversation.call(
        model='qwen-vl-plus',
        messages=[{
            'role': 'user',
            'content': [
                {'image': 'https://dashscope.oss-cn-beijing.aliyuncs.com/images/dog_and_girl.jpeg'},
                {'text': '描述这张图片。'}
            ]
        }],
        stream=True  # 必须开启流式传输 [cite: 17, 87]
    )

    first_token_received = False
    for response in responses:
        if not first_token_received:
            # 计算首字延迟 (Task 1.1 要求 < 800ms)
            latency = (time.time() - start_time) * 1000
            print(f"首字延迟: {latency:.2f} ms")
            first_token_received = True

        if response.status_code == 200:
            print(response.output.choices[0].message.content[0]['text'], end="", flush=True)

    total_latency = (time.time() - start_time) * 1000
    print(f"\n总延迟: {total_latency:.2f} ms")


if __name__ == "__main__":
    test_latency()