import os
import re
import time
import requests
from browserbase import Browserbase
from playwright.sync_api import sync_playwright

# ── 环境变量 ─────────────────────────────────────────────────────
JIJYUN_WEBHOOK_URL = os.getenv("JIJYUN_WEBHOOK_URL", "")
FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL", "")
SF_API_KEY         = os.getenv("SF_API_KEY", "")              # 硅基流动生图

# 多账号池：依次尝试 _1 / _2 / _3 / 无后缀
# GitHub Secrets 里加 BROWSERBASE_API_KEY_2 / BROWSERBASE_PROJECT_ID_2 等即可
def _load_bb_accounts() -> list:
    accounts = []
    for suffix in ["", "_2", "_3", "_4"]:
        key = os.getenv(f"BROWSERBASE_API_KEY{suffix}", "")
        pid = os.getenv(f"BROWSERBASE_PROJECT_ID{suffix}", "")
        ctx = os.getenv(f"BROWSERBASE_CONTEXT_ID{suffix}", "")
        if key and pid:
            accounts.append({"api_key": key, "project_id": pid, "context_id": ctx})
    return accounts

BB_ACCOUNTS  = _load_bb_accounts()
STATE_FILE   = "bb_state.json"
COOLDOWN_DAYS = 30
MAX_CONSEC   = 3       # 连续失败几次触发冷却

def load_bb_state() -> dict:
    """读取账号状态文件（连续失败次数 + 冷却截止时间）"""
    if os.path.exists(STATE_FILE):
        try:
            import json
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_bb_state(state: dict):
    """将账号状态写回文件"""
    import json
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    print(f"[状态] 已保存 {STATE_FILE}", flush=True)

def is_in_cooldown(state: dict, key: str) -> bool:
    """判断某账号是否还在冷却期"""
    from datetime import datetime
    info = state.get(key, {})
    until = info.get("cooldown_until")
    if not until:
        return False
    return datetime.utcnow().isoformat() < until

def mark_failure(state: dict, key: str) -> bool:
    """记录一次 402 失败，返回 True 表示已触发冷却"""
    from datetime import datetime, timedelta
    info = state.setdefault(key, {"consecutive_failures": 0, "cooldown_until": None})
    info["consecutive_failures"] = info.get("consecutive_failures", 0) + 1
    if info["consecutive_failures"] >= MAX_CONSEC:
        until = (datetime.utcnow() + timedelta(days=COOLDOWN_DAYS)).isoformat()
        info["cooldown_until"] = until
        print(f"[状态] 账号 ...{key} 连续失败 {MAX_CONSEC} 次，冷却至 {until[:10]}", flush=True)
        return True
    return False

def mark_success(state: dict, key: str):
    """成功后清零连续失败计数"""
    state[key] = {"consecutive_failures": 0, "cooldown_until": None}

# ── 日期工具 ─────────────────────────────────────────────────────
def get_beijing_date_cn() -> str:
    from datetime import datetime, timezone, timedelta
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y年%m月%d日")

def get_dates() -> tuple:
    from datetime import datetime, timezone, timedelta
    tz        = timezone(timedelta(hours=8))
    today     = datetime.now(tz)
    yesterday = today - timedelta(days=1)
    return today.strftime("%Y-%m-%d"), yesterday.strftime("%Y-%m-%d")

# ════════════════════════════════════════════════════════════════
# 模型选择：开启 Grok 4.20 Beta Toggle
# ════════════════════════════════════════════════════════════════
def enable_grok4_beta(page):
    print("\n[模型] 开启 Grok 4.20 测试版 Toggle...", flush=True)
    try:
        model_btn = page.wait_for_selector(
            "button:has-text('快速模式'), button:has-text('Fast'), "
            "button:has-text('自动模式'), button:has-text('Auto')",
            timeout=15000
        )
        model_btn.click()
        time.sleep(1)
        page.screenshot(path="01_model_menu.png")

        toggle = page.wait_for_selector(
            "button[role='switch'], input[type='checkbox']", timeout=8000
        )
        is_on = page.evaluate("""() => {
            const sw = document.querySelector("button[role='switch']");
            if (sw) return sw.getAttribute('aria-checked') === 'true' ||
                          sw.getAttribute('data-state') === 'checked';
            const cb = document.querySelector("input[type='checkbox']");
            return cb ? cb.checked : false;
        }""")
        if not is_on:
            toggle.click()
            print("[模型] ✅ Toggle 已开启", flush=True)
            time.sleep(1)
        else:
            print("[模型] ✅ Toggle 已是开启状态", flush=True)
        page.keyboard.press("Escape")
        time.sleep(0.5)
        page.screenshot(path="02_model_confirmed.png")
    except Exception as e:
        print(f"[模型] ⚠️ 失败，继续使用当前模型：{e}", flush=True)

