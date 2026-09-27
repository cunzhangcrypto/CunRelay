"""AI platform-copy generator using DeepSeek API.

Generates X / Threads / Telegram / Reddit copy for a single video in one call,
following the 内容分发规则:
  - X       : 观点 + 经验 + 结论，技术测试者身份
  - Threads : 真实体验 + 感受，真实用户身份
  - Telegram: 标题 + 摘要 + 核心信息，官方频道身份
  - Reddit  : 论坛讨论帖（英文），独立测试者身份
"""

import json
import re

import requests

from ..collectors.youtube import CollectedItem
from ..publishers.twitter import weighted_len

# X 纯中文实际上限约 140 字（加权 280）。用户要求严格小于 130 字，
# 留余量给标签/链接/emoji。超限就让 AI 重写，绝不靠发布端截断。
MAX_X_CHARS = 130  # 硬上限：生成/重写后必须 < 130 字

AI_SYSTEM_PROMPT = """你是一名多平台中文/英文内容分发助手。根据给定的 YouTube 视频信息（标题、频道、简介、字幕），为同一个视频生成四个平台的内容——Telegram / X / Threads 用中文，Reddit 全文用英文。每个平台用各自的语气和结构写，不是把同一段话翻译四份。

【X（Twitter）写作规则】（中文）
- 平台定位：观点 + 经验 + 结论。推文本身必须有独立阅读价值，不点链接也能看懂核心信息
- 结构：第一句=最反常识/信息量最大的结论（钩子，禁止任何铺垫）→ 2-3 句支撑细节或实测数据 → 可选一句开放性提问引导回复
- 正文里不放任何链接（视频链接由发布机制放到首条评论）
- 长度 110-125 个中文字（含标签），严格小于 130 字。注意 X 按"加权字符"计长度：中文/emoji 每个算 2 字符，超过会被 X 拒绝。必须写成完整通顺的短推文，禁止"只写开头、后半全靠截断"的长文——推文要能独立成立
- 标签 1-2 个放末尾，用高流量精准标签（如 #AI工具）
- 每 3 条至少 1 条以提问结尾，引导评论区互动
- 结论用词严谨（"我实测/部分场景"限定），避免绝对化
- 身份：技术测试者（结论 + 数据 + 测试）

【Threads 写作规则】（中文）
- 平台定位：个人关系感，像朋友圈 + 轻博客，不是技术论坛
- 结构：个人感受/最近发现 → 简单故事或体验 → 价值分享 → 链接（可选）
- 像真人分享，禁止"我的最新文章：《XXX》链接：xxx"这种 RSS 机器人语气
- 链接放最后，不要开头就甩链接；引导语避免"自取"式资源号语气
- 身份：真实用户（经历 + 感受 + 发现）

【Telegram 写作规则】（中文）
- 平台定位：官方更新通知 + 核心用户订阅渠道，告诉用户有什么新内容
- 固定结构：标题 → 一句话总结 → 核心信息（3-5点）→ 链接 → 标签
- 固定格式，信息密度高
- 禁止"新视频发布：XXX 链接"（像 RSS 机器人）；禁止"免费领取/福利/注册赚钱"广告腔
- 禁止复制 X 的内容（X 讲观点，TG 讲"我测试了什么、教程在哪"）
- 测试结论用词严谨，避免绝对化
- 增加"村长测试"人格感（如"村长实际测试：…"），不是冷冰冰的通知
- 身份：官方频道（更新 + 摘要 + 入口）

【Telegram 输出格式】
- telegram_title：单独一句话标题
- telegram_body：**从一句话总结直接开始，绝对不要重复标题**——不要把标题以【】、粗体或任何形式再写进 body。body 结构为：一句话总结 → 核心信息（3-5点）→ 链接 → 标签

【Reddit 写作规则】（**全文英文输出**，self-post 讨论帖）
- 平台定位：论坛讨论帖，目标是引发高质量讨论，不是视频通知栏或广告
- 标题（reddit_title，≤70 chars）：具体、有悬念或反常识结论；绝对禁止 "Check out my new video" / "New video:" / "Full guide:" 这类推广开头；标题本身必须包含讨论点
- 正文（reddit_body，200-500 words）：
  1. 开头 1-2 句：用第一人称讲"为什么我做了这个测试/我为什么关心这个话题"（I've been testing... / Recently I noticed...）
  2. 中间 2-3 段：列出 2-4 个具体观察或测试结果，每段 1-2 句；用 "What I found" / "Here's the thing" / "One thing that surprised me" 等自然过渡
  3. 讨论引导句（关键）：结尾前提一个开放性问题让社区讨论，如 "What's been your experience with this?" 或 "Am I missing something here?"
  4. 最后一行放视频参考链接，格式固定：`Full video walkthrough: <YouTube_URL>`，前面加一句说明为什么值得看
- 语气：独立测试者（independent tester / researcher），谦逊、严谨，不用自夸，不出现 "I'm a content creator" 这类创作者自称
- 绝对不要：重复标题在正文、开头第一句就放 YouTube 链接、写 "Please subscribe" / "Like and share" 这类 YouTube 话术、用 emoji 或 hashtag

【链接规则】（所有平台通用，严格遵循）
- 只保留与视频内容直接相关的链接：官方产品/网站、教程、体验地址、GitHub、相关工具等
- **必须过滤所有广告、推广、返佣、带货类链接**：住宅IP、指纹浏览器、优惠码、交易所返佣、会员充值、广告联盟等一律不写（例如 Geonix、Proxy6、Binance 返佣、ESTK、小地球仪这类都不要）
- 简介里没有相关内容链接就不写链接，禁止硬凑
- Telegram：telegram_body 的链接部分必须包含 YouTube 视频链接，放在最后（格式：🔗 视频：https://www.youtube.com/watch?v=视频ID）
- X / Threads：如放链接，优先放 YouTube 视频链接并带一句价值描述
- Reddit：正文最后一行固定放视频参考链接，格式见上面 Reddit 规则

【输出要求】
- 只输出一个 JSON 对象，禁止输出任何其他文字、解释或代码外注释
- JSON 格式：
{
  "x": "X 平台的完整中文文案（观点+结论+标签）",
  "threads": "Threads 平台的完整中文文案（真实体验+分享）",
  "reddit_title": "Reddit 英文标题（≤70字符，讨论感强，无广告腔）",
  "reddit_body": "Reddit 英文正文（200-500字，讨论帖结构，结尾问题+视频链接）",
  "telegram_title": "Telegram 中文一句话标题",
  "telegram_body": "Telegram 中文正文（一句话总结+3-5点+链接+标签，不重复标题）"
}
- 视频链接出现在需要的位置，X 和 Threads 可选，Telegram 正文、Reddit 正文 必须包含"""


