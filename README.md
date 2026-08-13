# CunRelay

> AI 驱动的全自动内容发布工具 —— 监控 YouTube 频道，AI 生成各平台专属文案，自动分发到 Telegram / X / Threads。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## 它做什么

一条流水线搞定「发现 → 创作 → 分发 → 记录」：

```
YouTube 频道 RSS 发现新视频
      ↓ 抓取字幕 + 封面
      ↓ AI（DeepSeek）生成各平台专属文案（Telegram / X / Threads）
      ↓ 自动发布 + 失败重试（最多重试 1 次）
      ↓ 每次发布结果同步到 Google Sheets
      ↓ 在线控制台 UI 实时展示（统计 / 队列 / 日志）
```

全程云端运行：监控、UI、记录都在线上，不依赖本地。

## 功能特性

- **YouTube 频道监控**：RSS 自动发现新视频，支持 channel_id / handle
- **AI 文案生成**：DeepSeek 按平台特性生成专属文案（Telegram 标题+要点、X 观点+标签、Threads 真实体验），自动过滤广告/推广链接，末尾必带视频链接
- **多平台分发**：Telegram（带封面图）、X (Twitter)、Threads（API 开放后启用）
- **每轮每频道只发最新 1 条**：避免刷屏
- **平台补发**：后启用/新配置的平台，会自动补齐 72 小时窗口内未发布过的视频（已发布的平台幂等跳过，绝不重复）
- **失败重试**：首发失败仅重试 1 次，间隔 30 分钟，避免平台限流
- **Google Sheets 记录**：每次发布成功/失败自动写入线上表格
- **在线控制台 UI**：科技感浅色主题，统计卡片带平台细分、发布队列/日志分页（每页 8 条）、监控源与表格一键跳转
- **智能自动刷新**：页面只在流水线运行窗口内轮询，检测到当天新快照立即停止，其余时间零请求
- **开源友好**：cron / 时区 / 关注列表全部可配置，任何时区部署者开箱即用

## 架构

| 组件 | 技术 | 说明 |
|---|---|---|
| 定时流水线 | GitHub Actions | 每天定时跑 collect + send + export + 部署，支持手动触发 |
| 数据库 | SQLite（CI Cache 持久化） | 去重、队列、发布记录，跨天保留 |
| 在线 UI | Cloudflare Pages（纯静态） | `public/` 目录，数据来自 `data.json` |
| 记录 | Google Sheets | 与 UI 互为备份 |

## 目录结构

```
cunrelay/
  collectors/     # YouTube 采集（RSS、字幕、封面）
  ai/             # DeepSeek 文案生成 + 链接过滤
  publishers/     # Telegram / X / Threads 发布
  scheduler/      # 队列编排、错峰、重试
  sheets/         # Google Sheets 同步
  storage/        # SQLite 存取
  web/            # 本地开发 UI 服务
  export.py       # 导出 data.json（线上 UI 数据源）
config/config.yaml   # 主配置
config/credentials.json  # Google 服务账号（不入库）
public/              # 前端静态文件（UI）
.github/workflows/relay.yml  # CI 流水线
```

## 快速开始（本地运行）

> 本地验证跑通后，再按下方「部署上线」搬到线上。

### 1. 环境准备

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)（依赖管理）

```bash
git clone https://github.com/<your-name>/CunRelay.git
cd CunRelay
uv sync
```

### 2. 配置密钥

```bash
cp .env.example .env
```

编辑 `.env`，至少填写：