# ════════════════════════════════════════════════════════════════
# 发送提示词
# ════════════════════════════════════════════════════════════════
def send_prompt(page, prompt_text: str, label: str, screenshot_prefix: str):
    print(f"\n[{label}] 填入提示词（共 {len(prompt_text)} 字符）...", flush=True)
    page.wait_for_selector("div[contenteditable='true'], textarea", timeout=30000)

    ok = page.evaluate("""(text) => {
        const el = document.querySelector("div[contenteditable='true']")
                || document.querySelector("textarea");
        if (!el) return false;
        el.focus();
        document.execCommand('selectAll', false, null);
        document.execCommand('delete', false, null);
        document.execCommand('insertText', false, text);
        return true;
    }""", prompt_text)

    if not ok:
        inp = page.query_selector("div[contenteditable='true'], textarea")
        inp.click()
        page.keyboard.press("Control+a")
        page.keyboard.press("Backspace")
        for i in range(0, len(prompt_text), 500):
            page.keyboard.type(prompt_text[i:i+500])
            time.sleep(0.2)

    time.sleep(1.5)
    page.screenshot(path=f"{screenshot_prefix}_before.png")

    # 先点一下输入框激活，确保按钮变为可见状态
    try:
        inp = page.query_selector("div[contenteditable='true'], textarea")
        if inp:
            inp.click()
            time.sleep(0.5)
    except Exception:
        pass

    # 尝试正常点击发送按钮（timeout 延长到 30s）
    clicked = False
    try:
        send_btn = page.wait_for_selector(
            "button[aria-label='Submit']:not([disabled]), "
            "button[aria-label='Send message']:not([disabled]), "
            "button[type='submit']:not([disabled])",
            timeout=30000,
            state="visible"
        )
        send_btn.click()
        clicked = True
    except Exception as e:
        print(f"[{label}] ⚠️ 常规点击失败（{e}），尝试 JS 点击...", flush=True)

    # JS 兜底：直接强制点击提交按钮
    if not clicked:
        result = page.evaluate("""() => {
            const btn = document.querySelector("button[type='submit']")
                     || document.querySelector("button[aria-label='Submit']")
                     || document.querySelector("button[aria-label='Send message']");
            if (btn) { btn.click(); return true; }
            return false;
        }""")
        if result:
            print(f"[{label}] ✅ JS 兜底点击成功", flush=True)
        else:
            raise RuntimeError(f"[{label}] ❌ 找不到发送按钮，流程中止")

    print(f"[{label}] ✅ 已发送", flush=True)
    time.sleep(5)

# ════════════════════════════════════════════════════════════════
# 等待 Grok 生成完毕
# ════════════════════════════════════════════════════════════════
def _get_last_msg(page) -> str:
    return page.evaluate("""() => {
        const msgs = document.querySelectorAll(
            '[data-testid="message"], .message-bubble, .response-content'
        );
        return msgs.length ? msgs[msgs.length - 1].innerText : "";
    }""")