def build_user_prompt(video: CollectedItem, transcript: str, max_transcript_chars: int) -> str:
    """Build the user prompt from a collected video."""
    desc = video.description[:800].replace("\n", " ") if video.description else "(无简介)"
    body = transcript[:max_transcript_chars] if transcript else "(无字幕，请仅基于标题和简介创作)"
    lines = [
        f"视频标题：{video.title}",
        f"频道：{video.source_name}",
        f"视频链接：{video.url}",
        f"发布时间：{video.published.strftime('%Y-%m-%d %H:%M UTC') if video.published else '未知'}",
        f"视频简介：{desc}",
        "",
        f"视频字幕（截取前 {max_transcript_chars} 字）：",
        body,
    ]
    return "\n".join(lines)


def _parse_json(content: str) -> dict | None:
    """Parse JSON from an LLM response, tolerating code fences."""
    text = content.strip()
    # Strip ```json ... ``` fences
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fallback: locate first {...} block
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def generate_platform_copy(
    video: CollectedItem,
    transcript: str,
    api_key: str,
    model: str = "deepseek-chat",
    api_base: str = "https://api.deepseek.com",
    timeout: int = 180,
    max_transcript_chars: int = 6000,
) -> dict:
    """Generate {x, threads, telegram_title, telegram_body} for one video.

    Returns an empty dict on failure (caller should log and skip).
    """
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": AI_SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(video, transcript, max_transcript_chars)},
        ],
        "temperature": 0.7,
        "max_tokens": 2048,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(
            f"{api_base.rstrip('/')}/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        data = _parse_json(content) or {}
        # 只要求活动平台（Telegram + X）的字段必须齐全；reddit/threads
        # 未启用时不强制，避免 AI 偶发漏掉它们而拖累整个视频都发不出去。
        missing = [k for k in ("x", "telegram_title", "telegram_body")
                   if not data.get(k)]
        if missing:
            print(f"  [AI] Incomplete copy (missing {missing})")
            return {}
        # X 字数保障：AI 写完校验，达到 130 字就重写，绝不靠发布端截断
        if weighted_len(data.get("x", "")) >= MAX_X_CHARS:
            rewritten = _rewrite_x_copy(
                data["x"], video, api_key, model, api_base, timeout)
            if rewritten:
                print(f"  [AI] X copy over limit, rewritten "
                      f"({weighted_len(data['x'])} -> {weighted_len(rewritten)} weighted)")
                data["x"] = rewritten
            else:
                print("  [AI] X rewrite failed, keeping original")
        print(f"  [AI] Copy generated for '{video.title}' ({len(content)} chars)")
        return data
    except Exception as e:
        print(f"  [AI] Generation failed: {e}")
        return {}


def _chat(payload: dict, api_key: str, api_base: str, timeout: int) -> str | None:
    """Send one chat completion request, return the assistant text or None."""
    try:
        resp = requests.post(
            f"{api_base.rstrip('/')}/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"  [AI] chat failed: {e}")
        return None


def _rewrite_x_copy(
    overlong: str,
    video: CollectedItem,
    api_key: str,
    model: str,
    api_base: str,
    timeout: int,
) -> str | None:
    """在字数超限时，让 AI 把 X 推文重写成一版字数内的完整短推文。

    只输出 JSON，取其中的 "x" 字段；返回 None 表示重写失败。
    """
    prompt = (
        "上一条 X 推文字数超限，无法发布。请只精简“X 推文”这一条，"
        "保留下划线的核心信息和语气，调整到官方字符限制内。\n"
        f"【必须严格小于 {MAX_X_CHARS} 字（中文字符计数，含标签）】，必须完整通顺，"
        "不要省略成半句或只留开头。\n"
        f"原推文：\n{overlong}\n\n"
        f"视频标题：{video.title}\n视频链接：{video.url}\n\n"
        "只输出一个 JSON 对象：{\"x\": \"精简后的完整 X 推文\"}"
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是中文内容精简助手，只输出 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.5,
        "max_tokens": 800,
    }
    content = _chat(payload, api_key, api_base, timeout)
    if not content:
        return None
    parsed = _parse_json(content)
    rewritten = (parsed or {}).get("x")
    if not rewritten:
        return None
    # 重写后仍 ≥130 字就拒绝，保留原文（宁可重试也不用截断）
    if weighted_len(rewritten) >= MAX_X_CHARS:
        return None
    return rewritten.strip()
