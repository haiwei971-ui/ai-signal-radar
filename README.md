# ai-signal-radar

> **Self-hosted 24/7 AI Opportunity, Bounty, Lead & Deal Radar.**  
> Single-file, zero-dependency Python 3 runtime with **SQLite deduplication**, **multi-provider LLM scoring** (OpenAI, DeepSeek, local Ollama, Anthropic Claude, Google Gemini), **instant webhooks** (Telegram, Discord, Slack, Feishu, Bark), a **built-in dark Web Dashboard**, and native **AI Agent `SKILL.md`**.

[English](README.md) · [简体中文](README.zh-CN.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B%20stdlib-22c55e.svg)](radar.py)
[![Zero Dependencies](https://img.shields.io/badge/Dependencies-Zero-38bdf8.svg)](#)

Built & maintained by **Harvey | Tech Builder** ([harveyi.com](https://harveyi.com) · [@lxy71 on X](https://x.com/lxy71)).

---

## Why `ai-signal-radar`?

High-value opportunities disappear fast — a `$450` open-source GitHub bounty gets claimed in 30 minutes, a high-intent Hacker News contract post gets buried under 80 replies by evening, and underpriced refurbished hardware sells out in minutes.

Most monitoring tools either require heavy Docker/Node/Playwright stacks that break constantly or dump hundreds of noisy raw RSS items into your chat.

**`ai-signal-radar` gives you the entire pipeline in one portable `radar.py` file (`0` pip packages required):**

1. **Pull from clean, legal public feeds & APIs**: GitHub Issues Search API (`github_issues`), Hacker News Algolia API (`hn_algolia`), RSS 2.0 (`rss`), Atom (`atom`), or any custom JSON API (`json` with dot-path selectors).
2. **Deduplicate in SQLite (WAL mode)**: Never see or get pinged about the same item twice.
3. **Two-stage scoring (Deterministic Rules → Optional LLM Triage)**:
   - Works **out of the box with zero API keys** via weighted keyword, exclusion, and max-price rules.
   - Optionally routes promising candidates (`score >= 35`) to **OpenAI / DeepSeek / OpenRouter / Groq / local Ollama (`$0` cost!) / Anthropic Claude / Google Gemini** to get a `0–100` score, `ACT_NOW | WATCH | SKIP` verdict, 1-line reason, and concrete next action.
4. **Act from CLI, Web UI, Webhooks, or your AI Coding Agent**:
   - Built-in dark Web Console (`http://127.0.0.1:8787`) with live scan trigger, filters, and Star / Read / Archive triage.
   - Native `SKILL.md` + `--json` output for **Claude Code**, **Cursor**, **Codex**, and **OpenCode**.

---

## 30-Second Quick Start (Zero Install)

```bash
git clone https://github.com/haiwei971-ui/ai-signal-radar.git
cd ai-signal-radar

# 1. Try the instant offline demo & open the Dark Web Dashboard
python3 radar.py demo
python3 radar.py serve --port 8787
# -> Open http://127.0.0.1:8787

# 2. Generate your own watchlists.json and run a live scan
python3 radar.py init
python3 radar.py scan

# 3. Query only high-signal ACT_NOW opportunities
python3 radar.py list --verdict ACT_NOW --min-score 70
```

---

## Built-In Presets (`python3 radar.py presets`)

| Preset | Type | What It Watches |
| :--- | :--- | :--- |
| `github-bounties` | `github_issues` | Newly opened GitHub issues with `bounty`, `💎 Bounty`, `paid`, Algora, or Polar rewards |
| `hn-buyer-leads` | `hn_algolia` | Fresh Hacker News threads seeking contractors, tools, or AI coding workflows |
| `remote-usd-jobs` | `rss` | Worldwide remote backend & AI engineering roles |
| `custom-deal-feed` | `rss` / `json` | Hardware/software price drops, refurbished restocks, or custom JSON feeds (`max_price` supported) |

You can also monitor **any JSON API** without writing code using dot-path selectors in `watchlists.json`:

```json
{
  "name": "refurb-mac-deals",
  "type": "json",
  "url": "https://api.example.com/v1/listings",
  "items_path": "data.items",
  "id_field": "sku",
  "title_field": "name",
  "url_field": "permalink",
  "summary_field": "specs",
  "price_field": "price_usd",
  "max_price": 800,
  "boost_keywords": { "32gb": 25, "m4": 20 },
  "min_notify_score": 70
}
```

---

## Connect Modern AI Models (Optional — Including Free Local Ollama)

Enable `"ai": { "enabled": true, ... }` in `watchlists.json`:

- **OpenAI / DeepSeek / OpenRouter / Groq**:
  ```json
  "ai": {
    "enabled": true,
    "provider": "openai_compatible",
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o-mini",
    "api_key_env": "OPENAI_API_KEY"
  }
  ```
- **Free Local Ollama (`$0` API cost)**:
  ```json
  "ai": {
    "enabled": true,
    "provider": "openai_compatible",
    "base_url": "http://127.0.0.1:11434/v1",
    "model": "qwen2.5:7b"
  }
  ```
- **Anthropic Claude**: `"provider": "anthropic"`, `"model": "claude-3-5-haiku-latest"`, `"api_key_env": "ANTHROPIC_API_KEY"`
- **Google Gemini**: `"provider": "gemini"`, `"model": "gemini-2.5-flash"`, `"api_key_env": "GEMINI_API_KEY"`

---

## Instant Push Alerts & Cron Automation

Toggle any webhook in `watchlists.json` and export the matching environment variable (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`, `DISCORD_WEBHOOK_URL`, `SLACK_WEBHOOK_URL`, `FEISHU_WEBHOOK_URL`, or `BARK_SERVER_URL`), then run on a 15-minute cron:

```cron
*/15 * * * * cd /path/to/ai-signal-radar && /usr/bin/env python3 radar.py scan --config watchlists.json --db radar.db >> radar.log 2>&1
```

---

## Run Tests

```bash
python3 -m unittest discover -s tests -v
```

---

## Companion Projects by Harvey (`@lxy71`)

- **[ai-verify-guards](https://github.com/haiwei971-ui/ai-verify-guards)** — Deterministic shell guards & Claude Code hooks that catch silent AI coding failures
- **[ai-diff-audit](https://github.com/haiwei971-ui/ai-diff-audit)** — Read-only Git diff auditor that flags skipped tests, deleted assertions, and swallowed errors
- **[harveyi.com](https://harveyi.com)** — Interactive terminal verification receipts & builder tools

## License

MIT — see [LICENSE](LICENSE).
