import json
import base64
import time
import uuid
import dashscope
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from pydantic import BaseModel
from typing import Optional, Any
from core.config import API_KEY

# 初始化模型权限
dashscope.api_key = API_KEY

router = APIRouter(prefix="/api/travel", tags=["旅游/引导"])


# --- 定义通用返回格式 (StandardResponse) ---
class StandardResponse(BaseModel):
    code: int = 200
    message: str = "success"
    data: Optional[Any] = None
    request_id: str


# ============================================================
# 不同模式对应不同 prompt —— 视障辅助的核心调优点都在这里
# ============================================================
# 四种模式的播报定位（关键区别在 navigationFusion 的"口吻"）：
#   - travel 出行：唯一的"导航口吻"模式。可以也应该给"障碍物/方位/可继续直行"
#                  这类通行结论。核心是路面 + 障碍 + 门/通道（尤其玻璃门、门开关）
#                  + 转弯，按"危险优先"合成播报。
#   - guide  导游：讲解口吻。讲景物/地标/招牌文字，不谈障碍、不谈前行
#                  （除非真有危险才提醒）。
#   - repair 维修：报修口吻。识别物品 + 找故障部位，输出可直接进工单的话。
#   - default 默认：主页随手拍的"客观描述"口吻。**不是导航**——只有当画面
#                  确实是一条畅通道路时才可给通行结论；拍到桌子/物品/风景等
#                  非道路场景，严禁出现"障碍物/请继续直行/可以前行"等导航词。
#
# 所有 prompt 共享的两条铁律：
# 1. 画面无效（黑屏/遮挡/糊）必须老实说看不清，不准编造
# 2. 输出必须是纯 JSON，6 个字段都给齐
PROMPT_COMMON_RULES = """
【铁律1：画面无效时必须老实承认】
如果图像出现以下任一情况，绝对不要编造障碍物或物体：
- 整体过暗、过亮、纯黑、纯白
- 镜头被手指/布料/物体遮挡
- 严重模糊、虚焦、抖动
此时应返回：
  level="warning", sceneType="unknown"
  quickSummary="无法识别画面"
  obstacleAlert / roadCondition 简要说明原因（"摄像头被遮挡"等）
  navigationFusion="看不清前方，请调整摄像头后再试"

【铁律2：输出格式】
必须以纯 JSON 返回，不要任何 Markdown 标识符或额外解释：
{
    "quickSummary": "一句话简短总结",
    "obstacleAlert": "障碍物/异常预警",
    "roadCondition": "路况/场景描述",
    "navigationFusion": "给视障用户的最终播报词（要直接、易懂）",
    "level": "normal | warning | danger",
    "sceneType": "outdoor | indoor | unknown"
}
"""


