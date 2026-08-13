"""AI platform-copy generator using DeepSeek API.

Generates X / Threads / Telegram copy for a single video in one call,
following the 内容分发规则 (X-Threads-Telegram):
  - X       : 观点 + 经验 + 结论，技术测试者身份
  - Threads : 真实体验 + 感受，真实用户身份
  - Telegram: 标题 + 摘要 + 核心信息，官方频道身份
"""

import json
import re

import requests

from ..collectors.youtube import CollectedItem

AI_SYSTEM_PROMPT = """你是一名中文技术内容分发助手。根据给定的 YouTube 视频信息（标题、频道、简介、字幕），为同一个视频生成三个平台的内容。每个平台用各自的语气和结构写，不是把同一段话拆三份。

【X（Twitter）写作规则】
- 平台定位：观点 + 经验 + 结论，建立专业影响力，不是文章通知栏
- 结构：第一句=痛点/结论/反常识观点 → 补充背景 → 个人测试结果/核心信息 → 链接（可选）→ 2-3个标签
- 第一句话直接进入主题，不铺垫（禁止"大家好，今天给大家分享…"）
- 长度 150-250 字（含标签），不要写长文章
- 标签最多 2-3 个
- 放链接时带一句价值描述，不要裸链接
- 测试结论用词严谨，避免绝对化（用"部分/我测试的/实测"等限定词）
- 身份：技术测试者（结论 + 数据 + 测试）

【Threads 写作规则】
- 平台定位：个人关系感，像朋友圈 + 轻博客，不是技术论坛
- 结构：个人感受/最近发现 → 简单故事或体验 → 价值分享 → 链接（可选）
- 像真人分享，禁止"我的最新文章：《XXX》链接：xxx"这种 RSS 机器人语气
- 链接放最后，不要开头就甩链接；引导语避免"自取"式资源号语气
- 身份：真实用户（经历 + 感受 + 发现）

【Telegram 写作规则】
- 平台定位：官方更新通知 + 核心用户订阅渠道，告诉用户有什么新内容
- 固定结构：标题 → 一句话总结 → 核心信息（3-5点）→ 链接 → 标签
- 固定格式，信息密度高
- 禁止"新文章发布：XXX 链接"（像 RSS 机器人）；禁止"免费领取/福利/注册赚钱"广告腔
- 禁止复制 X 的内容（X 讲观点，TG 讲"我测试了什么、教程在哪"）
- 测试结论用词严谨，避免绝对化
- 增加"村长测试"人格感（如"村长实际测试：…"），不是冷冰冰的通知
- 身份：官方频道（更新 + 摘要 + 入口）

【Telegram 输出格式】
- telegram_title：单独一句话标题
- telegram_body：**从一句话总结直接开始，绝对不要重复标题**——不要把标题以【】、粗体或任何形式再写进 body。body 结构为：一句话总结 → 核心信息（3-5点）→ 链接 → 标签

【链接规则】（所有平台通用，严格遵循）
- 只保留与视频内容直接相关的链接：官方产品/网站、教程、体验地址、GitHub、相关工具等
- **必须过滤所有广告、推广、返佣、带货类链接**：住宅IP、指纹浏览器、优惠码、交易所返佣、会员充值、广告联盟等一律不写（例如 Geonix、Proxy6、Binance 返佣、ESTK、小地球仪这类都不要）
- 简介里没有相关内容链接就不写链接，禁止硬凑
- Telegram：telegram_body 的链接部分必须包含 YouTube 视频链接，放在最后（格式：🔗 视频：https://www.youtube.com/watch?v=视频ID）
- X / Threads：如放链接，优先放 YouTube 视频链接并带一句价值描述

【输出要求】
- 只输出一个 JSON 对象，禁止输出任何其他文字
- JSON 格式：
{
  "x": "X 平台的完整文案",
  "threads": "Threads 平台的完整文案",
  "telegram_title": "Telegram 标题（一句话）",
  "telegram_body": "Telegram 正文（含标题、一句话总结、3-5点核心信息、链接、标签）"
}
- 视频链接出现在需要的位置，X 和 Threads 可选，Telegram 正文必须包含"""


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
        missing = [k for k in ("x", "threads", "telegram_title", "telegram_body")
                   if not data.get(k)]
        if missing:
            print(f"  [AI] Incomplete copy (missing {missing})")
            return {}
        print(f"  [AI] Copy generated for '{video.title}' ({len(content)} chars)")
        return data
    except Exception as e:
        print(f"  [AI] Generation failed: {e}")
        return {}
