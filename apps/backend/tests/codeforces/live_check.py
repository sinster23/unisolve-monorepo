"""One-shot live check for the Codeforces adapter.

Reads CODEFORCES_USERNAME from the root .env (no credentials are needed), runs
fetch_all(), and writes everything needed for verification to
tests/codeforces/live_output.json:

  - the normalized PlatformData
  - the raw (pre-normalization) `result` of user.info / user.rating, and a summary
    (item count + first items) of every user.status page
  - automatic consistency checks

Run from apps/backend:   python -m tests.codeforces.live_check
Takes a few seconds per request: the client waits 2s between calls on purpose.
"""

import asyncio
import json
import time
from collections import Counter
from pathlib import Path

from app.config import get_settings
from app.scrapers.codeforces.scraper import CodeforcesScraper

OUTPUT = Path(__file__).with_name("live_output.json")


def run_checks(data) -> list[dict]:
    stats, solved, meta = data.statistics, data.solved_problems, data.metadata
    by_diff = Counter(p.difficulty.value for p in solved)
    ids = [p.problem_id for p in solved]
    stamps = (
        [p.last_submitted_at for p in solved]
        + [p.first_submitted_at for p in solved]
        + [s.submitted_at for s in data.recent_submissions]
    )

    def check(name: str, ok: bool, detail: str = "") -> dict:
        return {"check": name, "ok": bool(ok), "detail": detail}

    return [
        check("statistics present", stats.total_solved is not None, f"total_solved={stats.total_solved}"),
        check("language/topic stats present", bool(stats.language_stats) and bool(stats.topic_stats)),
        check("contest stats present", stats.contest is not None and stats.contest.contests_attended is not None,
              str(stats.contest.model_dump() if stats.contest else None)),
        check("recent submissions present", len(data.recent_submissions) > 0, f"{len(data.recent_submissions)} items"),
        check("failed attempts kept", any(s.status != "Accepted" for s in data.recent_submissions),
              str(dict(Counter(s.status for s in data.recent_submissions)))),
        check("calendar present", data.activity is not None and bool(data.activity.daily_counts)),
        check("auth status", meta.auth_status.value == "not_required", meta.auth_status.value),
        check("submission history complete", meta.solved_problems_status.value == "complete",
              meta.solved_problems_status.value),
        check("solved count == statistics.total_solved", len(solved) == stats.total_solved,
              f"{len(solved)} vs {stats.total_solved}"),
        check("no duplicate solved problems", len(ids) == len(set(ids))),
        check("difficulty split matches statistics",
              (by_diff["easy"], by_diff["medium"], by_diff["hard"])
              == (stats.easy_solved, stats.medium_solved, stats.hard_solved),
              f"fetched={dict(by_diff)}"),
        check("unrated problems (info only)", True, f"{by_diff['unknown']} solved problems have no rating"),
        check("all timestamps timezone-aware", all(t is None or t.tzinfo is not None for t in stamps)),
        check("is_complete", meta.is_complete, f"failed_sections={meta.failed_sections}"),
    ]


async def main() -> int:
    settings = get_settings()
    print(f"Username configured : {bool(settings.codeforces_username)}")

    raw: list[dict] = []
    started = time.perf_counter()
    result: dict = {"error": None}

    async with CodeforcesScraper(settings=settings) as scraper:
        original = scraper._client.execute

        async def recording_execute(method, params=None):
            entry = {"method": method, "params": params}
            raw.append(entry)
            try:
                response = await original(method, params)
            except Exception as exc:  # recorded, then re-raised for the scraper to handle
                entry["error"] = f"{type(exc).__name__}: {exc}"
                raise
            if method == "user.status":
                entry["result_count"] = len(response)
                entry["result_first_items"] = response[:3]
            else:
                entry["result"] = response
            return response

        scraper._client.execute = recording_execute
        try:
            data = await scraper.fetch_all()
            result["platform_data"] = json.loads(data.model_dump_json())
            result["checks"] = run_checks(data)
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"

    result["elapsed_seconds"] = round(time.perf_counter() - started, 2)
    result["raw_requests"] = raw
    OUTPUT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWritten: {OUTPUT}  ({result['elapsed_seconds']}s, {len(raw)} requests)")

    if result["error"]:
        print(f"fetch_all raised: {result['error']}")
        return 1
    for item in result["checks"]:
        print(f"[{'PASS' if item['ok'] else 'FAIL'}] {item['check']}  {item['detail']}")
    for warning in result["platform_data"]["metadata"]["warnings"]:
        print(f"warning: {warning}")
    stats = result["platform_data"]["statistics"]
    print(
        f"\nCompare by hand with your profile page: solved={stats['total_solved']} "
        "(gym problems are included here; the profile's own count may differ slightly)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))