def wait_and_extract(page, label: str, screenshot_prefix: str,
                     interval: int = 3, stable_rounds: int = 4,
                     max_wait: int = 120, extend_if_growing: bool = False,
                     min_len: int = 80) -> str:
    """
    min_len: 内容至少达到此长度才允许触发"稳定"判定，防止 Grok 初始短文本被误判为完成。
    """
    print(f"[{label}] 等待回复（最长 {max_wait}s，最小有效长度 {min_len}）...", flush=True)
    last_len = -1
    stable   = 0
    elapsed  = 0

    while elapsed < max_wait:
        time.sleep(interval)
        elapsed += interval
        text    = _get_last_msg(page)
        cur_len = len(text.strip())
        print(f"  {elapsed}s | 字符数: {cur_len}", flush=True)

        if cur_len == last_len and cur_len >= min_len:
            stable += 1
            if stable >= stable_rounds:
                print(f"[{label}] ✅ 回复完毕（连续 {stable_rounds} 次稳定，{cur_len} 字符）", flush=True)
                page.screenshot(path=f"{screenshot_prefix}_done.png")
                return text.strip()
        else:
            stable   = 0
            last_len = cur_len

    if extend_if_growing:
        print(f"[{label}] ⏳ 到达 {max_wait}s，仍在生成，每 5s 延长（最多 300s）...", flush=True)
        prev_len  = last_len
        ext_count = 0
        max_ext   = 60   # 60 × 5s = 300s 上限，防止无限等待
        while ext_count < max_ext:
            time.sleep(5)
            text    = _get_last_msg(page)
            cur_len = len(text.strip())
            ext_count += 1
            print(f"  延长 +{ext_count*5}s | 字符数: {cur_len}", flush=True)
            if cur_len == prev_len:
                print(f"[{label}] ✅ 已停止生成，取结果", flush=True)
                break
            prev_len = cur_len
        else:
            print(f"[{label}] ⚠️ 延长 300s 到达上限，强制取结果", flush=True)
        page.screenshot(path=f"{screenshot_prefix}_done.png")
        return text.strip()
    else:
        print(f"[{label}] ⚠️ 超时，强制取结果", flush=True)
        page.screenshot(path=f"{screenshot_prefix}_timeout.png")
        return _get_last_msg(page).strip()

# ════════════════════════════════════════════════════════════════
# 阶段 A 提示词
# ════════════════════════════════════════════════════════════════
def build_prompt_a() -> str:
    date_today, date_yesterday = get_dates()
    return f"""执行Tiered Scan模式：你现在是X商业情报深度分析师。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【Step 0：获取精确时间戳（必须首先执行！）】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
请立即用 code_execution 运行以下代码，获得 Unix 时间戳：
import time
now = int(time.time())
since_ts = now - 86400
print(f"since_time:{{since_ts}}  until_time:{{now}}")

👉 在后续所有 x_keyword_search 调用中，必须使用上面输出的
   since_time 和 until_time 参数（整数时间戳），不要使用日期字符串。
   参考时间范围：北京时间 {date_yesterday} → {date_today}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【最优策略：3层分级扫描】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Tier1（核心）：全量搜索+拉所有回复线程。
Tier2（活跃）：只看赞≥30的帖+互动分析。
Tier3（泛列）：只抓赞≥100或大事件。

【第一轮搜索：请分为3批并行调用 x_keyword_search】

批次1 (18人 - Tier 1 巨头与领袖)：
@elonmusk @sama @karpathy @demishassabis @darioamodei @OpenAI @AnthropicAI @GoogleDeepMind @GaryMarcus @xAI @AIatMeta @GoogleAI @MSFTResearch @IlyaSutskever @gregbrockman @rowancheung @clmcleod @bindureddy

批次2 (16人 - Tier 2 中文KOL与极客)：
@dotey @oran_ge @vista8 @imxiaohu @Sxsyer @K_O_D_A_D_A @tualatrix @linyunqiu @garywong @web3buidl @AI_Era @AIGC_News @jiangjiang @hw_star @mranti @nishuang

批次3 (16人 - Tier 3 顶级VC与科技媒体)：
@a16z @ycombinator @lightspeedvp @sequoia @foundersfund @eladgil @pmarca @bchesky @chamath @paulg @TheInformation @TechCrunch @verge @WIRED @Scobleizer @bentossell

【操作要求】：
1. 立即执行工具调用，对重点推文调用 x_thread_fetch 拉取完整线程、互动和吵架记录。
2. 深度分析：新观点、是否吵架、市场反馈强度。
3. 🚨 Fallback 强制规则：若带 since_time/until_time 的搜索返回 0 条结果，必须立即去掉时间参数重试同一批次，使用最近可用的帖文，绝对不可以停止或报错。
4. ⚠️ 极其重要：搜索完成后，请只输出一段 200 字以内的内部情报摘要进行数据缓存，绝对不要输出最终的日报！请告诉我："第一轮扫描完毕，等待第二轮输入。\""""


