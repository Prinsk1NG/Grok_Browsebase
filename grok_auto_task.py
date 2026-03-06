import os
import re
import time
import requests
from browserbase import Browserbase
from playwright.sync_api import sync_playwright

BROWSERBASE_API_KEY    = os.getenv("BROWSERBASE_API_KEY", "")
BROWSERBASE_PROJECT_ID = os.getenv("BROWSERBASE_PROJECT_ID", "")
BROWSERBASE_CONTEXT_ID = os.getenv("BROWSERBASE_CONTEXT_ID", "")
JIJYUN_WEBHOOK_URL     = os.getenv("JIJYUN_WEBHOOK_URL", "")
FEISHU_WEBHOOK_URL     = os.getenv("FEISHU_WEBHOOK_URL", "")
XAI_API_KEY            = os.getenv("XAI_API_KEY", "")

def get_beijing_date_cn() -> str:
    from datetime import datetime, timezone, timedelta
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y年%m月%d日")

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

    send_btn = page.wait_for_selector(
        "button[aria-label='Submit']:not([disabled]), "
        "button[aria-label='Send message']:not([disabled]), "
        "button[type='submit']:not([disabled])",
        timeout=15000
    )
    send_btn.click()
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
                     max_wait: int = 120, extend_if_growing: bool = False) -> str:
    print(f"[{label}] 等待回复（最长 {max_wait}s）...", flush=True)
    last_len = -1
    stable   = 0
    elapsed  = 0

    while elapsed < max_wait:
        time.sleep(interval)
        elapsed += interval
        text    = _get_last_msg(page)
        cur_len = len(text.strip())
        print(f"  {elapsed}s | 字符数: {cur_len}", flush=True)

        if cur_len == last_len and cur_len > 0:
            stable += 1
            if stable >= stable_rounds:
                print(f"[{label}] ✅ 回复完毕", flush=True)
                page.screenshot(path=f"{screenshot_prefix}_done.png")
                return text.strip()
        else:
            stable   = 0
            last_len = cur_len

    if extend_if_growing:
        print(f"[{label}] ⏳ 到达 {max_wait}s，仍在生成，每 5s 延长...", flush=True)
        prev_len  = last_len
        ext_count = 0
        while True:
            time.sleep(5)
            text    = _get_last_msg(page)
            cur_len = len(text.strip())
            ext_count += 1
            print(f"  延长 +{ext_count*5}s | 字符数: {cur_len}", flush=True)
            if cur_len == prev_len:
                print(f"[{label}] ✅ 已停止生成，取结果", flush=True)
                break
            prev_len = cur_len
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
    date_string = get_beijing_date_cn()
    return f"""今天是新加坡/北京时间 {date_string}。你现在是一台绝对客观、严格遵守底层物理限制的"X 商业情报吸尘器"。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【阶段 0：前置时间计算（必须首先执行！）】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
请立即使用 code_execution 工具运行以下 Python 代码获取时间戳：
```python
import time
now = int(time.time())
print(f"since_time:{{now - 86400}} until_time:{{now}}")
```
👉 获取后必须将其写入下方搜索语句，并在日志中输出对应的 UTC 时间。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【阶段 A：无差别原始拉取 + 输出前过滤】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
第一步（拉取）：按以下批次执行全量拉取。
第二步（过滤）：对每条记录执行"三级过滤铁律"（过滤空壳、无关主题、无意义评论）。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【约束 1：10 批次并行拉取列表】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
批次 1（顶层巨头）：@sama, @lexfridman, @elonmusk, @karpathy, @ylecun, @sundarpichai, @satyanadella, @darioamodei, @gdb, @demishassabis, @geoffreyhinton, @jeffdean
批次 2（芯片与算力）：@jensenhuang, @LisaSu, @anshelsag, @IanCutress, @PatrickMoorhead, @ServeTheHome, @dylan522p, @SKHynix, @TSMC, @RajaXg
批次 3（AI 硬件与新物种）：@rabbit_inc, @Humane, @BrilliantLabsAR, @Frame_AI, @LimitlessAI, @Plaud_Official, @TabAl_HQ, @OasisAI, @Friend_AI, @AImars
批次 4（空间计算与 XR）：@ID_AA_Carmack, @boztank, @LumusVision, @XREAL_Global, @vitureofficial, @magicleap, @KarlGuttag, @NathieVR, @SadieTeper, @lucasrizzo
批次 5（硅谷观察家）：@rowancheung, @bentossell, @p_millerd, @venturebeat, @TechCrunch, @TheInformation, @skorusARK, @william_yang, @backlon, @vladsavov
批次 6（中文圈核心 A）：@dotey, @oran_ge, @waylybaye, @tualatrix, @K_O_D_A_D_A, @Sun_Zhuo, @Xander0214, @wong2_x, @imxiaohu, @vista8, @1moshu, @qiushui_ai
批次 7（中文圈核心 B）：@xiaogang_ai, @AI_Next_Gen, @MoonshotAI, @01AI_Official, @ZhipuAI, @DeepSeek_AI, @Baichuan_AI, @MiniMax_AI, @StepFun_AI, @Kimi_AI
批次 8（开发者与极客）：@stroughtonsmith, @_inside, @ali_heston, @bigclivedotcom, @chr1sa, @kevin_ashton, @DanielElizalde, @antgrasso, @Scobleizer, @GaryMarcus
批次 9（一级市场捕手）：@a16z, @sequoia, @ycombinator, @GreylockVC, @Accel, @Benchmark, @foundersfund, @IndexVentures, @LightspeedVP, @GeneralCatalyst
批次 10（研究与前沿）：@OpenAI, @GoogleDeepMind, @AnthropicAI, @MistralAI, @HuggingFace, @StabilityAI, @Midjourney, @Perplexity_AI, @GroqInc, @CerebrasSystems

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【输出格式：LLM 机读压缩行】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
列顺序固定：handle | 动作 | 对象 | 赞 | 评 | 内容直译 | 评论
字段规则：动作 P=原创, RT=转发, Q=引用, R=回复。指代不明请标注 [原推含图/链接，指代不明]。

最后附上单行检索日志。"""