def build_prompt(mode: str, instr: str, dist: float) -> str:
    """根据前端传的 mode 字段返回对应 prompt"""

    if mode == "travel":
        # 出行模式：唯一的"导航口吻"。空间感知版——先判断是否对准道路，
        # 再按"纵深 × 左右"归位、危险优先合成播报。绝不一股脑罗列画面内容。
        task = f"""
【任务：出行辅助导航（空间感知版）】
你是视障用户的"眼睛"，正帮他在路上安全行走，他完全看不见前方。
当前导航：指令"{instr}"，距离转弯点 {dist} 米。
不要把画面里的东西一股脑全念出来。先在心里按下面的步骤把空间想清楚，
再只挑"对他接下来几步最有用"的话说。

【第0步：瞄准检查——这张图是不是对准了前方的路？】
先判断镜头是否大致水平、朝向"正前方可行走的地面/通道"。
若画面其实是对着【天花板 / 天空 / 一面近墙 / 手里的东西 / 正下方的地面 / 严重偏斜】，
即没有拍到前方的路：
  → level="warning"，sceneType 照实填，
    obstacleAlert="画面未对准前方道路"，
    roadCondition 简述实际拍到了什么（如"镜头朝向天花板"），
    navigationFusion="没有拍到前方的路，请把手机竖直、镜头水平朝向正前方，再拍一次"。
  → 并且【不要】把画面里的杂物逐个当成障碍物播报。
对准了前方的路 → 继续下面的步骤。

【第1步：用户此刻的方位 / 朝向】
用一句话定位他正对着的空间：正前方是开阔通道 / 面对一堵墙 / 走廊向左（或向右）延伸 /
前方是路口或拐角 / 前方是上行或下行楼梯。

【第2步：把可见要素按"纵深 × 左右"归位（这是思考过程，不要原样念）】
- 纵深分三档：
  · 脚下到约1米内 —— 最高优先，直接影响下一步落脚；
  · 前方约2~4米 —— 次优先，马上要走到；
  · 约4米以外较远 —— 仅作提示，不当作紧迫障碍。
- 横向分三向：左 / 正前 / 右。
- 逐项记下【物体 + 纵深档 + 左右方位】，尤其盯死这几类（视障用户最容易受伤）：
  · 玻璃门 / 落地玻璃 / 玻璃幕墙（几乎看不见，重点）；门是开是关、要推还是拉、是否自动门/旋转门；
  · 台阶（上行/下行、几级）、斜坡、坑洼、缝隙、井盖、门槛；下行台阶和深坑尤其危险；
  · 立柱、护栏、行人、自行车、电动车、垃圾桶、悬挂物、车辆。
- 【关键纠偏】这些危险物【必须】带"距离+方位"。位于侧前方或较远处的，要说"左前方约X米"/
  "右前方约X米"，【绝不能】笼统说"前方有玻璃门"——否则用户会以为正前方马上就撞上。

【第3步：判断能不能走 + 定 level（取最危险一项）】
- 只有落在【正前方行走通道、脚下到中距】里的东西，才决定他能不能继续走、要不要停；
  侧方或远处的只作背景提示。
- danger：正前方近处的玻璃门/落地玻璃、向下台阶或深坑、贴近身体的障碍、驶近的车辆——可能受伤。
- warning：上行台阶、立柱、行人、关着的门、通道变窄、湿滑/不平路面等需注意但不致命；
  或侧前方有需要留意的玻璃门/台阶。
- normal：正前方通道开阔、路面平整、脚下与中距无障碍，可以放心继续直行。

【第4步：合成 navigationFusion（真正播给用户的话）】
- 口语、简短，一句说一件事。顺序：先播【正前方近处】最紧迫的，再播【接下来几米要注意的，
  带方位和距离】，最后才说转弯或"可继续走"。
- 危险物明确点名（玻璃门、向下台阶），别笼统说"有障碍物"；带【方位】、能判断时带【大致距离】、
  给【可执行动作】（停下 / 靠左通过 / 伸手探门再走 / 扶好慢下台阶）。
- 结合导航指令"{instr}"，在合适时机提示在哪、朝哪个方向转。
- 只有确认正前方通道安全、门通畅、路面平整时，才可以说"前方畅通，可以继续直行"。

【正例 / 反例】
- 正例（玻璃门在侧前方）："正前方通道开阔，可以往前走；注意左前方约四米有一扇玻璃门，
  走到那儿先伸手探一下再过。"
- 正例（近处台阶）："注意，正前方一米有三级向下的台阶，请扶好慢慢下。"
- 反例（方位错）：把左前方五米的玻璃门播成"前方有玻璃门，请停下"——位置说错会误导，甚至让他急停摔倒。
- 反例（没对准还硬播）：镜头对着天花板时说"前方无障碍，可以直行"。

【关于距离的说明】
单张图片的距离是估计值，请用透视、物体相对大小、遮挡关系把距离【粗分到上面三档】即可，
不必报精确米数；不确定时就说"大约"。

字段填写（出行模式）：
  - quickSummary：一句话概括前方场景（含朝向，如"正前方是一条向左延伸的走廊"）。
  - obstacleAlert：最该警惕的障碍/门/台阶，【必须带纵深档 + 左右方位】；确实没有就填"无"。
  - roadCondition：路面与通道情况（平整/有台阶/玻璃门/变窄/路口等）。
"""
    elif mode == "guide":
        # 导游模式：讲解景物、地标、文字
        task = """
【任务：导游讲解】
你是为视障用户讲解周围环境的导游。

要点：
1. 描述画面整体环境（室内/室外，大致场景：公园/街道/商场/景点）。
2. 重点讲解画面里能看到的【景物、建筑、地标、招牌文字、艺术品】。
3. 如果有可识别的文字（招牌、路牌、说明牌），念出来。
4. 给视障用户一种"被介绍这里有什么"的感觉，**不需要强调障碍物**。
5. obstacleAlert 此模式用作"画面中显眼物体的提示"（如"前方是一座红色雕像"）。
6. roadCondition 描述周围环境氛围（"这里是一条种满梧桐的街道"）。
7. navigationFusion 是综合讲解词，2-3 句，温和、信息量丰富；这是【讲解】不是导航，
   不要出现"障碍物/请继续直行/可以前行/避让"等导航词（除非画面真有危险需要提醒）。
8. level 一般用 normal；除非画面里有明显危险才 warning。
"""
    elif mode == "repair":
        # 维修模式：识别故障/损坏部位
        task = """
【任务：维修报修】
用户拍下了需要报修的物体或设施。

要点：
1. 先识别画面里【主要物品是什么】（电梯、座椅、门把手、墙面、灯具、电器…）。
2. 仔细找出【损坏/异常的部位】：破损、脱落、漏水、裂痕、变形、缺失零件、污渍。
3. obstacleAlert 用来描述【具体故障】："门把手脱落"、"墙面有大面积渗水痕迹"。
4. roadCondition 用来描述物品状态："座椅靠背的右侧塑料破裂，露出内部金属"。
5. navigationFusion 给一句【可以直接用于报修工单】的话："维修建议：3 楼休息区一把座椅靠背破损，建议更换"。
6. level: 影响使用→warning；有安全隐患（漏电、玻璃碎片）→danger；外观磨损→normal。
"""
    else:
        # default：主页随手拍 —— 客观描述画面，【不是导航】，绝不硬凑障碍物/通行结论
        task = """
【任务：默认场景描述（主页随手拍）】
用户在主页随手拍，只想知道"镜头前是什么"，**不一定在走路，更不是在找路**。
你的职责是【客观描述画面内容】，绝对不是导航播报。

第一步先判断：画面主体是不是"一条可供行走的道路/走道/人行道/楼梯通道"？
  A.【是道路，且畅通无阻】→ 才可以给通行类结论，例如"前方是一段平整的人行道，畅通可通行"。
  B.【是道路，但有台阶/障碍/危险】→ 提示障碍及方位（左/中/右）。
  C.【不是道路】（桌子、物品、墙面、书本、食物、电器、人脸、风景、室内陈设…）
     → 【严禁】出现以下任何词：障碍物、前方无障碍、请继续直行、可以前行、是否前行、放心走、避让。
       只老老实实描述画面里有什么。

字段含义（默认模式）：
  - quickSummary：一句话画面是什么（"一张木桌"、"户外阳光下的公园小径"）。
  - obstacleAlert：仅 A/B（确实是道路）时填障碍与方位；情况 C 一律填"无"。
  - roadCondition：A/B 描述路况；C 描述物体/环境状态（"桌上有一个白色水杯和两本书"）。
  - navigationFusion：最终播报词，1-2 句口语化，严格遵守上面"非道路不导航"的规则。
  - level：默认 normal；只有画面里真实可见的危险才升 warning/danger。

【反例】拍到桌子时，绝不能播报"前方无障碍物，请继续直行"；
【正例】应播报"画面里是一张木桌，桌上放着一个水杯和几本书"。
"""

    return task + "\n" + PROMPT_COMMON_RULES