| 变量 | 说明 |
|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek 密钥（[platform.deepseek.com](https://platform.deepseek.com)） |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token（找 [@BotFather](https://t.me/BotFather) 创建） |
| `TELEGRAM_CHAT_ID` | 目标频道 ID（形如 `-100xxxxxxxxxx`） |
| `GOOGLE_SHEETS_CREDENTIALS` | Google 服务账号 JSON（单行），或留空用 `config/credentials.json` |

X 发布密钥（OAuth 1.0a 四件套）可暂留空，未配置时自动跳过该平台。

### 3. 配置 Google Sheets

1. [Google Cloud Console](https://console.cloud.google.com) 创建服务账号，下载 JSON 放到 `config/credentials.json`
2. 把服务账号邮箱（形如 `xxx@xxx.iam.gserviceaccount.com`）添加为表格的**编辑者**
3. 在 `config/config.yaml` 设置 `sheets.spreadsheet_id`

### 4. 修改主配置

`config/config.yaml`：

- `follow.youtube`：监控的频道列表（name + channel_id / handle）
- `app.timezone`：你的时区（默认 `Asia/Shanghai`）
- `publish.offsets`：各平台错峰（分钟）
- `web.auto_refresh`：页面自动刷新窗口

### 5. 跑起来

```bash
# 完整跑一遍：采集 → AI → 发布 → 导出
uv run python -m cunrelay

# 本地查看控制台 UI
uv run python -m cunrelay serve
# 打开 http://127.0.0.1:8080
```

## 部署上线

整个系统部署到 GitHub Actions + Cloudflare Pages，公网可访问，无需服务器。

### 1. 推送代码到 GitHub

```bash
git init
git add .
git commit -m "init: CunRelay v0.1"
git remote add origin https://github.com/<your-name>/CunRelay.git
git push -u origin main
```

> 建议仓库设为 **Public**：公共仓库的 Actions 分钟数完全免费无限制。仓库内不含任何密钥（`.env`、`config/credentials.json`、`public/data.json` 均已被 `.gitignore` 排除）。

### 2. 创建 Cloudflare Pages 项目

1. 登录 [Cloudflare Dashboard](https://dash.cloudflare.com) → **Workers & Pages** → **Create** → **Pages**
2. 连接你的 GitHub 仓库，项目名 `cunrelay`
3. **不需要**设置构建命令（UI 是纯静态文件，由 Actions 负责生成和部署）

### 3. 配置 GitHub Secrets

仓库 → **Settings → Secrets and variables → Actions → New repository secret**：

| Secret | 必填 | 说明 |
|---|---|---|
| `CLOUDFLARE_API_TOKEN` | ✅ | Cloudflare API Token，需 Pages 编辑权限（[创建指引](https://developers.cloudflare.com/fundamentals/api/get-started/create-token/)） |
| `DEEPSEEK_API_KEY` | ✅ | DeepSeek 密钥 |
| `TELEGRAM_BOT_TOKEN` | ✅ | Telegram Bot Token |
| `TELEGRAM_CHAT_ID` | ✅ | 目标频道 ID |
| `GOOGLE_SHEETS_CREDENTIALS` | ✅ | 服务账号 JSON（单行 JSON 字符串） |
| `X_API_KEY` / `X_API_KEY_SECRET` / `X_ACCESS_TOKEN` / `X_ACCESS_TOKEN_SECRET` | 可选 | X 发布，未配置自动跳过 |
| `FOLLOW_CONFIG` | 可选 | 关注列表 JSON，优先级高于 `config/config.yaml` |

> 线上 `config/config.yaml` 里的 `follow` 列表建议留空，用 `FOLLOW_CONFIG` Secret 传入，避免公开仓库暴露订阅信息。

### 4. 定时与手动触发

- **定时**：默认每天北京时间 20:00 自动跑（`.github/workflows/relay.yml` 中 `cron: "0 12 * * *"`，UTC 时间）。改你自己的时间只需改这一处
- **手动**：GitHub → **Actions** → **CunRelay Pipeline** → **Run workflow**

### 5. 首次运行验证

1. 手动触发一次流水线
2. 在 Actions 日志确认：采集、发布、Sheets 写入、`data.json` 生成、Pages 部署全部成功
3. 打开 Cloudflare Pages 提供的域名（`https://cunrelay.pages.dev`），看到控制台即部署完成

## 自动刷新机制（页面不空转）

控制台 UI 采用「结果驱动」的智能刷新，**不依赖固定时间、不产生多余请求**：

- CI 每次运行会从 `relay.yml` 的 cron 自动推导出你的流水线本地时刻（换算 `app.timezone`），写入 `data.json`
- 前端只在「监测窗口」（流水线时刻 + 6 小时，可配）内轮询，**一旦检测到当天的数据快照已生成，立即刷新并停止**
- 窗口外页面完全静默（零网络请求），右上角「刷新」按钮随时手动刷新

## 常见问题

**为什么一次发了好几条？**
72 小时窗口内如果有多个新视频，会全部入队。默认「每轮每频道只发最新 1 条」，其余标记已读跳过。

**后启用平台会补发历史视频吗？**
会。窗口（默认 72 小时）内已处理过的视频，如果某个当前启用的平台从没发布过（比如今天只配了 Telegram、明天启用 X），下次运行时 X 会自动补齐这部分视频；已发布过的平台幂等跳过，不会重复。

**发布失败会一直重试吗？**
不会。首发失败只重试 1 次（30 分钟后），再失败即标记失败，避免平台限流。

**免费额度够吗？**
完全够。GitHub Actions 公共仓库无限分钟；Cloudflare Pages 静态请求/带宽无限、每月 500 次构建（我们每天 1 次）；Google Sheets 免费 API 配额充裕。

## License

MIT

## 关于

© 2026 · [村长实验室 czlab.dev](https://czlab.dev) · [村长博客 cunzhangblog.com](https://www.cunzhangblog.com)