# ════════════════════════════════════════════════════════════════
# 阶段 B 提示词
# ════════════════════════════════════════════════════════════════
def build_prompt_b() -> str:
    date_today, date_yesterday = get_dates()
    return f"""执行Tiered Scan模式：这是第二轮搜索（覆盖后50个核心账号）。

⚠️ 时间范围：请复用第一轮 Step0 已计算好的 since_time / until_time 时间戳，
不要重新计算，不要使用日期字符串。参考范围：{date_yesterday} → {date_today}。

🚨 Fallback 强制规则：若带时间戳搜索返回 0 条结果，立即去掉 since_time/until_time 参数重试，
使用最近可用帖文。严禁以"无法获取时间范围数据"为由停止输出——必须用现有数据完成日报。

【第二轮搜索：请分为以下3批调用 x_keyword_search】

批次4 (18人 - Tier 1 开源新星与基础设施)：
@HuggingFace @MistralAI @Perplexity_AI @GroqInc @Cohere @TogetherCompute @runwayml @Midjourney @StabilityAI @Scale_AI @CerebrasSystems @tenstorrent @weights_biases @langchainai @llama_index @supabase @vllm_project @huggingface_hub

批次5 (16人 - Tier 2 硬件与空间计算生态)：
@nvidia @AMD @Intel @SKhynix @tsmc @magicleap @NathieVR @PalmerLuckey @ID_AA_Carmack @boz @rabovitz @htcvive @XREAL_Global @RayBan @MetaQuestVR @PatrickMoorhead

批次6 (16人 - Tier 3 顶级研究员与硬核圈子)：
@jeffdean @chrmanning @hardmaru @goodfellow_ian @feifeili @_akhaliq @promptengineer @AI_News_Tech @siliconvalley @aithread @aibreakdown @aiexplained @aipubcast @lexfridman @hubermanlab @swyx

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【🚨 最终成稿指令（严格执行）】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
完成这50人的检索后，请综合你在【第一轮】和【第二轮】收集到的所有高价值情报，挑选出最震撼的 10 个话题。
⛔ 无论工具是否有时间限制，都必须输出完整日报。"无法获取数据"不是停止的理由——用你能搜到的最新帖文完成输出。

1. 强制标识：开头必须且只能是 @@@START@@@，结尾必须且只能是 @@@END@@@。绝对禁止将定界符放在草稿中。
2. @@@END@@@ 必须是你输出的最后一行，单独成行，其后不得有任何文字、空格或说明。
3. 彻底禁用代码块：定界符内部，严禁使用三个反引号包裹任何内容。

请严格按以下模板输出定稿：

@@@START@@@
📡 AI圈极客吃瓜日报 | {date_today}

**🏰 【巨头宫斗】**

**🍉 1. 填入真实话题标题**
**🗣️ 极客原声态：**
@原推账号 | 真实姓名 (❤️赞/💬评)
> "填入中文译文，绝对不要包含任何URL链接"
**📝 捕手解码：**
• 📌 增量事实：填入对该事件的客观事实补充
• 🧠 隐性博弈：填入巨头或行业之间的暗战剖析
• 🎯 资本风向标：填入对投资或商业趋势的研判

**🍉 2. 填入下一个话题标题**
...格式同上...

---

**🇨🇳 【中文圈大瓜】**

**🍉 3. 填入话题标题**
...以此类推，覆盖 硬件/空间计算、一级市场风向 等维度，共 10 个话题...
@@@END@@@"""


# ════════════════════════════════════════════════════════════════
# 阶段 C 提示词
# ════════════════════════════════════════════════════════════════
def build_prompt_c() -> str:
    return """【阶段 C：标题 + 封面图提示词生成】

任务：从以上 10 条新闻中，找出最具冲突感、炸裂感或吃瓜属性的核心事件，完成以下两项输出：

━━━ 输出一：微信公众号文章标题 ━━━
从 10 条新闻中挑选 1～2 个最有冲击力的事件，写一个中文标题，要求：
- 极度抓眼球，制造好奇心或情绪冲击
- 风格参考：「XXX 公开撕 XXX：这场战争刚刚开始」「AI 圈最大瓜：XXX 当众打脸 XXX」
- 可用数字、破折号、感叹号增强张力
- 长度：15～30 个汉字
- 禁止：平淡陈述句、学术腔

━━━ 输出二：封面图英文提示词 ━━━
针对同一核心事件，生成文生图提示词，严格遵守：
- 风格：American comic book style，漫威/DC 面板感，bold black ink outlines，flat vibrant colors，halftone dot shading
- 构图：两股势力或角色正面对抗，表情极度夸张，动作感强烈
- 象征物：用抽象化符号代表事件主角（芯片/机器人/火箭/巨型拳头/美元等），禁止真实人脸和公司 Logo 原图
- 对话气泡：包含一句 ≤10 个英文单词的台词，点出冲突核心
- 画幅：横版 16:9，适合作为公众号封面
- 禁止：中文文字、水印、写实摄影感
- 长度：英文提示词 ≤ 150 词

━━━ 严格按以下格式输出，不要多余解释 ━━━
TITLE: <中文标题>
PROMPT: <英文提示词>

⚠️ 只输出纯文字，不要生成图片，不要调用任何工具，直接返回文本即可。"""


