#!/usr/bin/env python3
"""Offline deterministic test suite for ai-signal-radar."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import radar  # noqa: E402


SAMPLE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Remote Jobs</title>
    <item>
      <guid>job-101</guid>
      <title>Senior Python AI Agent Engineer (Worldwide Contract $120/hr)</title>
      <link>https://example.com/jobs/101</link>
      <description>&lt;p&gt;Build LLM &amp;amp; Python agent tooling worldwide.&lt;/p&gt;</description>
      <pubDate>Tue, 24 Nov 2026 04:00:00 GMT</pubDate>
    </item>
    <item>
      <guid>job-102</guid>
      <title>Onsite Java Role (US Only)</title>
      <link>https://example.com/jobs/102</link>
      <description>Must be onsite in office.</description>
    </item>
  </channel>
</rss>
"""

SAMPLE_ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Releases</title>
  <entry>
    <id>tag:example.com,2026:1</id>
    <title>Refurbished Workstation Deal $599</title>
    <link rel="alternate" href="https://example.com/deals/1"/>
    <summary>Self-hosted local CLI box</summary>
    <updated>2026-11-24T03:00:00Z</updated>
    <author><name>DealBot</name></author>
  </entry>
</feed>
"""

SAMPLE_GH_ISSUES = {
    "items": [
        {
            "id": 9001,
            "title": "[Bounty $500] Add strict JSON check to Python CLI agent",
            "html_url": "https://github.com/acme/repo/issues/9",
            "body": "Paid $500 USD via Algora for a clean fix.",
            "labels": [{"name": "💎 Bounty"}, {"name": "python"}],
            "user": {"login": "alice"},
            "created_at": "2026-11-24T02:00:00Z",
            "comments": 1,
        }
    ]
}

SAMPLE_HN = {
    "hits": [
        {
            "objectID": "8801",
            "title": "Ask HN: Hiring freelance dev to wire Claude Code guardrails ($1,500 budget)",
            "url": "",
            "story_text": "Looking for someone experienced with coding agent verification hooks.",
            "author": "founder_bob",
            "points": 25,
            "num_comments": 6,
            "created_at": "2026-11-24T01:30:00Z",
        }
    ]
}

SAMPLE_JSON_FEED = {
    "data": {
        "listings": [
            {"sku": "mac-1", "name": "Mac mini M4 32GB", "permalink": "https://example.com/m1", "specs": "Open source server", "price_usd": 650},
            {"sku": "mac-2", "name": "Mac Pro Rack", "permalink": "https://example.com/m2", "specs": "Workstation", "price_usd": 4200},
        ]
    }
}


class ParserAndRuleTests(unittest.TestCase):
    def test_rss_and_atom_parsers(self):
        rss_items = radar.parse_feed_xml(SAMPLE_RSS, "remote-usd-jobs")
        self.assertEqual(len(rss_items), 2)
        self.assertEqual(rss_items[0].price_or_reward, "$120")
        self.assertIn("Build LLM & Python", rss_items[0].summary)

        atom_items = radar.parse_feed_xml(SAMPLE_ATOM, "deals")
        self.assertEqual(len(atom_items), 1)
        self.assertEqual(atom_items[0].url, "https://example.com/deals/1")
        self.assertEqual(atom_items[0].author, "DealBot")

    def test_github_and_hn_parsers(self):
        gh = radar.parse_github_issues_json(SAMPLE_GH_ISSUES, "github-bounties")
        self.assertEqual(len(gh), 1)
        self.assertEqual(gh[0].price_or_reward, "$500")
        score, verdict, _, _ = radar.score_by_rules(gh[0], radar.BUILTIN_PRESETS["github-bounties"])
        self.assertGreaterEqual(score, 85)
        self.assertEqual(verdict, "ACT_NOW")

        hn = radar.parse_hn_algolia_json(SAMPLE_HN, "hn-buyer-leads")
        self.assertEqual(len(hn), 1)
        self.assertIn("news.ycombinator.com/item?id=8801", hn[0].url)
        h_score, h_verdict, _, _ = radar.score_by_rules(hn[0], radar.BUILTIN_PRESETS["hn-buyer-leads"])
        self.assertGreaterEqual(h_score, 85)
        self.assertEqual(h_verdict, "ACT_NOW")

    def test_json_dot_path_and_max_price_filter(self):
        wl = {
            "name": "mac-deals",
            "type": "json",
            "url": "https://api.example.com/listings",
            "items_path": "data.listings",
            "id_field": "sku",
            "title_field": "name",
            "url_field": "permalink",
            "summary_field": "specs",
            "price_field": "price_usd",
            "max_price": 800,
            "boost_keywords": {"32gb": 25, "m4": 20},
        }
        items = radar.parse_json_endpoint(SAMPLE_JSON_FEED, wl)
        self.assertEqual(len(items), 2)
        s1, v1, _, _ = radar.score_by_rules(items[0], wl)
        s2, v2, r2, _ = radar.score_by_rules(items[1], wl)
        self.assertEqual(v1, "ACT_NOW")
        self.assertGreaterEqual(s1, 90)
        self.assertEqual(v2, "SKIP")
        self.assertIn("exceeds max_price", r2)
        self.assertLessEqual(s2, 25)

    def test_exclusion_rule(self):
        rss_items = radar.parse_feed_xml(SAMPLE_RSS, "remote-usd-jobs")
        _, verdict, reason, _ = radar.score_by_rules(rss_items[1], radar.BUILTIN_PRESETS["remote-usd-jobs"])
        self.assertEqual(verdict, "SKIP")
        self.assertIn("Excluded", reason)


class LlmScorerAndWebhookTests(unittest.TestCase):
    def test_openai_anthropic_and_gemini_llm_scorers(self):
        sig = radar.parse_github_issues_json(SAMPLE_GH_ISSUES, "github-bounties")[0]
        wl = radar.BUILTIN_PRESETS["github-bounties"]

        # 1. OpenAI-compatible / local Ollama (no API key needed for localhost:11434)
        ai_ollama = {"enabled": True, "provider": "openai_compatible", "base_url": "http://127.0.0.1:11434/v1", "model": "qwen2.5:7b"}
        scored_o = radar.evaluate_signal(
            sig,
            wl,
            ai_ollama,
            llm_poster=lambda u, p, h: {"choices": [{"message": {"content": '```json\n{"score":94,"verdict":"ACT_NOW","reason":"Clear $500 Python scope","suggested_action":"Submit PR with unit tests"}\n```'}}]},
        )
        self.assertEqual(scored_o.score, 94)
        self.assertEqual(scored_o.scored_by, "llm:openai_compatible:qwen2.5:7b")

        # 2. Anthropic
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-ant-key"}):
            scored_a = radar.evaluate_signal(
                sig,
                wl,
                {"enabled": True, "provider": "anthropic", "model": "claude-3-5-haiku", "api_key_env": "ANTHROPIC_API_KEY"},
                llm_poster=lambda u, p, h: {"content": [{"text": '{"score":88,"verdict":"ACT_NOW","reason":"Great fit","suggested_action":"Claim issue"}'}]},
            )
            self.assertEqual(scored_a.score, 88)
            self.assertEqual(scored_a.scored_by, "llm:anthropic:claude-3-5-haiku")

        # 3. Gemini
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-gem-key"}):
            scored_g = radar.evaluate_signal(
                sig,
                wl,
                {"enabled": True, "provider": "gemini", "model": "gemini-2.5-flash", "api_key_env": "GEMINI_API_KEY"},
                llm_poster=lambda u, p, h: {"candidates": [{"content": {"parts": [{"text": '{"score":91,"verdict":"ACT_NOW","reason":"Fast win","suggested_action":"Open PR"}'}]}}]},
            )
            self.assertEqual(scored_g.score, 91)

    def test_all_five_webhook_channels(self):
        sig = radar.evaluate_signal(
            radar.parse_github_issues_json(SAMPLE_GH_ISSUES, "github-bounties")[0],
            radar.BUILTIN_PRESETS["github-bounties"],
            {"enabled": False},
        )
        calls = []
        notif_cfg = {
            "telegram": {"enabled": True, "bot_token_env": "TG_TOK", "chat_id_env": "TG_CHAT"},
            "discord": {"enabled": True, "webhook_url_env": "DC_WH"},
            "slack": {"enabled": True, "webhook_url_env": "SL_WH"},
            "feishu": {"enabled": True, "webhook_url_env": "FS_WH"},
            "bark": {"enabled": True, "server_url_env": "BK_SRV"},
        }
        env = {
            "TG_TOK": "123:abc",
            "TG_CHAT": "999",
            "DC_WH": "https://discord.example/wh",
            "SL_WH": "https://slack.example/wh",
            "FS_WH": "https://feishu.example/wh",
            "BK_SRV": "https://bark.example/key",
        }
        with mock.patch.dict(os.environ, env):
            sent = radar.dispatch_notifications(sig, notif_cfg, poster=lambda u, p, h: calls.append((u, p)) or {})
        self.assertEqual(sent, ["telegram", "discord", "slack", "feishu", "bark"])
        self.assertEqual(len(calls), 5)

    def test_slack_plain_text_success_is_accepted(self):
        with mock.patch.object(radar.urllib.request, "urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = b"ok\n"
            self.assertEqual(radar.http_post_json("https://slack.example/noop", {"text": "test"}), {})


class StoreScanWebAndCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "test.db")
        self.cfg_path = str(Path(self.tmp.name) / "watchlists.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_deduplication_and_status_lifecycle(self):
        store = radar.SignalStore(self.db_path)
        cfg = {
            "ai": {"enabled": False},
            "notifications": {"discord": {"enabled": True, "webhook_url_env": "DC_WH"}},
            "watchlists": [radar.BUILTIN_PRESETS["github-bounties"]],
        }
        posted = []
        with mock.patch.dict(os.environ, {"DC_WH": "https://discord.example/wh"}):
            r1 = radar.run_scan(
                cfg,
                store,
                fetcher=lambda u, h: json.dumps(SAMPLE_GH_ISSUES).encode("utf-8"),
                poster=lambda u, p, h: posted.append(p) or {},
            )
            self.assertEqual(r1["new_signals"], 1)
            self.assertEqual(r1["notified"], 1)
            self.assertEqual(len(posted), 1)

            # Second identical scan must deduplicate (0 new, 0 duplicate alerts)
            r2 = radar.run_scan(
                cfg,
                store,
                fetcher=lambda u, h: json.dumps(SAMPLE_GH_ISSUES).encode("utf-8"),
                poster=lambda u, p, h: posted.append(p) or {},
            )
            self.assertEqual(r2["new_signals"], 0)
            self.assertEqual(r2["notified"], 0)
            self.assertEqual(len(posted), 1)

    def test_failed_channel_retries_after_restart_without_resending_successful_channel(self):
        channels = ("discord", "slack")
        cfg = {
            "watchlists": [radar.BUILTIN_PRESETS["github-bounties"]],
            "notifications": {channel: {"enabled": True} for channel in channels},
        }
        urls = {channel: f"https://{channel}.example/test-webhook" for channel in channels}
        env = {f"{channel.upper()}_WEBHOOK_URL": url for channel, url in urls.items()}
        fetched = json.dumps(SAMPLE_GH_ISSUES).encode("utf-8")
        attempts = []

        def first_attempt(url, payload, headers):
            attempts.append(url)
            if url == urls["discord"]:
                raise radar.urllib.error.HTTPError(url, 503, "Service unavailable", {}, None)
            return {}

        with mock.patch.dict(os.environ, env):
            store = radar.SignalStore(self.db_path)
            first = radar.run_scan(cfg, store, fetcher=lambda *_: fetched, poster=first_attempt)
            self.assertEqual(first["notified"], 1)
            self.assertEqual(first["errors"], ["notify(github-bounties, discord): HTTP 503"])
            self.assertEqual(attempts, [urls["discord"], urls["slack"]])

            store = radar.SignalStore(self.db_path)
            second = radar.run_scan(
                cfg, store, fetcher=lambda *_: b'{"items": []}',
                poster=lambda url, *_: attempts.append(url) or {},
            )
            self.assertEqual(second["new_signals"], 0)
            self.assertEqual(second["notified"], 1)
            self.assertFalse(second["errors"])
            self.assertEqual(attempts, [urls["discord"], urls["slack"], urls["discord"]])
            self.assertEqual(store.query()[0]["notified"], 1)
            self.assertEqual(store.pending_notifications(["github-bounties"], channels), [])

    def test_delivery_claim_is_exclusive_and_can_be_recovered_after_expiry(self):
        store1 = radar.SignalStore(self.db_path)
        store2 = radar.SignalStore(self.db_path)
        wl = radar.BUILTIN_PRESETS["github-bounties"]
        sig = radar.evaluate_signal(
            radar.parse_github_issues_json(SAMPLE_GH_ISSUES, wl["name"])[0], wl, {},
        )
        self.assertTrue(store1.insert(sig, ("slack",)))
        with mock.patch.object(radar.time, "time", return_value=1000):
            first_lease = store1.claim_notification(sig.fingerprint, "slack")
            self.assertIsNotNone(first_lease)
            self.assertIsNone(store2.claim_notification(sig.fingerprint, "slack"))
        with mock.patch.object(radar.time, "time", return_value=1000 + radar.NOTIFICATION_LEASE_SECONDS):
            next_lease = store2.claim_notification(sig.fingerprint, "slack")
            self.assertIsNotNone(next_lease)
            store1.finish_notification(sig.fingerprint, "slack", first_lease, True)
            self.assertEqual(store2.query()[0]["notified"], 0)
            store2.finish_notification(sig.fingerprint, "slack", next_lease, True)
            self.assertEqual(store1.query()[0]["notified"], 1)

    def test_existing_database_gets_queue_without_changing_signal_status(self):
        store = radar.SignalStore(self.db_path)
        wl = radar.BUILTIN_PRESETS["github-bounties"]
        sig = radar.evaluate_signal(
            radar.parse_github_issues_json(SAMPLE_GH_ISSUES, wl["name"])[0], wl, {},
        )
        self.assertTrue(store.insert(sig))
        store.update_status(sig.fingerprint, "starred")
        store.mark_notified(sig.fingerprint)
        with store._connect() as conn:
            conn.execute("DROP TABLE pending_notifications")

        migrated = radar.SignalStore(self.db_path)
        self.assertEqual(migrated.query(status="starred")[0]["notified"], 1)
        self.assertEqual(migrated.pending_notifications([wl["name"]], ["slack"]), [])

    def test_web_dashboard_and_http_api(self):
        import contextlib, io, socket
        store = radar.SignalStore(self.db_path)
        with contextlib.redirect_stdout(io.StringIO()):
            radar.main(["--db", self.db_path, "--config", self.cfg_path, "demo", "--json"])
        handler_cls = radar.make_handler({"ai": {"enabled": False}, "watchlists": []}, store)

        def raw_http(method: str, path: str, body: str = ""):
            body_bytes = body.encode("utf-8")
            req_bytes = (
                f"{method} {path} HTTP/1.0\r\n"
                f"Host: localhost\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(body_bytes)}\r\n\r\n"
            ).encode("utf-8") + body_bytes

            class InMemSocket:
                def __init__(self, data: bytes) -> None:
                    self._r = io.BytesIO(data)
                    self.w = io.BytesIO()

                def makefile(self, mode: str, *args, **kwargs):
                    return self._r if "r" in mode else self.w

                def sendall(self, b: bytes) -> None:
                    self.w.write(b)

                def close(self) -> None:
                    pass

            class NonClosingBytesIO(io.BytesIO):
                def close(self) -> None:
                    pass

            sock = InMemSocket(req_bytes)
            sock.w = NonClosingBytesIO()
            handler_cls(sock, ("127.0.0.1", 0), None)
            raw_resp = sock.w.getvalue().decode("utf-8", errors="replace")
            head, payload = raw_resp.split("\r\n\r\n", 1)
            status_code = int(head.splitlines()[0].split()[1])
            return status_code, payload

        code_html, html_page = raw_http("GET", "/")
        self.assertEqual(code_html, 200)
        self.assertIn("AI Signal Radar", html_page)

        code_st, st_body = raw_http("GET", "/api/stats")
        self.assertEqual(code_st, 200)
        self.assertEqual(json.loads(st_body)["total"], 3)

        _, sig_body = raw_http("GET", "/api/signals?verdict=ACT_NOW")
        sigs = json.loads(sig_body)
        self.assertGreaterEqual(len(sigs["items"]), 2)
        fp = sigs["items"][0]["fingerprint"]

        code_up, up_body = raw_http("POST", "/api/status", json.dumps({"fingerprint": fp, "status": "starred"}))
        self.assertEqual(code_up, 200)
        self.assertTrue(json.loads(up_body)["updated"])

        _, after_body = raw_http("GET", "/api/stats")
        self.assertEqual(json.loads(after_body)["starred"], 1)
        self.assertEqual(raw_http("GET", "/api/signals?limit=bad")[0], 400)
        self.assertEqual(raw_http("POST", "/api/status", "[]")[0], 400)
        self.assertEqual(raw_http("POST", "/api/scan", "{}")[0], 200)

    def test_cli_init_demo_and_list_subcommands(self):
        init_res = subprocess.run(
            [sys.executable, str(ROOT / "radar.py"), "--config", self.cfg_path, "--db", self.db_path, "init"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(init_res.returncode, 0, init_res.stderr)
        self.assertTrue(os.path.exists(self.cfg_path))

        demo_res = subprocess.run(
            [sys.executable, str(ROOT / "radar.py"), "--config", self.cfg_path, "--db", self.db_path, "demo"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(demo_res.returncode, 0, demo_res.stderr)
        self.assertIn("AI SIGNAL RADAR DEMO", demo_res.stdout)

        list_res = subprocess.run(
            [sys.executable, str(ROOT / "radar.py"), "--config", self.cfg_path, "--db", self.db_path, "list", "--verdict", "ACT_NOW", "--json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(list_res.returncode, 0, list_res.stderr)
        payload = json.loads(list_res.stdout)
        self.assertGreaterEqual(payload["stats"]["act_now"], 2)

    def test_documented_scan_command_and_exit_codes(self):
        script = str(ROOT / "radar.py")
        cmd = [sys.executable, script, "scan", "--config", self.cfg_path, "--db", self.db_path, "--json"]
        missing = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(missing.returncode, 2)
        self.assertIn("Could not load config", missing.stderr)
        self.assertFalse(Path(self.db_path).exists())

        Path(self.cfg_path).write_text('{"watchlists": []}', encoding="utf-8")
        clean = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(clean.returncode, 0, clean.stderr)
        self.assertEqual(json.loads(clean.stdout)["errors"], [])

        failed = subprocess.run(cmd + ["--watchlist", "missing"], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 1, failed.stderr)
        self.assertIn("Unknown or disabled watchlist", json.loads(failed.stdout)["errors"][0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
