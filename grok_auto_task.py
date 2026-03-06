import os
import re
import time
import json
import requests
from browserbase import Browserbase
from playwright.sync_api import sync_playwright

# ── 环境变量 ──────────────────────────────────────────────────────
BROWSERBASE_API_KEY    = os.getenv("BROWSERBASE_API_KEY", "")
BROWSERBASE_PROJECT_ID = os.getenv("BROWSERBASE_PROJECT_ID", "")
BROWSERBASE_CONTEXT_ID = os.getenv("BROWSERBASE_CONTEXT_ID", "")
JIJYUN_WEBHOOK_URL     = os.getenv("JIJYUN_WEBHOOK_URL", "")
FEISHU_WEBHOOK_URL     = os.getenv("FEISHU_WEBHOOK_URL", "")

def get_beijing_date_cn() -> str:
    from datetime import datetime, timezone, timedelta
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y年%m月%d日")

# ════════════════════════════════════════════════════════════════
# 核心函数 1：粘贴提示词并发送
# ════════════════════════════════════════════════════════════════
def send_prompt(page, prompt_text: str, label: str):
    print(f"\n[{label}] 填写提示词...", flush=True)

    # 定位输入框
    input_box = page.wait_for_selector(
        "div[contenteditable='true'], textarea",
        timeout=30000
    )
    input_box.click()
    time.sleep(0.5)

    # 清空输入框
    page.keyboard.press("Control+a")
    page.keyboard.press("Backspace")
    time.sleep(0.3)

    # 用剪贴板 API 粘贴（速度快，不逐字输入）
    page.evaluate(f"""
        const text = {json.dumps(prompt_text)};
        const dt = new DataTransfer();
        dt.setData('text/plain', text);
        document.activeElement.dispatchEvent(
            new ClipboardEvent('paste', {{clipboardData: dt, bubbles: true}})
        );
    """)
    time.sleep(1)

    page.screenshot(path=f"before_{label}.png")

    # 点击发送按钮
    send_btn = page.wait_for_selector(
        "button[aria-label='Send message'], button[type='submit']",
        timeout=10000
    )
    send_btn.click()
    print(f"[{label}] ✅ 已发送", flush=True)
    time.sleep(4)  # 等 Grok 开始生成

# ════════════════════════════════════════════════════════════════
# 核心函数 2：等待 Grok 生成完毕，返回最新回复的 Markdown 文本
# ════════════════════════════════════════════════════════════════
def wait_and_extract(page, label: str,
                     interval: int = 3,
                     stable_rounds: int = 4,
                     max_wait: int = 120) -> str:
    """
    每隔 interval 秒检查最后一条回复的字符数。
    连续 stable_rounds 次不变 → 生成完毕。
    超过 max_wait 秒强制取结果。
    """
    print(f"[{label}] 等待 Grok 回复（最长 {max_wait}s）...", flush=True)
    last_len = -1
    stable   = 0
    elapsed  = 0

    while elapsed < max_wait:
        time.sleep(interval)
        elapsed += interval

        text = page.evaluate("""
            () => {
                const msgs = document.querySelectorAll(
                    '[data-testid="message"], .message-bubble, .response-content'
                );
                return msgs.length ? msgs[msgs.length - 1].innerText : "";
            }
        """)

        cur_len = len(text.strip())
        print(f"  {elapsed}s | 字符数: {cur_len}", flush=True)

        if cur_len == last_len and cur_len > 0:
            stable += 1
            if stable >= stable_rounds:
                print(f"[{label}] ✅ 回复完毕（连续 {stable_rounds} 次稳定）", flush=True)
                page.screenshot(path=f"done_{label}.png")
                return text.strip()
        else:
            stable   = 0
            last_len = cur_len

    print(f"[{label}] ⚠️ 超时，强制取当前内容", flush=True)
    page.screenshot(path=f"timeout_{label}.png")
    return page.evaluate("""
        () => {
            const msgs = document.querySelectorAll(
                '[data-testid="message"], .message-bubble, .response-content'
            );
            return msgs.length ? msgs[msgs.length - 1].innerText : "";
        }
    """).strip()