# ════════════════════════════════════════════════════════════════
# 阶段 B 提示词
# ════════════════════════════════════════════════════════════════
def build_prompt_b() -> str:
    date_string = get_beijing_date_cn()
    return f"""【阶段 B：主编排版与深度解码】

你现在的角色是"AI 圈的顶级观察员与吃瓜课代表"。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【🧠 第一步：深度思考与打草稿】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
为了保证日报品质，请先进行深度的"思维链"分析。
1. 挑选最有价值的 10 个话题。
2. 仔细推敲"隐性博弈"和"资本风向标"的底层逻辑。
3. 思考过程你可以畅所欲言，但必须放在【最终成稿】之前。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【🚨 第二步：最终机器输出规范（严格执行）】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
完成思考后，请严格按以下格式输出最终定稿：

1. 强制机器抓取标识（极其重要）：为了防止你的排版被网页吃掉，你必须将所有最终输出内容，**完整包裹在一个 markdown 代码块中**（即使用 ```markdown 开始，``` 结束）。在代码块之外，不准说任何多余的废话！
   ⚠️ 绝对禁止将这对代码块符号放在任何"思考"或"草稿"段落中。
2. 代码块内防嵌套：在 ```markdown 内部的正文中，严禁再嵌套使用三个反引号包裹任何局部内容。
3. 绝不保留占位说明：下面模板中的括号提示语，请替换为你的真实分析，绝不能把"在此处填写"原样输出！

```markdown
📡 AI圈极客吃瓜日报 | {date_string}

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
...以此类推，完成后续维度共 10 个话题...
```"""