# ════════════════════════════════════════════════════════════════
# 调用硅基流动 SiliconFlow API 生图
# ════════════════════════════════════════════════════════════════
def generate_cover_image(prompt: str) -> str:
    """调用硅基流动 FLUX.1-schnell 生成封面图，返回图片 URL。失败返回空字符串。"""
    if not SF_API_KEY:
        print("⚠️ SF_API_KEY 未配置，跳过生图", flush=True)
        return ""
    if not prompt:
        print("⚠️ 封面图提示词为空，跳过生图", flush=True)
        return ""
    print("\n[生图] 调用硅基流动 FLUX.1-schnell 生成封面图...", flush=True)
    print(f"[生图] 提示词：{prompt[:120]}...", flush=True)
    try:
        resp = requests.post(
            "https://api.siliconflow.cn/v1/images/generations",
            headers={
                "Authorization": f"Bearer {SF_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model":      "black-forest-labs/FLUX.1-schnell",
                "prompt":     prompt,
                "n":          1,
                "image_size": "1280x720"
            },
            timeout=120
        )
        resp.raise_for_status()
        image_url = resp.json()["data"][0]["url"]
        print(f"[生图] ✅ 封面图生成成功：{image_url[:80]}...", flush=True)
        return image_url
    except Exception as e:
        print(f"[生图] ❌ 生图失败：{e}", flush=True)
        return ""


# ════════════════════════════════════════════════════════════════
# 下载图片到本地
# ════════════════════════════════════════════════════════════════
def download_image(url: str, save_path: str = "cover.png") -> bool:
    if not url:
        print("[下载] ⚠️ URL 为空，跳过下载", flush=True)
        return False
    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        with open(save_path, "wb") as f:
            f.write(resp.content)
        print(f"[下载] ✅ 已保存到 {save_path}（{len(resp.content)//1024} KB）", flush=True)
        return True
    except Exception as e:
        print(f"[下载] ❌ 下载失败：{e}", flush=True)
        return False


# ════════════════════════════════════════════════════════════════
# 上传图片到路过图床（国内永久 URL）
# ════════════════════════════════════════════════════════════════
def upload_to_imgse(image_path: str) -> str:
    """上传本地图片到路过图床（imgse.com，国内可访问），返回永久公开 URL。失败返回空字符串。"""
    if not os.path.exists(image_path):
        print(f"[图床] ⚠️ 文件不存在：{image_path}", flush=True)
        return ""
    try:
        with open(image_path, "rb") as f:
            resp = requests.post(
                "https://imgse.com/api/1/upload",
                headers={"X-API-Key": "6d207e02198a847aa98d0a2a901485a5"},
                files={"source": ("cover.jpg", f, "image/jpeg")},
                timeout=30
            )
        resp.raise_for_status()
        url = resp.json()["image"]["url"]
        print(f"[图床] ✅ 路过图床 URL：{url}", flush=True)
        return url
    except Exception as e:
        print(f"[图床] ❌ 路过图床上传失败：{e}", flush=True)
        return ""


# ════════════════════════════════════════════════════════════════
# 推送飞书
# ════════════════════════════════════════════════════════════════
def push_to_feishu(text: str, cover_url: str = ""):
    if not FEISHU_WEBHOOK_URL:
        print("⚠️ FEISHU_WEBHOOK_URL 未配置，跳过", flush=True)
        return
    msg = text
    if cover_url:
        msg = f"🖼️ 封面图：{cover_url}\n\n{msg}"
    payload = {"msg_type": "text", "content": {"text": msg}}
    resp = requests.post(FEISHU_WEBHOOK_URL, json=payload, timeout=30)
    print(f"飞书推送：{resp.status_code} | {resp.text[:80]}", flush=True)


