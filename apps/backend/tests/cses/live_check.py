"""One-shot live check for the CSES adapter.

Reads CSES_USERNAME / CSES_PASSWORD from the root .env, runs fetch_all(), and
writes everything needed for verification to tests/cses/live_output.json:

  - the normalized PlatformData
  - the raw (pre-normalization) parsed problem set, including the oldest/newest
    Accepted solve found for every solved task
  - a log of every HTTP request made (URL, status, timing; no bodies)
  - automatic consistency checks

Run from apps/backend:   python -m tests.cses.live_check
The password and PHPSESSID are never written; the script aborts if they would be.
"""

import asyncio
import json
import time
from pathlib import Path

from app.config import get_settings
from app.scrapers.cses import queries
from app.scrapers.cses.scraper import CSESScraper

OUTPUT = Path(__file__).with_name("live_output.json")


def run_checks(data, raw_problemset: dict | None) -> list[dict]:
    stats, solved, meta = data.statistics, data.solved_problems, data.metadata
    subs = data.recent_submissions
    keys = [p.problem_id or p.title for p in solved]
    firsts = [p.first_submitted_at for p in solved]
    lasts = [p.last_submitted_at for p in solved]
    stamps = firsts + lasts + [s.submitted_at for s in subs]

    def check(name: str, ok: bool, detail: str = "") -> dict:
        return {"check": name, "ok": bool(ok), "detail": detail}

    missing_dates = [p.problem_id for p in solved if p.first_submitted_at is None or p.last_submitted_at is None]
    bad_order = [
        p.problem_id
        for p in solved
        if p.first_submitted_at and p.last_submitted_at and p.first_submitted_at > p.last_submitted_at
    ]
    # Submission list is "oldest solve per task", so it must line up with first_submitted_at.
    first_by_id = {p.problem_id: p.first_submitted_at for p in solved}
    sub_mismatch = [s.problem_id for s in subs if first_by_id.get(s.problem_id) != s.submitted_at]
    expected = len(raw_problemset["solved"]) if raw_problemset else None

    return [
        check("statistics present", stats.total_solved is not None, f"total_solved={stats.total_solved}"),
        check("auth status", meta.auth_status.value == "authenticated", meta.auth_status.value),
        check("profile url present", data.profile_url is not None, str(data.profile_url)),
        check("solved list complete", meta.solved_problems_status.value == "complete",
              f"{meta.solved_problems_status.value}, expected={meta.solved_problems_expected}"),
        check("solved count == statistics.total_solved", len(solved) == stats.total_solved,
              f"{len(solved)} vs {stats.total_solved}"),
        check("solved count == raw solved tasks", expected is not None and len(solved) == expected,
              f"{len(solved)} vs {expected}"),
        check("no duplicate solved problems", len(keys) == len(set(keys))),
        check("every solved problem has first_submitted_at", not any(f is None for f in firsts),
              f"missing for {missing_dates[:10]}" if missing_dates else ""),
        check("every solved problem has last_submitted_at", not any(l is None for l in lasts),
              f"missing for {missing_dates[:10]}" if missing_dates else ""),
        check("first_submitted_at <= last_submitted_at", not bad_order, f"violations: {bad_order[:10]}"),
        check("recent submissions == one per solved problem", len(subs) == len(solved),
              f"{len(subs)} vs {len(solved)}"),
        check("submissions all Accepted", all(s.status == "Accepted" for s in subs)),
        check("submissions match first_submitted_at", not sub_mismatch, f"mismatches: {sub_mismatch[:10]}"),
        check("submission ids unique", len({s.submission_id for s in subs}) == len(subs)),
        check("all timestamps timezone-aware", all(t is None or t.tzinfo is not None for t in stamps)),
        check("unsupported stats left None (not 0)",
              stats.easy_solved is None and stats.total_submissions is None and not stats.topic_stats),
        check("is_complete", meta.is_complete, f"failed_sections={meta.failed_sections}"),
    ]


async def main() -> int:
    settings = get_settings()
    print(f"Username configured : {bool(settings.cses_username)}")
    print(f"Credentials present : {settings.has_cses_credentials}")

    requests: list[dict] = []
    captured: dict = {}
    started = time.perf_counter()
    result: dict = {"error": None}

    original_fetch_problemset = queries.fetch_problemset

    async def capturing_fetch_problemset(client):
        raw = await original_fetch_problemset(client)
        # Same dict object that fetch_solved_details later fills in place, so by the
        # end of the run it also holds oldest_solve / newest_solve for every task.
        captured["problemset"] = raw
        return raw

    queries.fetch_problemset = capturing_fetch_problemset  # scraper looks it up at call time
    try:
        async with CSESScraper(settings=settings) as scraper:
            http = scraper._client.http
            original_get = http.get

            async def recording_get(url, *args, **kwargs):
                entry = {"method": "GET", "url": str(url)}
                requests.append(entry)
                t0 = time.perf_counter()
                try:
                    resp = await original_get(url, *args, **kwargs)
                except Exception as exc:  # recorded, then re-raised for the scraper to handle
                    entry["error"] = f"{type(exc).__name__}: {exc}"
                    raise
                finally:
                    entry["seconds"] = round(time.perf_counter() - t0, 3)
                entry["status"] = resp.status_code
                return resp

            http.get = recording_get
            try:
                data = await scraper.fetch_all()
                result["platform_data"] = json.loads(data.model_dump_json())
                result["checks"] = run_checks(data, captured.get("problemset"))
            except Exception as exc:
                result["error"] = f"{type(exc).__name__}: {exc}"
            session_id = http.cookies.get("PHPSESSID")
    finally:
        queries.fetch_problemset = original_fetch_problemset

    result["elapsed_seconds"] = round(time.perf_counter() - started, 2)
    result["request_count"] = len(requests)
    result["requests"] = requests
    result["raw_problemset"] = captured.get("problemset")
    text = json.dumps(result, indent=2, ensure_ascii=False)

    secrets = [session_id]
    if settings.cses_password:
        secrets.append(settings.cses_password.get_secret_value())
    for secret in secrets:
        if secret and secret in text:
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
