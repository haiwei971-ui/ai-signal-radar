#!/usr/bin/env python3
"""
ai-signal-radar — Self-hosted 24/7 AI Opportunity, Bounty, Lead & Deal Radar.

Monitors GitHub paid bounties, Hacker News contract/product leads, remote USD tech
feeds, and arbitrary RSS / Atom / JSON endpoints. Deduplicates in SQLite, scores
every new item (via OpenAI / Ollama / Anthropic / Gemini OR zero-key deterministic
rules), pushes high-signal alerts to Telegram / Discord / Slack / Feishu / Bark,
and serves a built-in dark-mode Web Dashboard.

Zero external dependencies (Python 3.9+ stdlib only). Single-file portable runtime.
Built by Harvey | Tech Builder (@lxy71 · https://harveyi.com)
License: MIT
"""

from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import hashlib
import html
import http.server
import json
import os
import re
import sqlite3
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

VERSION = "1.0.0"
USER_AGENT = f"ai-signal-radar/{VERSION} (+https://github.com/haiwei971-ui/ai-signal-radar)"


# --------------------------------------------------------------------------- #
# Data Models & Built-In Presets
# --------------------------------------------------------------------------- #


@dataclass
class RawSignal:
    watchlist: str
    source_type: str
    external_id: str
    title: str
    url: str
    summary: str = ""
    author: str = ""
    published_at: str = ""
    price_or_reward: str = ""
    raw_meta: Dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        key = f"{self.watchlist}::{self.external_id or self.url or self.title}"
        return hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest()[:32]


@dataclass
class ScoredSignal:
    fingerprint: str
    watchlist: str
    source_type: str
    title: str
    url: str
    summary: str
    author: str
    published_at: str
    price_or_reward: str
    score: int
    verdict: str  # "ACT_NOW", "WATCH", "SKIP"
    reason: str
    suggested_action: str
    scored_by: str  # "rules" or provider name


BUILTIN_PRESETS: Dict[str, Dict[str, Any]] = {
    "github-bounties": {
        "name": "github-bounties",
        "description": "Newly opened GitHub issues offering cash bounties (Algora, Polar, $ labels)",
        "type": "github_issues",
        "query": 'is:issue is:open label:bounty,"💎 Bounty",paid sort:created-desc',
        "limit": 20,
        "must_match_any": ["bounty", "$", "usd", "reward", "algora", "polar", "paid"],
        "boost_keywords": {"python": 15, "typescript": 15, "cli": 12, "agent": 12, "bug": 10, "$": 18},
        "exclude_keywords": ["closed", "claimed"],
        "ai_goal": "Find well-scoped open-source engineering bounties a solo Python/TS developer or coding agent can solve cleanly in 1-4 hours.",
        "min_notify_score": 70,
    },
    "hn-buyer-leads": {
        "name": "hn-buyer-leads",
        "description": "Fresh Hacker News posts/comments looking for tools, contractors, or AI coding workflows",
        "type": "hn_algolia",
        "query": "claude code OR coding agent OR freelance OR looking for tool",
        "tags": "story",
        "limit": 25,
        "boost_keywords": {
            "claude code": 22,
            "cursor": 16,
            "coding agent": 22,
            "verify": 15,
            "guardrail": 18,
            "freelance": 20,
            "contract": 18,
            "budget": 18,
            "hiring": 15,
        },
        "exclude_keywords": ["crypto giveaway"],
        "ai_goal": "Spot high-intent developer pain points, consulting leads, or launch threads worth replying to early.",
        "min_notify_score": 68,
    },
    "remote-usd-jobs": {
        "name": "remote-usd-jobs",
        "description": "Remote developer & AI engineering roles from public RSS feeds",
        "type": "rss",
        "url": "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
        "limit": 20,
        "boost_keywords": {
            "python": 18,
            "ai": 15,
            "llm": 18,
            "agent": 18,
            "typescript": 14,
            "contract": 15,
            "worldwide": 16,
            "anywhere": 16,
        },
        "exclude_keywords": ["us only", "onsite"],
        "ai_goal": "Surface worldwide remote backend/AI roles with strong async engineering culture or USD contract potential.",
        "min_notify_score": 72,
    },
    "custom-deal-feed": {
        "name": "custom-deal-feed",
        "description": "Template RSS/JSON arbitrage & deal monitor (refurb hardware, SaaS lifetime deals, marketplace feeds)",
        "type": "rss",
        "url": "https://hnrss.org/newest?q=Show+HN+open+source",
        "limit": 15,
        "max_price": 0,
        "boost_keywords": {"open source": 18, "cli": 15, "self-hosted": 18, "sqlite": 14, "local": 12},
        "exclude_keywords": ["waitlist only"],
        "ai_goal": "Spot undervalued hardware/software deals or fast-rising open-source tools early.",
        "min_notify_score": 70,
    },
}


def default_config() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "ai": {
            "enabled": False,
            "provider": "openai_compatible",
            "model": "gpt-4o-mini",
            "base_url": "https://api.openai.com/v1",
            "api_key_env": "OPENAI_API_KEY",
            "min_rule_score_for_llm": 35,
            "persona": "Pragmatic full-stack & AI tooling builder looking for paid bounties, high-intent developer leads, and actionable opportunities.",
        },
        "notifications": {
            "telegram": {"enabled": False, "bot_token_env": "TELEGRAM_BOT_TOKEN", "chat_id_env": "TELEGRAM_CHAT_ID"},
            "discord": {"enabled": False, "webhook_url_env": "DISCORD_WEBHOOK_URL"},
            "slack": {"enabled": False, "webhook_url_env": "SLACK_WEBHOOK_URL"},
            "feishu": {"enabled": False, "webhook_url_env": "FEISHU_WEBHOOK_URL"},
            "bark": {"enabled": False, "server_url_env": "BARK_SERVER_URL"},
        },
        "watchlists": [
            BUILTIN_PRESETS["github-bounties"],
            BUILTIN_PRESETS["hn-buyer-leads"],
        ],
    }


# --------------------------------------------------------------------------- #
# Utilities
# --------------------------------------------------------------------------- #