@router.post("/analyze", response_model=StandardResponse)
async def analyze_scene(
        file: UploadFile = File(...),
        payload: str = Form(...)
):
    """
    融合导航分析接口：接收图片 + 导航上下文，由 Qwen-VL-Plus 生成综合描述
    """
    req_id = str(uuid.uuid4())
    t_start = time.time()
    print(f"[analyze] start ts={t_start:.3f} req_id={req_id}")

    try:
        # 1. 解析前端 payload
        try:
            input_data = json.loads(payload)
        except:
            return StandardResponse(code=400, message="Payload JSON 格式非法", request_id=req_id)

        nav_info = input_data.get("navigation", {})
        instr = nav_info.get("instruction", "保持直行")
        dist = nav_info.get("distanceToTurn", 0)
        mode = input_data.get("mode", "default")  # 前端必传，缺省 default
        print(f"[analyze] mode={mode} instr=\"{instr}\" dist={dist}")

        # 2. 图片转 Base64 编码
        image_content = await file.read()
        base64_image = base64.b64encode(image_content).decode("utf-8")
        t_read = time.time()
        print(f"[analyze] file read done +{(t_read - t_start)*1000:.0f}ms size={len(image_content)/1024:.1f}KB b64={len(base64_image)/1024:.1f}KB")

        # 3. 按模式构造 Prompt
        prompt = build_prompt(mode, instr, dist)

        # 4. 调用视觉模型 (选择模型在此处指定)
        t_model_start = time.time()
        print(f"[analyze] model call start +{(t_model_start - t_start)*1000:.0f}ms")
        response = dashscope.MultiModalConversation.call(
            model='qwen-vl-plus',
            messages=[{'role': 'user', 'content': [
                {'image': f'data:image/jpeg;base64,{base64_image}'},
                {'text': prompt}
            ]}]
        )
        t_model_done = time.time()
        print(f"[analyze] model done +{(t_model_done - t_start)*1000:.0f}ms (model_only={(t_model_done - t_model_start)*1000:.0f}ms) status={response.status_code}")

        if response.status_code != 200:
            return StandardResponse(code=response.status_code, message=response.message, request_id=req_id)

        # 5. 防御性解析 AI 返回的文本内容
        ai_text = response.output.choices[0].message.content[0]['text']

        try:
            # 去除可能存在的 Markdown 代码块标记
            clean_json = ai_text.replace("```json", "").replace("```", "").strip()
            final_data = json.loads(clean_json)
        except:
            # 如果 AI 没有返回 JSON，则手动封装以防前端崩溃
            final_data = {
                "quickSummary": "识别完成",
                "obstacleAlert": ai_text,
                "roadCondition": "请查看具体描述",
                "navigationFusion": f"AI建议：{ai_text}",
                "level": "normal",
                "sceneType": "unknown"
            }

        t_end = time.time()
        print(f"[analyze] DONE total={(t_end - t_start)*1000:.0f}ms req_id={req_id}")
        return StandardResponse(data=final_data, request_id=req_id)

    except Exception as e:
        print(f"[analyze] ERROR after {(time.time() - t_start)*1000:.0f}ms: {e}")
        return StandardResponse(code=500, message=f"服务器内部错误: {str(e)}", request_id=req_id)