# ════════════════════════════════════════════════════════════════
# 推送极简云（微信公众号）
# ════════════════════════════════════════════════════════════════
def push_to_jijyun(text: str, title: str, cover_url: str = ""):
    if not JIJYUN_WEBHOOK_URL:
        print("⚠️ JIJYUN_WEBHOOK_URL 未配置，跳过", flush=True)
        return
    html = text.replace("\n", "<br>")
    payload = {
        "title":        title,
        "author":       "大尉Prinski",
        "html_content": html,
        "cover_jpg":    cover_url       # 路过图床永久 URL，微信可直接抓取
    }
    resp = requests.post(JIJYUN_WEBHOOK_URL, json=payload, timeout=30)
    print(f"极简云推送：{resp.status_code} | {resp.text[:120]}", flush=True)


# ════════════════════════════════════════════════════════════════
# 提取 @@@START@@@ ... @@@END@@@ 之间的正文
# ════════════════════════════════════════════════════════════════
def extract_markdown_block(text: str) -> str:
    start = text.find("@@@START@@@")
    end   = text.find("@@@END@@@")
    if start == -1:
        return ""
    content_start = start + len("@@@START@@@")
    if end != -1 and end > start:
        return text[content_start:end].strip()
    # @@@END@@@ 缺失（被截断）：取 @@@START@@@ 之后的全部内容
    print("⚠️ @@@END@@@ 未找到，使用 @@@START@@@ 之后的全文兜底", flush=True)
    return text[content_start:].strip()


# ════════════════════════════════════════════════════════════════
# 内容质量检查
# ════════════════════════════════════════════════════════════════
def is_valid_content(text: str) -> bool:
    """判断日报内容是否有效（非空、包含关键标识符、字数足够）"""
    if not text or len(text) < 300:
        return False
    # @@@END@@@ 可能因截断缺失，不作为硬性要求
    required = ["@@@START@@@", "🍉"]
    return all(kw in text for kw in required)


