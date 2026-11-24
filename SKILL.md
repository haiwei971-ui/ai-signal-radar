---
name: ai-signal-radar
description: >-
  Self-hosted opportunity, bounty, lead, and deal radar for AI coding agents (Claude Code,
  Cursor, Codex, Copilot). Scans GitHub paid bounties, Hacker News buyer/freelance leads,
  remote USD tech feeds, and custom RSS/Atom/JSON endpoints, deduplicates in SQLite,
  scores items 0-100 via rules + optional LLM (OpenAI, DeepSeek, Ollama, Claude, Gemini),
  and emits structured JSON digests.
---

# ai-signal-radar — Opportunity & Bounty Radar for AI Agents

Use `radar.py` to scan public feeds for actionable engineering bounties, consulting leads, remote contracts, and price deals without leaving the terminal.

## Commands

```bash
# 1. Initialize starter watchlists.json
python3 radar.py init

# 2. Run a live scan and emit structured JSON for agent triage
python3 radar.py scan --json

# 3. Query only high-signal ACT_NOW items from SQLite
python3 radar.py list --verdict ACT_NOW --min-score 70 --json

# 4. Preview offline demo data + launch Web UI on http://127.0.0.1:8787
python3 radar.py demo
python3 radar.py serve --port 8787
```

## Agent Workflow

1. Run `python3 radar.py scan --json` on demand or on a schedule.
2. Filter `items` where `verdict == "ACT_NOW"`.
3. Present the top 3–5 opportunities to the user with their `price_or_reward`, `reason`, `suggested_action`, and `url`.