# ════════════════════════════════════════════════════════════════
# 阶段 C 提示词：生成美式漫画封面图提示词
# ════════════════════════════════════════════════════════════════
def build_prompt_c() -> str:
    return """【阶段 C：封面图提示词生成】

任务：从以上 10 条新闻中，找出冲突感最强、最具 drama 或吃瓜属性的核心事件。
如果没有明显撕逼，就从中提炼出最具戏剧性张力的角度自由发挥。

请生成一段英文文生图提示词，严格遵守以下要求：
- 风格：American comic book style，漫威/DC 面板感，bold black ink outlines，flat vibrant colors，halftone dot shading
- 构图：两股势力或角色正面对抗，表情极度夸张，动作感强烈
- 象征物：用抽象化符号代表事件主角（芯片/机器人/火箭/巨型拳头/美元等），禁止使用真实人脸和公司 Logo 原图
- 对话气泡：包含一句 ≤10 个英文单词的台词，点出冲突核心
- 画幅：横版 16:9，适合作为公众号封面
- 禁止：中文文字、水印、写实摄影感
- 长度：英文提示词 ≤ 150 词

只输出英文提示词本身，不要任何解释和前缀。"""

# ════════════════════════════════════════════════════════════════
# 调用 xAI Aurora API 生图
# ════════════════════════════════════════════════════════════════
def generate_cover_image(prompt: str) -> str:
    """调用 xAI Aurora 生成图片，返回图片 URL。失败返回空字符串。"""
    if not XAI_API_KEY:
        print("⚠️ XAI_API_KEY 未配置，跳过生图", flush=True)
        return ""

    print("\n[生图] 调用 xAI Aurora 生成封面图...", flush=True)
    print(f"[生图] 提示词：{prompt[:120]}...", flush=True)

    try:
        resp = requests.post(
            "https://api.x.ai/v1/images/generations",
            headers={
                "Authorization": f"Bearer {XAI_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "aurora",
                "prompt": prompt,
                "n": 1,
                "size": "1792x1024"   # 16:9 横版封面
            },
            timeout=60
        )
        resp.raise_for_status()
        image_url = resp.json()["data"][0]["url"]
        print(f"[生图] ✅ 封面图生成成功：{image_url[:80]}...", flush=True)
        return image_url
    except Exception as e:
        print(f"[生图] ❌ 生图失败：{e}", flush=True)
        return ""

# ════════════════════════════════════════════════════════════════
# 下载图片到本地（用于 Artifact 上传）
# ════════════════════════════════════════════════════════════════
def download_image(url: str, save_path: str = "cover.png") -> bool:
    if not url:
        return False
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        with open(save_path, "wb") as f:
            f.write(r.content)
        print(f"[生图] ✅ 封面图已下载：{save_path}", flush=True)
        return True
    except Exception as e:
        print(f"[生图] ⚠️ 下载失败：{e}", flush=True)
        return False

# ════════════════════════════════════════════════════════════════
# 提取 ```markdown 代码块
# ════════════════════════════════════════════════════════════════
def extract_markdown_block(text: str) -> str:
    match = re.search(r'```markdown\s*([\s\S]+?)\s*```', text)
    if match:
        print("✅ 成功提取 markdown 代码块", flush=True)
        return match.group(1).strip()
    print("⚠️ 未找到 ```markdown 块，返回原始文本", flush=True)
    return text.strip()

# ════════════════════════════════════════════════════════════════
# 推送：飞书（附带封面图 URL）
# ════════════════════════════════════════════════════════════════
def push_to_feishu(text: str, cover_url: str = ""):
    if not FEISHU_WEBHOOK_URL:
        print("⚠️ FEISHU_WEBHOOK_URL 未配置，跳过", flush=True)
        return
    msg = text[:3800]
    if cover_url:
        msg = f"🖼️ 封面图：{cover_url}\n\n{msg}"
    payload = {"msg_type": "text", "content": {"text": msg}}
    resp = requests.post(FEISHU_WEBHOOK_URL, json=payload, timeout=30)
    print(f"飞书推送：{resp.status_code}", flush=True)