# ════════════════════════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════════════════════════
def main():
    print("=" * 60, flush=True)
    print(f"🚀 AI吃瓜日报自动化任务启动", flush=True)
    print("=" * 60, flush=True)

    if not BB_ACCOUNTS:
        raise RuntimeError("❌ 未配置任何 Browserbase 账号，请检查 Secrets")

    bb_state   = load_bb_state()
    session    = None
    session_id = None
    used_acct  = None
    used_key   = None

    for i, acct in enumerate(BB_ACCOUNTS):
        key = acct["api_key"][-8:]   # 用末8位作为状态字典的 key
        print(f"\n[Browserbase] 尝试账号 #{i+1}（...{key}）", flush=True)

        # 检查冷却期
        if is_in_cooldown(bb_state, key):
            info  = bb_state.get(key, {})
            until = info.get("cooldown_until", "")[:10]
            print(f"[Browserbase] ⏸️  账号 #{i+1} 冷却中（至 {until}），跳过", flush=True)
            continue

        try:
            bb_obj       = Browserbase(api_key=acct["api_key"])
            session_opts = {"project_id": acct["project_id"]}
            if acct["context_id"]:
                session_opts["browser_settings"] = {
                    "context": {"id": acct["context_id"], "persist": True}
                }
            session    = bb_obj.sessions.create(**session_opts)
            session_id = session.id
            used_acct  = acct
            used_key   = key
            mark_success(bb_state, key)
            save_bb_state(bb_state)
            print(f"[Browserbase] ✅ 账号 #{i+1} 可用，Session ID: {session_id}", flush=True)
            break

        except Exception as e:
            err_str = str(e)
            if "402" in err_str or "Payment Required" in err_str or "minutes limit" in err_str:
                triggered = mark_failure(bb_state, key)
                save_bb_state(bb_state)
                if triggered:
                    print(f"[Browserbase] 🔴 账号 #{i+1} 已触发 {COOLDOWN_DAYS} 天冷却，切换下一个...", flush=True)
                else:
                    info = bb_state.get(key, {})
                    cnt  = info.get("consecutive_failures", 0)
                    print(f"[Browserbase] ⚠️  账号 #{i+1} 额度用完（第 {cnt}/{MAX_CONSEC} 次），切换下一个...", flush=True)
                continue
            else:
                save_bb_state(bb_state)
                raise

    if not session_id:
        raise RuntimeError("❌ 所有 Browserbase 账号均不可用（额度耗尽或冷却中），请充值或新增账号")

    raw_b_text   = ""
    cover_prompt = ""
    cover_title_c = ""

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(
            f"wss://connect.browserbase.com?apiKey={used_acct['api_key']}"
            f"&sessionId={session_id}"
        )
        context = browser.contexts[0]
        page    = context.pages[0] if context.pages else context.new_page()

        # Step 1：打开 Grok
        print("\n打开 grok.com...", flush=True)
        page.goto("https://grok.com", wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)
        page.screenshot(path="00_opened.png")

        # Step 2：开启 Grok 4.20 Beta
        enable_grok4_beta(page)

        # Step 3：阶段 A（第一轮扫描，50个账号前半段）
        send_prompt(page, build_prompt_a(), "阶段A", "03_stage_a")
        print("[阶段A] ⏳ 强制等待 90s，确保 Grok 工具调用完成...", flush=True)
        time.sleep(90)
        wait_and_extract(page, "阶段A", "03_stage_a",
                         interval=3, stable_rounds=4, max_wait=120,
                         extend_if_growing=True, min_len=100)

        # Step 4：阶段 B（第二轮扫描 + 成稿）
        send_prompt(page, build_prompt_b(), "阶段B", "04_stage_b")
        print("[阶段B] ⏳ 强制等待 60s，等待工具调用启动...", flush=True)
        time.sleep(60)
        raw_b_text = wait_and_extract(page, "阶段B", "04_stage_b",
                                      interval=5, stable_rounds=3, max_wait=200,
                                      extend_if_growing=True, min_len=1000)
        print(f"\n阶段B 内容长度：{len(raw_b_text)} 字符", flush=True)

        # Step 5：阶段 C（标题 + 封面图提示词）
        send_prompt(page, build_prompt_c(), "阶段C", "05_stage_c")
        cover_raw = wait_and_extract(page, "阶段C", "05_stage_c",
                                     interval=3, stable_rounds=3, max_wait=60,
                                     extend_if_growing=False)

        # 解析 TITLE / PROMPT
        title_match  = re.search(r"TITLE[:：]\s*(.+)", cover_raw)
        prompt_match = re.search(r"PROMPT[:：]\s*([\s\S]+)", cover_raw)
        cover_title_c = title_match.group(1).strip()  if title_match  else ""
        cover_prompt  = prompt_match.group(1).strip() if prompt_match else ""
        if not cover_prompt:
            print("[阶段C] ⚠️ 未找到 PROMPT:，封面图跳过生成", flush=True)
        print(f"\n[阶段C] 动态标题：{cover_title_c}", flush=True)
        print(f"[阶段C] 封面图提示词：{cover_prompt[:100]}...", flush=True)

        browser.close()

    # ── 内容质量守卫 ─────────────────────────────────────────────
    # 始终用 raw_b_text 做质量判断（最完整），extract 结果仅用于推送
    if not is_valid_content(raw_b_text):
        print("\n❌ 日报内容质量不达标（内容过短或缺少标识符），终止推送。", flush=True)
        print(f"   原始内容前200字：{raw_b_text[:200]}", flush=True)
        raise SystemExit(1)

    final_markdown = extract_markdown_block(raw_b_text)
    if not final_markdown:
        final_markdown = raw_b_text   # 实在提取不到定界符，用全文兜底

    # ── Step 6：硅基流动生图 ────────────────────────────────────
    cover_url = generate_cover_image(cover_prompt)
    download_image(cover_url, "cover.png")

    # ── Step 7：标题（优先阶段C动态标题，fallback 正文标题）────
    if cover_title_c:
        title = f"{get_beijing_date_cn()} | {cover_title_c}"
    else:
        title_match = re.search(r'AI圈极客吃瓜日报[^\n]*', final_markdown)
        title = title_match.group(0).strip() if title_match else \
                f"{get_beijing_date_cn()} AI圈极客吃瓜日报"
    print(f"\n标题：{title}", flush=True)

    # ── Step 8：上传封面图到路过图床 ────────────────────────────
    imgse_url       = upload_to_imgse("cover.png")
    final_cover_url = imgse_url if imgse_url else cover_url
    print(f"封面图最终 URL：{final_cover_url[:80] if final_cover_url else '无'}", flush=True)

    # ── Step 9：推送飞书 ─────────────────────────────────────────
    print("\n推送飞书...", flush=True)
    push_to_feishu(final_markdown, final_cover_url)

    # ── Step 10：推送极简云（微信公众号）───────────────────────
    print("推送极简云...", flush=True)
    push_to_jijyun(final_markdown, title, final_cover_url)

    print("\n🎉 全部完成！", flush=True)


if __name__ == "__main__":
    main()