TAG_STRIP_RE = re.compile(r"<[^>]+>")
PRICE_EXTRACT_RE = re.compile(r"(?:\$|USD\s?)\s?\d[\d,]*(?:\.\d{1,2})?", re.IGNORECASE)


def now_utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean_text(raw: Optional[str], max_len: int = 500) -> str:
    if not raw:
        return ""
    unescaped = html.unescape(str(raw))
    no_tags = TAG_STRIP_RE.sub(" ", unescaped)
    compact = " ".join(no_tags.split())
    return compact if len(compact) <= max_len else compact[: max_len - 3] + "..."


def extract_price_label(text: str) -> str:
    m = PRICE_EXTRACT_RE.search(text or "")
    return m.group(0).strip() if m else ""


def parse_price_value(label: str) -> Optional[float]:
    if not label:
        return None
    nums = re.sub(r"[^0-9.]", "", label)
    try:
        return float(nums) if nums else None
    except ValueError:
        return None


def dot_get(obj: Any, path: str, default: Any = "") -> Any:
    if not path:
        return obj
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part, default)
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if 0 <= idx < len(cur) else default
        else:
            return default
    return default if cur is None else cur


def http_get(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 15) -> bytes:
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json, application/xml, text/xml, */*"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def http_post_json(url: str, payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None, timeout: int = 25) -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    hdrs = {"User-Agent": USER_AGENT, "Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else {}


# --------------------------------------------------------------------------- #
# Source Parsers & Fetchers (RSS 2.0, Atom, GitHub Issues, HN Algolia, JSON)
# --------------------------------------------------------------------------- #


def local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def child_text(elem: ET.Element, names: Iterable[str]) -> str:
    wanted = set(names)
    for ch in elem:
        if local_tag(ch.tag) in wanted and ch.text:
            return ch.text.strip()
    return ""


def parse_feed_xml(xml_bytes: bytes, watchlist_name: str, limit: int = 25) -> List[RawSignal]:
    root = ET.fromstring(xml_bytes)
    signals: List[RawSignal] = []
    root_name = local_tag(root.tag).lower()

    if root_name == "feed":  # Atom
        entries = [e for e in root if local_tag(e.tag) == "entry"]
        for entry in entries[:limit]:
            title = clean_text(child_text(entry, ("title",)), 220)
            link = ""
            for ch in entry:
                if local_tag(ch.tag) == "link":
                    rel = ch.attrib.get("rel", "alternate")
                    href = ch.attrib.get("href", "").strip()
                    if href and (rel == "alternate" or not link):
                        link = href
            summary = clean_text(child_text(entry, ("summary", "content")), 500)
            ext_id = child_text(entry, ("id",)) or link or title
            published = child_text(entry, ("published", "updated"))
            author = ""
            for ch in entry:
                if local_tag(ch.tag) == "author":
                    author = clean_text(child_text(ch, ("name",)), 80)
            reward = extract_price_label(f"{title} {summary}")
            if title or link:
                signals.append(
                    RawSignal(
                        watchlist=watchlist_name,
                        source_type="atom",
                        external_id=ext_id,
                        title=title or link,
                        url=link,
                        summary=summary,
                        author=author,
                        published_at=published,
                        price_or_reward=reward,
                    )
                )
    else:  # RSS 2.0 / RDF
        items = [elem for elem in root.iter() if local_tag(elem.tag) == "item"]
        for item in items[:limit]:
            title = clean_text(child_text(item, ("title",)), 220)
            link = child_text(item, ("link",))
            summary = clean_text(child_text(item, ("description", "encoded")), 500)
            ext_id = child_text(item, ("guid",)) or link or title
            pub_raw = child_text(item, ("pubDate", "date"))
            published = pub_raw
            if pub_raw:
                try:
                    published = email.utils.parsedate_to_datetime(pub_raw).astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
                except Exception:
                    published = pub_raw
            author = clean_text(child_text(item, ("creator", "author")), 80)
            reward = extract_price_label(f"{title} {summary}")
            if title or link:
                signals.append(
                    RawSignal(
                        watchlist=watchlist_name,
                        source_type="rss",
                        external_id=ext_id,
                        title=title or link,
                        url=link,
                        summary=summary,
                        author=author,
                        published_at=published,
                        price_or_reward=reward,
                    )
                )
    return signals


def parse_github_issues_json(data: Dict[str, Any], watchlist_name: str, limit: int = 25) -> List[RawSignal]:
    items = data.get("items", []) if isinstance(data, dict) else []
    out: List[RawSignal] = []
    for it in items[:limit]:
        if not isinstance(it, dict):
            continue
        labels = [lb.get("name", "") for lb in it.get("labels", []) if isinstance(lb, dict)]
        label_str = ", ".join(lb for lb in labels if lb)
        title = clean_text(it.get("title", ""), 220)
        body = clean_text(it.get("body", ""), 420)
        summary = f"[Labels: {label_str}] {body}".strip() if label_str else body
        reward = extract_price_label(f"{title} {label_str} {body}")
        url = it.get("html_url", "")
        user = (it.get("user") or {}).get("login", "") if isinstance(it.get("user"), dict) else ""
        out.append(
            RawSignal(
                watchlist=watchlist_name,
                source_type="github_issues",
                external_id=str(it.get("id") or url),
                title=title,
                url=url,
                summary=summary,
                author=user,
                published_at=it.get("created_at", ""),
                price_or_reward=reward,
                raw_meta={"labels": labels, "comments": it.get("comments", 0)},
            )
        )
    return out


def parse_hn_algolia_json(data: Dict[str, Any], watchlist_name: str, limit: int = 25) -> List[RawSignal]:
    hits = data.get("hits", []) if isinstance(data, dict) else []
    out: List[RawSignal] = []
    for h in hits[:limit]:
        if not isinstance(h, dict):
            continue
        obj_id = str(h.get("objectID", ""))
        hn_url = f"https://news.ycombinator.com/item?id={obj_id}" if obj_id else ""
        title = clean_text(h.get("title") or h.get("story_title") or "HN Comment", 220)
        story_text = clean_text(h.get("story_text") or h.get("comment_text") or "", 450)
        ext_url = h.get("url") or hn_url
        pts = h.get("points") or 0
        num_comments = h.get("num_comments") or 0
        summary = f"[{pts} pts · {num_comments} comments · {hn_url}] {story_text}".strip()
        reward = extract_price_label(f"{title} {story_text}")
        out.append(
            RawSignal(
                watchlist=watchlist_name,
                source_type="hn_algolia",
                external_id=obj_id or ext_url,
                title=title,
                url=ext_url,
                summary=summary,
                author=h.get("author", ""),
                published_at=h.get("created_at", ""),
                price_or_reward=reward,
                raw_meta={"hn_url": hn_url, "points": pts, "num_comments": num_comments},
            )
        )
    return out


def parse_json_endpoint(data: Any, wl: Dict[str, Any], limit: int = 25) -> List[RawSignal]:
    items_path = wl.get("items_path", "")
    items = dot_get(data, items_path, []) if items_path else data
    if not isinstance(items, list):
        return []
    name = wl.get("name", "json-feed")
    title_field = wl.get("title_field", "title")
    url_field = wl.get("url_field", "url")
    summary_field = wl.get("summary_field", "summary")
    id_field = wl.get("id_field", "id")
    price_field = wl.get("price_field", "price")
    author_field = wl.get("author_field", "author")
    published_field = wl.get("published_field", "published_at")

    out: List[RawSignal] = []
    for row in items[:limit]:
        if not isinstance(row, dict):
            continue
        title = clean_text(str(dot_get(row, title_field, "")), 220)
        url = str(dot_get(row, url_field, ""))
        summary = clean_text(str(dot_get(row, summary_field, "")), 480)
        ext_id = str(dot_get(row, id_field, "")) or url or title
        raw_price = dot_get(row, price_field, "")
        price_str = f"${raw_price}" if isinstance(raw_price, (int, float)) else str(raw_price or extract_price_label(f"{title} {summary}"))
        if title or url:
            out.append(
                RawSignal(
                    watchlist=name,
                    source_type="json",
                    external_id=ext_id,
                    title=title or url,
                    url=url,
                    summary=summary,
                    author=str(dot_get(row, author_field, "")),
                    published_at=str(dot_get(row, published_field, "")),
                    price_or_reward=price_str,
                )
            )
    return out


def fetch_watchlist(wl: Dict[str, Any], fetcher: Callable[[str, Optional[Dict[str, str]]], bytes] = http_get) -> List[RawSignal]:
    wtype = wl.get("type", "rss")
    name = wl.get("name", wtype)
    limit = int(wl.get("limit", 20))

    if wtype in ("rss", "atom"):
        raw = fetcher(wl["url"], wl.get("headers"))
        return parse_feed_xml(raw, name, limit=limit)

    if wtype == "github_issues":
        q = urllib.parse.quote(wl.get("query", "is:issue is:open label:bounty"))
        url = f"https://api.github.com/search/issues?q={q}&per_page={min(limit, 50)}"
        headers: Dict[str, str] = {"Accept": "application/vnd.github+json"}
        gh_token = os.environ.get("GITHUB_TOKEN", "").strip()
        if gh_token:
            headers["Authorization"] = f"Bearer {gh_token}"
        raw = fetcher(url, headers)
        return parse_github_issues_json(json.loads(raw.decode("utf-8", errors="replace")), name, limit=limit)

    if wtype == "hn_algolia":
        q = urllib.parse.quote(wl.get("query", "claude code"))
        tags = urllib.parse.quote(wl.get("tags", "story"))
        url = f"https://hn.algolia.com/api/v1/search_by_date?query={q}&tags={tags}&hitsPerPage={min(limit, 50)}"
        raw = fetcher(url, None)
        return parse_hn_algolia_json(json.loads(raw.decode("utf-8", errors="replace")), name, limit=limit)

    if wtype == "json":
        raw = fetcher(wl["url"], wl.get("headers"))
        return parse_json_endpoint(json.loads(raw.decode("utf-8", errors="replace")), wl, limit=limit)

    raise ValueError(f"Unsupported watchlist type: {wtype}")


# --------------------------------------------------------------------------- #
# Scoring Engine (Deterministic Rules + Multi-Provider LLM Scorer)
# --------------------------------------------------------------------------- #


def score_by_rules(sig: RawSignal, wl: Dict[str, Any]) -> Tuple[int, str, str, str]:
    blob = f"{sig.title} {sig.summary} {sig.price_or_reward}".lower()

    # Exclusions
    for bad in wl.get("exclude_keywords", []):
        if bad and bad.lower() in blob:
            return 5, "SKIP", f"Excluded by keyword '{bad}'", "Skip"

    must_any = wl.get("must_match_any", [])
    if must_any and not any(tok.lower() in blob for tok in must_any):
        return 18, "SKIP", "Did not match any required trigger keywords", "Skip"

    max_price = wl.get("max_price")
    pval = parse_price_value(sig.price_or_reward)
    if max_price and float(max_price) > 0 and pval is not None and pval > float(max_price):
        return 20, "SKIP", f"Price {sig.price_or_reward} exceeds max_price ({max_price})", "Skip"

    score = 40
    matched: List[str] = []
    boosts: Dict[str, int] = wl.get("boost_keywords", {}) or {}
    for kw, weight in boosts.items():
        if kw.lower() in blob:
            score += int(weight)
            matched.append(f"{kw}(+{weight})")

    if sig.price_or_reward:
        score += 12
        matched.append(f"reward/price({sig.price_or_reward})")

    score = max(0, min(100, score))
    verdict = "ACT_NOW" if score >= 70 else ("WATCH" if score >= 45 else "SKIP")
    reason = "Matched: " + ", ".join(matched[:6]) if matched else "Baseline feed match (no boost keywords hit)"
    action = (
        "Inspect source immediately and act while fresh."
        if verdict == "ACT_NOW"
        else ("Keep on radar; check if fit improves." if verdict == "WATCH" else "Low signal.")
    )
    return score, verdict, reason, action


def extract_json_object(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    else:
        brace = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if brace:
            cleaned = brace.group(0)
    data = json.loads(cleaned)
    return data if isinstance(data, dict) else {}


def score_by_llm(
    sig: RawSignal,
    wl: Dict[str, Any],
    ai_cfg: Dict[str, Any],
    poster: Callable[[str, Dict[str, Any], Optional[Dict[str, str]]], Dict[str, Any]] = http_post_json,
) -> Optional[Tuple[int, str, str, str, str]]:
    if not ai_cfg.get("enabled"):
        return None
    provider = (ai_cfg.get("provider") or "openai_compatible").lower()
    model = ai_cfg.get("model") or "gpt-4o-mini"
    key_env = ai_cfg.get("api_key_env") or "OPENAI_API_KEY"
    api_key = os.environ.get(key_env, "").strip()
    base_url = (ai_cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")

    # Local Ollama doesn't require an API key
    is_local_ollama = "localhost:11434" in base_url or "127.0.0.1:11434" in base_url
    if not api_key and not is_local_ollama:
        return None

    persona = ai_cfg.get("persona", "")
    goal = wl.get("ai_goal", wl.get("description", "Evaluate opportunity quality."))
    prompt = (
        "You are an opportunity & signal triage engine.\n"
        f"Builder Persona: {persona}\n"
        f"Watchlist Goal: {goal}\n\n"
        f"Signal Title: {sig.title}\n"
        f"Price/Reward: {sig.price_or_reward or 'N/A'}\n"
        f"URL: {sig.url}\n"
        f"Summary: {sig.summary}\n\n"
        "Return ONLY valid JSON with keys: "
        '{"score": <int 0-100>, "verdict": "ACT_NOW"|"WATCH"|"SKIP", '
        '"reason": "<concise 1-sentence reason>", "suggested_action": "<concrete next step>"}'
    )

    if provider == "anthropic":
        resp = poster(
            "https://api.anthropic.com/v1/messages",
            {
                "model": model,
                "max_tokens": 250,
                "messages": [{"role": "user", "content": prompt}],
            },
            {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        )
        blocks = resp.get("content", [])
        raw_text = blocks[0].get("text", "{}") if blocks and isinstance(blocks[0], dict) else "{}"
    elif provider == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={urllib.parse.quote(api_key)}"
        resp = poster(
            url,
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"},
            },
            None,
        )
        cands = resp.get("candidates", [])
        parts = (((cands[0] if cands else {}).get("content") or {}).get("parts") or [{}])
        raw_text = parts[0].get("text", "{}")
    else:  # openai_compatible (OpenAI, DeepSeek, OpenRouter, Groq, Ollama)
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        resp = poster(
            f"{base_url}/chat/completions",
            {
                "model": model,
                "temperature": 0.1,
                "messages": [{"role": "user", "content": prompt}],
            },
            headers,
        )
        choices = resp.get("choices", [])
        raw_text = (((choices[0] if choices else {}).get("message") or {}).get("content") or "{}")

    parsed = extract_json_object(raw_text)
    score = max(0, min(100, int(parsed.get("score", 50))))
    verdict = str(parsed.get("verdict") or ("ACT_NOW" if score >= 70 else "WATCH" if score >= 45 else "SKIP")).upper()
    if verdict not in ("ACT_NOW", "WATCH", "SKIP"):
        verdict = "ACT_NOW" if score >= 70 else ("WATCH" if score >= 45 else "SKIP")
    reason = clean_text(str(parsed.get("reason", "AI evaluated")), 220)
    action = clean_text(str(parsed.get("suggested_action", "")), 220)
    return score, verdict, reason, action, f"llm:{provider}:{model}"


def evaluate_signal(
    sig: RawSignal,
    wl: Dict[str, Any],
    ai_cfg: Dict[str, Any],
    llm_poster: Callable[[str, Dict[str, Any], Optional[Dict[str, str]]], Dict[str, Any]] = http_post_json,
) -> ScoredSignal:
    r_score, r_verdict, r_reason, r_action = score_by_rules(sig, wl)
    scored_by = "rules"
    min_for_llm = int(ai_cfg.get("min_rule_score_for_llm", 35))

    if ai_cfg.get("enabled") and r_score >= min_for_llm:
        try:
            llm_res = score_by_llm(sig, wl, ai_cfg, poster=llm_poster)
            if llm_res is not None:
                r_score, r_verdict, r_reason, r_action, scored_by = llm_res
        except Exception as exc:
            r_reason = f"{r_reason} (LLM fallback: {clean_text(str(exc), 60)})"

    return ScoredSignal(
        fingerprint=sig.fingerprint(),
        watchlist=sig.watchlist,
        source_type=sig.source_type,
        title=sig.title,
        url=sig.url,
        summary=sig.summary,
        author=sig.author,
        published_at=sig.published_at or now_utc_iso(),
        price_or_reward=sig.price_or_reward,
        score=r_score,
        verdict=r_verdict,
        reason=r_reason,
        suggested_action=r_action,
        scored_by=scored_by,
    )


# --------------------------------------------------------------------------- #
# SQLite Storage & Deduplication Ledger
# --------------------------------------------------------------------------- #


class SignalStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        parent = os.path.dirname(os.path.abspath(db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    fingerprint TEXT PRIMARY KEY,
                    watchlist TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    author TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    price_or_reward TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    verdict TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    suggested_action TEXT NOT NULL,
                    scored_by TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'unread',
                    notified INTEGER NOT NULL DEFAULT 0,
                    discovered_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_score ON signals(score DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status)")

    def exists(self, fingerprint: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM signals WHERE fingerprint = ?", (fingerprint,)).fetchone()
            return row is not None

    def insert(self, item: ScoredSignal) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO signals (
                    fingerprint, watchlist, source_type, title, url, summary, author,
                    published_at, price_or_reward, score, verdict, reason,
                    suggested_action, scored_by, status, notified, discovered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unread', 0, ?)
                """,
                (
                    item.fingerprint,
                    item.watchlist,
                    item.source_type,
                    item.title,
                    item.url,
                    item.summary,
                    item.author,
                    item.published_at,
                    item.price_or_reward,
                    item.score,
                    item.verdict,
                    item.reason,
                    item.suggested_action,
                    item.scored_by,
                    now_utc_iso(),
                ),
            )
            return cur.rowcount > 0

    def mark_notified(self, fingerprint: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE signals SET notified = 1 WHERE fingerprint = ?", (fingerprint,))

    def update_status(self, fingerprint: str, status: str) -> bool:
        if status not in ("unread", "starred", "archived", "read"):
            raise ValueError(f"Invalid status: {status}")
        with self._connect() as conn:
            cur = conn.execute("UPDATE signals SET status = ? WHERE fingerprint = ?", (status, fingerprint))
            return cur.rowcount > 0

    def query(
        self,
        watchlist: str = "",
        verdict: str = "",
        status: str = "",
        min_score: int = 0,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        clauses = ["score >= ?"]
        params: List[Any] = [int(min_score)]
        if watchlist:
            clauses.append("watchlist = ?")
            params.append(watchlist)
        if verdict:
            clauses.append("verdict = ?")
            params.append(verdict.upper())
        if status:
            clauses.append("status = ?")
            params.append(status.lower())
        else:
            clauses.append("status != 'archived'")
        params.append(int(limit))
        sql = f"SELECT * FROM signals WHERE {' AND '.join(clauses)} ORDER BY score DESC, discovered_at DESC LIMIT ?"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def stats(self) -> Dict[str, Any]:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
            act_now = conn.execute("SELECT COUNT(*) FROM signals WHERE verdict = 'ACT_NOW' AND status != 'archived'").fetchone()[0]
            unread = conn.execute("SELECT COUNT(*) FROM signals WHERE status = 'unread'").fetchone()[0]
            starred = conn.execute("SELECT COUNT(*) FROM signals WHERE status = 'starred'").fetchone()[0]
            by_wl = {
                r["watchlist"]: r["cnt"]
                for r in conn.execute("SELECT watchlist, COUNT(*) AS cnt FROM signals GROUP BY watchlist").fetchall()
            }
        return {
            "total": total,
            "act_now": act_now,
            "unread": unread,
            "starred": starred,
            "by_watchlist": by_wl,
        }


# --------------------------------------------------------------------------- #
# Webhook Notifications (Telegram, Discord, Slack, Feishu, Bark)
# --------------------------------------------------------------------------- #


def format_alert_message(sig: ScoredSignal) -> str:
    reward = f" · {sig.price_or_reward}" if sig.price_or_reward else ""
    return (
        f"[{sig.verdict} · {sig.score}/100{reward}] ({sig.watchlist})\n"
        f"{sig.title}\n"
        f"Why: {sig.reason}\n"
        f"Next: {sig.suggested_action}\n"
        f"{sig.url}"
    )


def dispatch_notifications(
    sig: ScoredSignal,
    notif_cfg: Dict[str, Any],
    poster: Callable[[str, Dict[str, Any], Optional[Dict[str, str]]], Dict[str, Any]] = http_post_json,
) -> List[str]:
    sent_channels: List[str] = []
    text = format_alert_message(sig)

    tg = notif_cfg.get("telegram") or {}
    if tg.get("enabled"):
        token = os.environ.get(tg.get("bot_token_env", "TELEGRAM_BOT_TOKEN"), "").strip()
        chat_id = os.environ.get(tg.get("chat_id_env", "TELEGRAM_CHAT_ID"), "").strip()
        if token and chat_id:
            poster(
                f"https://api.telegram.org/bot{token}/sendMessage",
                {"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
                None,
            )
            sent_channels.append("telegram")

    dc = notif_cfg.get("discord") or {}
    if dc.get("enabled"):
        wh = os.environ.get(dc.get("webhook_url_env", "DISCORD_WEBHOOK_URL"), "").strip()
        if wh:
            poster(wh, {"content": text}, None)
            sent_channels.append("discord")

    sl = notif_cfg.get("slack") or {}
    if sl.get("enabled"):
        wh = os.environ.get(sl.get("webhook_url_env", "SLACK_WEBHOOK_URL"), "").strip()
        if wh:
            poster(wh, {"text": text}, None)
            sent_channels.append("slack")

    fs = notif_cfg.get("feishu") or {}
    if fs.get("enabled"):
        wh = os.environ.get(fs.get("webhook_url_env", "FEISHU_WEBHOOK_URL"), "").strip()
        if wh:
            poster(wh, {"msg_type": "text", "content": {"text": text}}, None)
            sent_channels.append("feishu")

    bk = notif_cfg.get("bark") or {}
    if bk.get("enabled"):
        srv = os.environ.get(bk.get("server_url_env", "BARK_SERVER_URL"), "").strip().rstrip("/")
        if srv:
            poster(
                f"{srv}/push",
                {"title": f"[{sig.score}] {sig.watchlist}", "body": f"{sig.title}\n{sig.reason}", "url": sig.url},
                None,
            )
            sent_channels.append("bark")

    return sent_channels


# --------------------------------------------------------------------------- #
# Scan Runner
# --------------------------------------------------------------------------- #


def run_scan(
    cfg: Dict[str, Any],
    store: SignalStore,
    only_watchlist: str = "",
    fetcher: Callable[[str, Optional[Dict[str, str]]], bytes] = http_get,
    poster: Callable[[str, Dict[str, Any], Optional[Dict[str, str]]], Dict[str, Any]] = http_post_json,
) -> Dict[str, Any]:
    ai_cfg = cfg.get("ai") or {}
    notif_cfg = cfg.get("notifications") or {}
    watchlists = cfg.get("watchlists") or []

    fetched_count = 0
    inserted: List[Dict[str, Any]] = []
    notified_count = 0
    errors: List[str] = []

    for wl in watchlists:
        name = wl.get("name", "")
        if only_watchlist and name != only_watchlist:
            continue
        if wl.get("enabled", True) is False:
            continue
        try:
            raw_items = fetch_watchlist(wl, fetcher=fetcher)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            continue

        fetched_count += len(raw_items)
        min_notify = int(wl.get("min_notify_score", 70))

        for raw_sig in raw_items:
            fp = raw_sig.fingerprint()
            if store.exists(fp):
                continue
            scored = evaluate_signal(raw_sig, wl, ai_cfg, llm_poster=poster)
            if store.insert(scored):
                row = asdict(scored)
                inserted.append(row)
                if scored.score >= min_notify and scored.verdict != "SKIP":
                    try:
                        channels = dispatch_notifications(scored, notif_cfg, poster=poster)
                        if channels:
                            store.mark_notified(fp)
                            notified_count += 1
                    except Exception as exc:
                        errors.append(f"notify({name}): {exc}")

    inserted.sort(key=lambda r: r["score"], reverse=True)
    return {
        "scanned_at": now_utc_iso(),
        "fetched": fetched_count,
        "new_signals": len(inserted),
        "act_now_new": sum(1 for r in inserted if r["verdict"] == "ACT_NOW"),
        "notified": notified_count,
        "errors": errors,
        "items": inserted,
    }


# --------------------------------------------------------------------------- #
# Built-In Dark-Obsidian Web Dashboard (Zero Node / Zero External Assets)
# --------------------------------------------------------------------------- #

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Signal Radar — Opportunity & Bounty Console</title>
<style>
  :root {
    --bg: #0b0d12;
    --panel: #121621;
    --border: #232a3b;
    --text: #e7ecf5;
    --muted: #8b95ab;
    --accent: #38bdf8;
    --green: #22c55e;
    --amber: #f59e0b;
    --red: #ef4444;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif;
    background: var(--bg); color: var(--text); line-height: 1.5;
  }
  header {
    border-bottom: 1px solid var(--border); padding: 18px 28px;
    display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 14px;
    background: rgba(18, 22, 33, 0.9); position: sticky; top: 0; backdrop-filter: blur(8px); z-index: 10;
  }
  .brand { display: flex; align-items: center; gap: 12px; }
  .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--green); box-shadow: 0 0 12px var(--green); }
  h1 { font-size: 18px; margin: 0; letter-spacing: -0.01em; }
  .sub { color: var(--muted); font-size: 13px; }
  .wrap { max-width: 1180px; margin: 0 auto; padding: 24px 28px 60px; }
  .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin-bottom: 20px; }
  .kpi { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 14px 18px; }
  .kpi .lbl { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; }
  .kpi .val { font-size: 26px; font-weight: 700; margin-top: 4px; }
  .controls {
    display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 12px 16px; margin-bottom: 18px;
  }
  select, input[type="number"], button {
    background: #0d1018; color: var(--text); border: 1px solid var(--border);
    border-radius: 7px; padding: 8px 12px; font-size: 13px;
  }
  button { cursor: pointer; font-weight: 600; transition: border-color 0.15s, opacity 0.15s; }
  button:hover { border-color: var(--accent); }
  button.primary { background: var(--accent); color: #06121d; border-color: var(--accent); }
  button:disabled { opacity: 0.5; cursor: wait; }
  .cards { display: flex; flex-direction: column; gap: 12px; }
  .card {
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 16px 20px;
    display: flex; flex-direction: column; gap: 8px;
  }
  .card.act { border-left: 3px solid var(--green); }
  .card.watch { border-left: 3px solid var(--amber); }
  .row-top { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; flex-wrap: wrap; }
  .badges { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; font-size: 12px; }
  .badge { padding: 2px 8px; border-radius: 999px; font-weight: 600; border: 1px solid var(--border); }
  .badge.ACT_NOW { background: rgba(34,197,94,0.15); color: #4ade80; border-color: rgba(34,197,94,0.4); }
  .badge.WATCH { background: rgba(245,158,11,0.15); color: #fbbf24; border-color: rgba(245,158,11,0.4); }
  .badge.SKIP { color: var(--muted); }
  .reward { color: #38bdf8; font-weight: 700; }
  .title { font-size: 16px; font-weight: 600; color: var(--text); text-decoration: none; }
  .title:hover { color: var(--accent); text-decoration: underline; }
  .reason { font-size: 13px; color: #cbd5e1; background: #0c0f17; border-radius: 6px; padding: 8px 12px; border: 1px solid #1b2232; }
  .summary { font-size: 13px; color: var(--muted); }
  .actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 4px; }
  .actions button { padding: 5px 10px; font-size: 12px; }
  .toast { font-size: 13px; color: var(--accent); margin-left: auto; }
  .empty { text-align: center; padding: 48px 20px; color: var(--muted); border: 1px dashed var(--border); border-radius: 10px; }
</style>
</head>
<body>
<header>
  <div class="brand">
    <span class="dot"></span>
    <div>
      <h1>AI Signal Radar</h1>
      <div class="sub">24/7 Opportunity, Bounty, Lead &amp; Deal Console</div>
    </div>
  </div>
  <div style="display:flex;gap:10px;align-items:center;">
    <span id="toast" class="toast"></span>
    <button id="scanBtn" class="primary" onclick="triggerScan()">Run Live Scan</button>
  </div>
</header>
<div class="wrap">
  <div class="kpis">
    <div class="kpi"><div class="lbl">Total Tracked</div><div class="val" id="kTotal">-</div></div>
    <div class="kpi"><div class="lbl">ACT_NOW Signals</div><div class="val" id="kAct" style="color:var(--green)">-</div></div>
    <div class="kpi"><div class="lbl">Unread</div><div class="val" id="kUnread">-</div></div>
    <div class="kpi"><div class="lbl">Starred</div><div class="val" id="kStarred" style="color:var(--amber)">-</div></div>
  </div>
  <div class="controls">
    <select id="fWatchlist" onchange="loadSignals()"><option value="">All Watchlists</option></select>
    <select id="fVerdict" onchange="loadSignals()">
      <option value="">All Verdicts</option>
      <option value="ACT_NOW">ACT_NOW</option>
      <option value="WATCH">WATCH</option>
      <option value="SKIP">SKIP</option>
    </select>
    <select id="fStatus" onchange="loadSignals()">
      <option value="">Active (Unread + Starred)</option>
      <option value="unread">Unread Only</option>
      <option value="starred">Starred Only</option>
      <option value="archived">Archived</option>
    </select>
    <label style="font-size:13px;color:var(--muted);">Min Score:
      <input id="fScore" type="number" min="0" max="100" value="0" style="width:68px" onchange="loadSignals()">
    </label>
  </div>
  <div id="list" class="cards"></div>
</div>
<script>
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}
async function loadSignals() {
  const wl = document.getElementById('fWatchlist').value;
  const vd = document.getElementById('fVerdict').value;
  const st = document.getElementById('fStatus').value;
  const sc = document.getElementById('fScore').value || 0;
  const [statsRes, sigRes] = await Promise.all([
    fetch('/api/stats').then(r => r.json()),
    fetch(`/api/signals?watchlist=${encodeURIComponent(wl)}&verdict=${encodeURIComponent(vd)}&status=${encodeURIComponent(st)}&min_score=${encodeURIComponent(sc)}`).then(r => r.json())
  ]);
  document.getElementById('kTotal').textContent = statsRes.total;
  document.getElementById('kAct').textContent = statsRes.act_now;
  document.getElementById('kUnread').textContent = statsRes.unread;
  document.getElementById('kStarred').textContent = statsRes.starred;

  const sel = document.getElementById('fWatchlist');
  const known = new Set(Array.from(sel.options).map(o => o.value));
  Object.keys(statsRes.by_watchlist || {}).forEach(k => {
    if (!known.has(k)) {
      const opt = document.createElement('option');
      opt.value = k; opt.textContent = `${k} (${statsRes.by_watchlist[k]})`;
      sel.appendChild(opt);
    }
  });

  const box = document.getElementById('list');
  const items = sigRes.items || [];
  if (!items.length) {
    box.innerHTML = '<div class="empty">No signals match your current filters. Click <b>Run Live Scan</b> or lower Min Score.</div>';
    return;
  }
  box.innerHTML = items.map(it => {
    const cls = it.verdict === 'ACT_NOW' ? 'act' : (it.verdict === 'WATCH' ? 'watch' : '');
    const reward = it.price_or_reward ? `<span class="reward">${esc(it.price_or_reward)}</span>` : '';
    return `
      <div class="card ${cls}">
        <div class="row-top">
          <div class="badges">
            <span class="badge ${esc(it.verdict)}">${esc(it.verdict)} · ${it.score}/100</span>
            <span class="badge">${esc(it.watchlist)}</span>
            ${reward}
            <span class="sub">${esc(it.scored_by)} · ${esc(it.published_at || '')}</span>
          </div>
          <span class="sub">status: <b>${esc(it.status)}</b></span>
        </div>
        <a class="title" href="${esc(it.url)}" target="_blank" rel="noopener noreferrer">${esc(it.title)}</a>
        <div class="reason"><b>Why:</b> ${esc(it.reason)} &nbsp;|&nbsp; <b>Action:</b> ${esc(it.suggested_action)}</div>
        ${it.summary ? `<div class="summary">${esc(it.summary)}</div>` : ''}
        <div class="actions">
          <button onclick="setStatus('${esc(it.fingerprint)}','starred')">★ Star</button>
          <button onclick="setStatus('${esc(it.fingerprint)}','read')">✓ Mark Read</button>
          <button onclick="setStatus('${esc(it.fingerprint)}','archived')">Archive</button>
        </div>
      </div>`;
  }).join('');
}
async function setStatus(fp, status) {
  await fetch('/api/status', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({fingerprint: fp, status})
  });
  loadSignals();
}
async function triggerScan() {
  const btn = document.getElementById('scanBtn');
  const toast = document.getElementById('toast');
  btn.disabled = true; toast.textContent = 'Scanning feeds...';
  try {
    const r = await fetch('/api/scan', {method: 'POST'}).then(x => x.json());
    toast.textContent = `Fetched ${r.fetched}, +${r.new_signals} new (${r.act_now_new} ACT_NOW)`;
    await loadSignals();
  } catch (e) {
    toast.textContent = 'Scan error';
  } finally {
    btn.disabled = false;
  }
}
loadSignals();
</script>
</body>
</html>
"""


def make_handler(cfg: Dict[str, Any], store: SignalStore) -> type:
    lock = threading.Lock()

    class RadarHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return  # Quiet clean console

        def _send_json(self, code: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path in ("/", "/index.html"):
                body = DASHBOARD_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/api/stats":
                self._send_json(200, store.stats())
                return
            if parsed.path == "/api/signals":
                qs = urllib.parse.parse_qs(parsed.query)
                items = store.query(
                    watchlist=(qs.get("watchlist") or [""])[0],
                    verdict=(qs.get("verdict") or [""])[0],
                    status=(qs.get("status") or [""])[0],
                    min_score=int((qs.get("min_score") or ["0"])[0] or 0),
                    limit=int((qs.get("limit") or ["100"])[0] or 100),
                )
                self._send_json(200, {"items": items})
                return
            self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length).decode("utf-8", errors="replace") if length > 0 else "{}"
            try:
                body = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                self._send_json(400, {"error": "invalid json"})
                return

            if parsed.path == "/api/status":
                fp = str(body.get("fingerprint", ""))
                st = str(body.get("status", ""))
                try:
                    updated = store.update_status(fp, st)
                    self._send_json(200, {"updated": updated})
                except ValueError as exc:
                    self._send_json(400, {"error": str(exc)})
                return

            if parsed.path == "/api/scan":
                with lock:
                    report = run_scan(cfg, store, only_watchlist=str(body.get("watchlist", "")))
                self._send_json(200, report)
                return

            self._send_json(404, {"error": "not found"})

    return RadarHandler


# --------------------------------------------------------------------------- #
# Demo Mode & CLI Commands
# --------------------------------------------------------------------------- #


DEMO_SIGNALS: List[Tuple[RawSignal, Dict[str, Any]]] = [
    (
        RawSignal(
            watchlist="github-bounties",
            source_type="github_issues",
            external_id="demo-gh-1",
            title="[💎 Bounty $450] Add deterministic JSON schema & pipefail check to CLI runner",
            url="https://github.com/example/agent-runner/issues/142",
            summary="[Labels: 💎 Bounty, python, cli] Need zero-dependency Python 3 validation hook with full unit tests.",
            author="maintainer_alex",
            published_at=now_utc_iso(),
            price_or_reward="$450",
        ),
        BUILTIN_PRESETS["github-bounties"],
    ),
    (
        RawSignal(
            watchlist="hn-buyer-leads",
            source_type="hn_algolia",
            external_id="demo-hn-2",
            title="Ask HN: Looking for a contractor to set up Claude Code guardrails & CI gates for 12-person team",
            url="https://news.ycombinator.com/item?id=999001",
            summary="[42 pts · 18 comments] Our team merges 400 PRs/mo with Claude Code and Cursor; budget $2,500 for a clean pre-commit + CI setup.",
            author="cto_fintech",
            published_at=now_utc_iso(),
            price_or_reward="$2,500",
        ),
        BUILTIN_PRESETS["hn-buyer-leads"],
    ),
    (
        RawSignal(
            watchlist="custom-deal-feed",
            source_type="rss",
            external_id="demo-deal-3",
            title="Refurbished Mac mini M4 32GB / 512GB back in stock — $679 (open source local LLM server favorite)",
            url="https://example.com/deals/mac-mini-m4-32gb",
            summary="Official refurbished drop, 22% below retail. Popular self-hosted local coding agent server.",
            author="dealbot",
            published_at=now_utc_iso(),
            price_or_reward="$679",
        ),
        BUILTIN_PRESETS["custom-deal-feed"],
    ),
]


def load_config(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return default_config()
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def print_table(items: List[Dict[str, Any]]) -> None:
    if not items:
        print("No matching signals.")
        return
    for idx, it in enumerate(items, 1):
        reward = f" [{it['price_or_reward']}]" if it.get("price_or_reward") else ""
        print(f"{idx:2d}. [{it['verdict']:7s} {it['score']:3d}/100]{reward} ({it['watchlist']}) {it['title']}")
        print(f"    Why : {it['reason']}")
        print(f"    URL : {it['url']}")


def build_cli() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="radar.py",
        description="ai-signal-radar: Self-hosted 24/7 AI Opportunity, Bounty, Lead & Deal Radar.",
    )
    p.add_argument("--config", default="watchlists.json", help="Path to watchlists config JSON (default: watchlists.json)")
    p.add_argument("--db", default="radar.db", help="Path to SQLite database (default: radar.db)")
    p.add_argument("--version", action="version", version=f"ai-signal-radar {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Create starter watchlists.json with built-in presets")
    p_init.add_argument("--presets", nargs="*", choices=list(BUILTIN_PRESETS.keys()), help="Specific presets to include")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing config file")

    sub.add_parser("presets", help="List built-in opportunity & deal watchlist presets")

    p_scan = sub.add_parser("scan", help="Fetch watchlists, deduplicate, score (rules + optional LLM), and notify")
    p_scan.add_argument("--watchlist", default="", help="Scan only a single named watchlist")
    p_scan.add_argument("--json", action="store_true", help="Output structured JSON (ideal for AI coding agents)")

    p_list = sub.add_parser("list", help="Query stored signals from SQLite")
    p_list.add_argument("--watchlist", default="", help="Filter by watchlist name")
    p_list.add_argument("--verdict", default="", choices=["", "ACT_NOW", "WATCH", "SKIP"], help="Filter by verdict")
    p_list.add_argument("--status", default="", choices=["", "unread", "starred", "read", "archived"], help="Filter by status")
    p_list.add_argument("--min-score", type=int, default=0, help="Minimum score (0-100)")
    p_list.add_argument("--limit", type=int, default=25, help="Max rows to display")
    p_list.add_argument("--json", action="store_true", help="Output structured JSON")

    p_demo = sub.add_parser("demo", help="Seed realistic demo signals (offline) to preview CLI & Web UI instantly")
    p_demo.add_argument("--json", action="store_true", help="Output JSON")

    p_serve = sub.add_parser("serve", help="Start the built-in Dark Web Dashboard")
    p_serve.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    p_serve.add_argument("--port", type=int, default=8787, help="Bind port (default: 8787)")

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_cli()
    args = parser.parse_args(argv)

    if args.cmd == "presets":
        for key, val in BUILTIN_PRESETS.items():
            print(f"- {key:18s} ({val['type']:13s}): {val['description']}")
        return 0

    if args.cmd == "init":
        if os.path.exists(args.config) and not args.force:
            print(f"[radar] Config already exists: {args.config} (pass --force to overwrite)", file=sys.stderr)
            return 1
        cfg = default_config()
        if args.presets:
            cfg["watchlists"] = [BUILTIN_PRESETS[k] for k in args.presets]
        with open(args.config, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
        print(f"[radar] Wrote starter config to {args.config} ({len(cfg['watchlists'])} watchlists).")
        return 0

    cfg = load_config(args.config)
    store = SignalStore(args.db)

    if args.cmd == "demo":
        inserted = 0
        for raw_sig, preset in DEMO_SIGNALS:
            scored = evaluate_signal(raw_sig, preset, {"enabled": False})
            if store.insert(scored):
                inserted += 1
        rows = store.query(limit=20)
        if args.json:
            print(json.dumps({"seeded": inserted, "items": rows}, indent=2))
        else:
            print(f"=== AI SIGNAL RADAR DEMO ({inserted} new demo signal(s) seeded into {args.db}) ===")
            print_table(rows)
            print(f"\nLaunch Web UI: python3 radar.py --db {args.db} serve --port 8787")
        return 0

    if args.cmd == "scan":
        report = run_scan(cfg, store, only_watchlist=args.watchlist)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(
                f"=== RADAR SCAN COMPLETE ({report['scanned_at']}) ===\n"
                f"Fetched: {report['fetched']} | New: {report['new_signals']} | "
                f"ACT_NOW: {report['act_now_new']} | Alerts Sent: {report['notified']}"
            )
            if report["errors"]:
                for err in report["errors"]:
                    print(f"  [warn] {err}", file=sys.stderr)
            print_table(report["items"][:15])
        return 0

    if args.cmd == "list":
        rows = store.query(
            watchlist=args.watchlist,
            verdict=args.verdict,
            status=args.status,
            min_score=args.min_score,
            limit=args.limit,
        )
        if args.json:
            print(json.dumps({"stats": store.stats(), "items": rows}, indent=2))
        else:
            st = store.stats()
            print(f"Stats: total={st['total']} | ACT_NOW={st['act_now']} | unread={st['unread']} | starred={st['starred']}")
            print_table(rows)
        return 0

    if args.cmd == "serve":
        handler_cls = make_handler(cfg, store)
        server = http.server.ThreadingHTTPServer((args.host, args.port), handler_cls)
        print(f"[radar] Web Dashboard live at http://{args.host}:{args.port} (DB: {args.db})")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