# ════════════════════════════════════════════════════════════════
# 推送：极简云（正文 + 封面图 URL）
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
        "cover_jpg":    cover_url
    }
    resp = requests.post(JIJYUN_WEBHOOK_URL, json=payload, timeout=30)
    print(f"极简云推送：{resp.status_code}", flush=True)

# ════════════════════════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════════════════════════
def main():
    if not BROWSERBASE_CONTEXT_ID:
        print("❌ 未找到 BROWSERBASE_CONTEXT_ID，请先配置 GitHub Secrets", flush=True)
        raise SystemExit(1)

    print("\n初始化 Browserbase 会话...", flush=True)
    client  = Browserbase(api_key=BROWSERBASE_API_KEY)
    session = client.sessions.create(
        project_id=BROWSERBASE_PROJECT_ID,
        browser_settings={"context": {"id": BROWSERBASE_CONTEXT_ID, "persist": True}}
    )
    print(f"会话 ID：{session.id}", flush=True)

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(session.connect_url)
        ctx     = browser.contexts[0]
        page    = ctx.new_page()

        # Step 1：打开 Grok
        print("\n[Step 1] 打开 grok.com...", flush=True)
        page.goto("https://grok.com/", wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)
        page.screenshot(path="00_opened.png")

        # Step 2：开启 Grok 4.2 Beta
        enable_grok4_beta(page)

        # Step 3：阶段 A（最长 120s）
        send_prompt(page, build_prompt_a(), "阶段A", "03_stage_a")
        wait_and_extract(page, "阶段A", "03_stage_a",
                         interval=3, stable_rounds=4, max_wait=120,
                         extend_if_growing=False)

        # Step 4：阶段 B（最长 200s，超时若仍在吐字则延长）
        send_prompt(page, build_prompt_b(), "阶段B", "04_stage_b")
        raw_b = wait_and_extract(page, "阶段B", "04_stage_b",
                                  interval=3, stable_rounds=4, max_wait=200,
                                  extend_if_growing=True)
        final_markdown = extract_markdown_block(raw_b)
        print(f"\n阶段B 内容长度：{len(final_markdown)} 字符", flush=True)

        # Step 5：阶段 C（生成封面图提示词，最长 60s，轻量快速）
        send_prompt(page, build_prompt_c(), "阶段C", "05_stage_c")
        cover_prompt_raw = wait_and_extract(page, "阶段C", "05_stage_c",
                                             interval=3, stable_rounds=3, max_wait=60,
                                             extend_if_growing=False)
        # 清理多余前缀（Grok 有时会加"Sure, here is..."）
        cover_prompt = cover_prompt_raw.strip().split("\n")[-1] \
                       if "\n" in cover_prompt_raw else cover_prompt_raw.strip()
        print(f"\n封面图提示词：{cover_prompt[:100]}...", flush=True)

        browser.close()

    # Step 6：调用 xAI Aurora 生图（Browserbase Session 已关闭，节省时间）
    cover_url = generate_cover_image(cover_prompt)
    download_image(cover_url, "cover.png")

    # Step 7：提取标题
    title_match = re.search(r'AI圈极客吃瓜日报[^\n]*', final_markdown)
    title = title_match.group(0).strip() if title_match else \
            f"{get_beijing_date_cn()} AI圈极客吃瓜日报"
    print(f"\n标题：{title}", flush=True)

    # Step 8：推送飞书
    print("\n推送飞书...", flush=True)
    push_to_feishu(final_markdown, cover_url)

    # Step 9：推送极简云（附封面图）
    print("推送极简云...", flush=True)
    push_to_jijyun(final_markdown, title, cover_url)

    print("\n🎉 全部完成！", flush=True)

if __name__ == "__main__":
    main()