@router.post("/parse-command", response_model=StandardResponse)
async def parse_command(data: dict):
    """
    指令解析接口：使用 Qwen-Plus 识别用户意图。
    前端会带 mode 字段，让模型理解用户当前所处的场景，给更准确的回复。
    """
    req_id = str(uuid.uuid4())
    user_text = data.get("text", "")
    cur_mode = data.get("mode", "default")
    t_start = time.time()
    # ★ 关键调试输出：把前端送进来的用户文本和当前模式都打出来
    print(f"[parse] start ts={t_start:.3f} req_id={req_id} mode={cur_mode} >>> TEXT: \"{user_text}\"")

    mode_hint = {
        "default": "用户在主页（默认模式）",
        "travel":  "用户在出行模式（关心路况、障碍物、转弯）",
        "guide":   "用户在导游模式（关心景物、地标、文字说明）",
        "nav":     "用户在导航模式（关心目的地、路线选择、偏航）",
        "repair":  "用户在维修模式（关心物品故障、报修信息）",
    }.get(cur_mode, "用户在主页")

    prompt = (
        f"你是视障辅助小程序的语音指令解析器。{mode_hint}。\n"
        f"用户原话：\"{user_text}\"\n\n"
        "请输出纯 JSON（不要 markdown 代码块），包含三个字段：\n"
        '  - action: 一个动作标识符（switchMode / setVoice / setSpeed / setAutoSpeak / '
        'repeat / back / navConfirm / navExit / unknown 等），未识别意图时用 "unknown"\n'
        "  - params: action 需要的参数对象（例如 {mode: 'travel'} / {voice: 'female'}），没有就给 {}\n"
        '  - reply: 给用户播报的简短回复（一句话以内），紧扣当前场景\n'
    )

    try:
        # 模型选择：此处改为纯文本模型 qwen-plus
        t_model_start = time.time()
        response = dashscope.Generation.call(
            model='qwen-plus',
            messages=[{'role': 'user', 'content': prompt}],
            result_format='message'
        )
        t_model_done = time.time()
        print(f"[parse] model done +{(t_model_done - t_start)*1000:.0f}ms (model_only={(t_model_done - t_model_start)*1000:.0f}ms) status={response.status_code}")

        if response.status_code == 200:
            ai_content = response.output.choices[0].message.content
            clean_json = ai_content.replace("```json", "").replace("```", "").strip()
            parsed = json.loads(clean_json)
            print(f"[parse] DONE total={(time.time() - t_start)*1000:.0f}ms action={parsed.get('action')} reply=\"{(parsed.get('reply') or '')[:40]}\"")
            return StandardResponse(data=parsed, request_id=req_id)
        else:
            print(f"[parse] !!! 模型失败 status={response.status_code} msg={response.message}")
            return StandardResponse(code=response.status_code, message=response.message, request_id=req_id)

    except Exception as e:
        print(f"[parse] !!! 异常 after {(time.time() - t_start)*1000:.0f}ms: {e}")
        return StandardResponse(code=500, message=str(e), request_id=req_id)