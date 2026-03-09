# Info Radar

个人信息雷达。定时拉取多源资讯，agent 总结后推送。

## Trigger

当以下情况时使用此 skill：
- 用户说"信息雷达"、"info radar"、"今日资讯"、"早报"、"晚报"、"morning briefing"
- cron 定时触发早报/晚报

## Usage

### 1. 拉取新条目

```bash
python /Users/jettt/.nanobot/workspace/skills/info-radar/radar.py --hours 12
```

- `--hours 12`：早报/晚报用 12 小时窗口
- `--hours 24`：日报用 24 小时
- stdout 输出 JSON，stderr 输出进度日志

### 2. 总结并推送

读取 stdout 的 JSON 输出，按 category 分组，对每组：
- 挑出最值得关注的 3-5 条
- 每条用 1 句中文概括要点，附原文链接
- 如果某个 category 没有新内容，跳过不提

输出格式示例：

```
🛰 信息雷达 — 03/09 早报

🤖 AI/Tech
- OpenAI 发布 GPT-5 Turbo，推理速度提升 3 倍 → [链接]
- Anthropic 开源 Claude 的 tool-use 协议 → [链接]

🔒 Security
- Krebs: 新型供应链攻击影响 npm 生态 → [链接]

☁️ Cloud Native
- 无新内容

🎮 Gaming
- 任天堂 Switch 2 发售日确认 → [链接]

🚀 Indie Dev
- 独立开发者用 AI 一周做出爆款游戏 → [链接]

💰 Crypto
- BTC 突破 15 万，ETH ETF 资金净流入创新高 → [链接]
```

### 3. 管理订阅源

编辑 feeds.json 即可增删源，无需改代码：
- 文件位置：`/Users/jettt/.nanobot/workspace/skills/info-radar/feeds.json`
- 设置 `"enabled": false` 可临时禁用某个源

## Files

- `radar.py` — 核心拉取脚本
- `feeds.json` — 订阅源配置
- `seen.json` — 去重记录（自动生成，勿手动编辑）

## Dependencies

- Python 3.12+
- feedparser (`pip install feedparser`)

## Cron

早报：每天 08:30
晚报：每天 20:30