# ════════════════════════════════════════════════════════════════
# 阶段 A 提示词
# ════════════════════════════════════════════════════════════════
def build_prompt_a() -> str:
    return f"""今天是新加坡/北京时间 {get_beijing_date_cn()}。你现在是一台绝对客观、严格遵守底层物理限制的"X 商业情报吸尘器"。

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
# 阶段 B 提示词（在同一对话中发送，Grok 已有阶段 A 的上下文）
# ════════════════════════════════════════════════════════════════
def build_prompt_b() -> str:
    return f"""基于以上阶段 A 的全量数据，请生成今日（{get_beijing_date_cn()}）AI 吃瓜日报。

要求：
1. 从中选出 5-8 条最值得关注的信息，按重要性排序
2. 每条写 2-3 句分析（是什么 → 为什么重要 → 影响是什么）
3. 最后附"今日总结"（3-5句，宏观视角）

输出格式：**标准 Markdown**
- 用 ## 作为每条新闻的标题
- 用加粗标注关键词
- 最后的总结用 > 引用块格式

直接输出 Markdown 正文，不要任何解释。"""

# ════════════════════════════════════════════════════════════════
# 推送：飞书
# ════════════════════════════════════════════════════════════════
def push_to_feishu(markdown_text: str):
    if not FEISHU_WEBHOOK_URL:
        print("⚠️ FEISHU_WEBHOOK_URL 未配置，跳过", flush=True)
        return
    clean = markdown_text.strip()[:4000]
    payload = {"msg_type": "text", "content": {"text": clean}}
    resp = requests.post(FEISHU_WEBHOOK_URL, json=payload, timeout=30)
    print(f"飞书推送：{resp.status_code}", flush=True)

# ════════════════════════════════════════════════════════════════
# 推送：极简云（微信公众号草稿）
# ════════════════════════════════════════════════════════════════
def push_to_jijyun(markdown_text: str, title: str):
    if not JIJYUN_WEBHOOK_URL:
        print("⚠️ JIJYUN_WEBHOOK_URL 未配置，跳过", flush=True)
        return
    # 极简云接受 Markdown，转成简单 HTML
    html = markdown_text.replace("\n", "<br>")
    payload = {"title": title, "content": html, "draft": True}
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
        browser_settings={
            "context": {"id": BROWSERBASE_CONTEXT_ID, "persist": True}
        }
    )
    print(f"会话 ID：{session.id}", flush=True)

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(session.connect_url)
        ctx     = browser.contexts[0]
        page    = ctx.new_page()

        # ── Step 1：打开 Grok ─────────────────────────────────────
        print("\n[Step 1] 打开 grok.com...", flush=True)
        page.goto("https://grok.com/", wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)
        page.screenshot(path="00_opened.png")

        # ── Step 2：发送阶段 A 提示词 ─────────────────────────────
        send_prompt(page, build_prompt_a(), "阶段A")

        # ── Step 3：等待阶段 A 回复（最长 2 分钟）────────────────
        wait_and_extract(page, "阶段A", interval=3, stable_rounds=4, max_wait=120)

        # ── Step 4：发送阶段 B 提示词（同一对话，Grok 有上下文）──
        send_prompt(page, build_prompt_b(), "阶段B")

        # ── Step 5：等待阶段 B 回复（最长 2 分钟），提取 Markdown ─
        markdown_result = wait_and_extract(page, "阶段B", interval=3,
                                            stable_rounds=4, max_wait=120)
        print(f"\n最终 Markdown 长度：{len(markdown_result)} 字符", flush=True)

        # ── Step 6：提取标题（取第一个 ## 行，否则用日期）────────
        title_match = re.search(r'^#{1,2}\s+(.+)$', markdown_result, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else f"{get_beijing_date_cn()} AI 吃瓜日报"
        print(f"标题：{title}", flush=True)

        # ── Step 7：推送飞书 ──────────────────────────────────────
        print("\n[Step 7] 推送飞书...", flush=True)
        push_to_feishu(markdown_result)

        # ── Step 8：推送极简云（微信公众号草稿）──────────────────
        print("\n[Step 8] 推送极简云...", flush=True)
        push_to_jijyun(markdown_result, title)

        browser.close()

    print("\n🎉 全部完成！", flush=True)

if __name__ == "__main__":
    main()
