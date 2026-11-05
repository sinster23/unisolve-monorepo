"""One-shot live check for the LeetCode adapter.

Reads LEETCODE_USERNAME / LEETCODE_SESSION / LEETCODE_CSRF_TOKEN from the root
.env, runs fetch_all(), and writes everything needed for verification to
tests/leetcode/live_output.json:

  - the normalized PlatformData
  - the raw (pre-normalization) GraphQL `data` of every request made
  - automatic consistency checks

Run from apps/backend:   python -m tests.leetcode.live_check
Cookies and tokens are never written; the script aborts if they would be.
"""

import asyncio
import json
import time
from collections import Counter
from pathlib import Path

from app.config import get_settings
from app.scrapers.leetcode.scraper import LeetCodeScraper

OUTPUT = Path(__file__).with_name("live_output.json")


def run_checks(data) -> list[dict]:
    stats, solved, meta = data.statistics, data.solved_problems, data.metadata
    by_diff = Counter(p.difficulty.value for p in solved)
    urls = [str(p.problem_url) for p in solved]
    stamps = [p.last_submitted_at for p in solved] + [s.submitted_at for s in data.recent_submissions]

    def check(name: str, ok: bool, detail: str = "") -> dict:
        return {"check": name, "ok": bool(ok), "detail": detail}

    return [
        check("statistics present", stats.total_solved is not None, f"total_solved={stats.total_solved}"),
        check("language/topic stats present", bool(stats.language_stats) and bool(stats.topic_stats)),
        check("recent submissions present", len(data.recent_submissions) > 0, f"{len(data.recent_submissions)} items"),
        check("failed attempts kept", any(s.status != "Accepted" for s in data.recent_submissions),
              str(dict(Counter(s.status for s in data.recent_submissions)))),
        check("calendar present", data.activity is not None and bool(data.activity.daily_counts)),
        check("auth status", meta.auth_status.value == "authenticated", meta.auth_status.value),
        check("solved list complete", meta.solved_problems_status.value == "complete",
              f"{meta.solved_problems_status.value}, expected={meta.solved_problems_expected}"),
        check("solved count == statistics.total_solved", len(solved) == stats.total_solved,
              f"{len(solved)} vs {stats.total_solved}"),
        check("no duplicate solved problems", len(urls) == len(set(urls))),
        check("difficulty split matches statistics",
              (by_diff["easy"], by_diff["medium"], by_diff["hard"])
              == (stats.easy_solved, stats.medium_solved, stats.hard_solved),
              f"fetched={dict(by_diff)}"),
        check("no UNKNOWN difficulty", by_diff["unknown"] == 0),
        check("all timestamps timezone-aware", all(t is None or t.tzinfo is not None for t in stamps)),
        check("is_complete", meta.is_complete, f"failed_sections={meta.failed_sections}"),
    ]


async def main() -> int:
    settings = get_settings()
    print(f"Username configured : {bool(settings.leetcode_username)}")
    print(f"Credentials present : {settings.has_leetcode_credentials}")

    raw: list[dict] = []
    started = time.perf_counter()
    result: dict = {"error": None}

    async with LeetCodeScraper(settings=settings) as scraper:
        original = scraper._client.execute

        async def recording_execute(query, variables=None, authenticated=False, referer=None):
            name = query.split("(")[0].split("{")[0].replace("query", "").strip()
            entry = {"operation": name, "authenticated": authenticated, "variables": variables}
            raw.append(entry)
            try:
                entry["response_data"] = await original(query, variables, authenticated, referer)
            except Exception as exc:  # recorded, then re-raised for the scraper to handle
                entry["error"] = f"{type(exc).__name__}: {exc}"
                raise
            return entry["response_data"]

        scraper._client.execute = recording_execute
        try:
            data = await scraper.fetch_all()
            result["platform_data"] = json.loads(data.model_dump_json())
            result["checks"] = run_checks(data)
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"

    result["elapsed_seconds"] = round(time.perf_counter() - started, 2)
    result["raw_requests"] = raw
    text = json.dumps(result, indent=2, ensure_ascii=False)

    for secret in (settings.leetcode_session, settings.leetcode_csrf_token):
        if secret and secret.get_secret_value() and secret.get_secret_value() in text:
            print("ABORTED: output contained a credential value; nothing was written.")
            return 2

    OUTPUT.write_text(text, encoding="utf-8")
    print(f"\nWritten: {OUTPUT}")
    if result["error"]:
        print(f"fetch_all raised: {result['error']}")
        return 1
    for item in result["checks"]:
        print(f"[{'PASS' if item['ok'] else 'FAIL'}] {item['check']}  {item['detail']}")
    for warning in result["platform_data"]["metadata"]["warnings"]:
        print(f"warning: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))