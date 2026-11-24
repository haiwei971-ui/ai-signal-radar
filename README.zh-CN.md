# ai-signal-radar（AI 机会 / 悬赏 / 客源 / 捡漏 24h 智能监控雷达）

[English](README.md) · [简体中文](README.zh-CN.md)

> **单文件、零第三方依赖（纯 Python 3 标准库）的 24 小时高价值信息差监控与 AI 智能评分系统。**  
> 内置 **SQLite 去重账本** + **暗黑极客风 Web 管理面板** + **多模型 AI 评分（OpenAI / DeepSeek / 本地免费 Ollama / Claude / Gemini）** + **多渠道即时推送（Telegram / 飞书 / Discord / Slack / Bark）** + **原生 AI Agent `SKILL.md`**。

作者：**Harvey | Tech Builder**（[harveyi.com](https://harveyi.com) · X: [@lxy71](https://x.com/lxy71)）

---

## 为什么做这个工具？

信息差和速度直接决定收益：GitHub 上刚放出的 `$450` 开源悬赏 Issue 半小时就会被人认领，Hacker News 上找外包/求推荐工具的高意向帖子晚几小时回就被淹没，官翻硬件或低价好物一上架就秒空。

市面上的监控项目往往需要装一整套臃肿的 Docker + Node + 浏览器爬虫，不仅配置麻烦还容易封号失效。**`ai-signal-radar` 把整套闭环浓缩进单个 `radar.py` 脚本，零 `pip install`，克隆下来 10 秒就能跑起来：**

1. **合法公开数据源通吃**：内置 GitHub 悬赏任务搜索（`github_issues`）、Hacker News 实时检索（`hn_algolia`）、RSS 2.0（`rss`）、Atom（`atom`）以及任意自定义 JSON 接口（支持点路径字段提取与 `max_price` 价格上限过滤）。
2. **双层智能评分（零 Key 规则打分 → 可选大模型精筛）**：
   - 不填任何 API Key 也能直接用关键词权重 + 黑名单 + 价格阈值跑通；
   - 开启 AI 后自动筛选高潜条目送入 **DeepSeek / OpenAI / 本地免费 Ollama / Claude / Gemini**，输出 `0–100` 分、`ACT_NOW`（立即行动）判定、入选理由与下一步行动建议。
3. **自带暗黑风 Web 控制台 + 原生 AI Agent 技能**：
   - 一行命令启动本地管理后台 `http://127.0.0.1:8787`（支持一键扫描、按分数/状态筛选、收藏与归档）；
   - 自带 `SKILL.md` 与 `--json` 输出，可直接接入 Claude Code、Cursor、Codex 等 Agent。

---

## 30 秒快速上手

```bash
git clone https://github.com/haiwei971-ui/ai-signal-radar.git
cd ai-signal-radar

# 1. 离线一键写入演示数据并打开 Web 管理面板（无需联网、无需 API Key）
python3 radar.py demo
python3 radar.py serve --port 8787
# 浏览器打开 http://127.0.0.1:8787 即可体验

# 2. 生成默认配置并执行实时扫描
python3 radar.py init
python3 radar.py scan

# 3. 只看值得立刻出手的 ACT_NOW 机会
python3 radar.py list --verdict ACT_NOW --min-score 70
```

---

## 接入大模型与消息推送

编辑 `watchlists.json` 即可接入任意兼容接口（如 DeepSeek、OpenAI 或本地免费 Ollama `http://127.0.0.1:11434/v1`），并通过环境变量配置 **飞书机器人（`FEISHU_WEBHOOK_URL`）**、**Bark 苹果推送（`BARK_SERVER_URL`）**、**Telegram**、**Discord** 或 **Slack**。

搭配系统 `crontab` 每 15 分钟巡检一次，高分机会出现秒级推送到手机，绝不重复打扰：

```cron
*/15 * * * * cd /path/to/ai-signal-radar && /usr/bin/env python3 radar.py scan --config watchlists.json --db radar.db >> radar.log 2>&1
```

## 运行测试

```bash
python3 -m unittest discover -s tests -v
```

## 开源协议

MIT — 详见 [LICENSE](LICENSE)。
