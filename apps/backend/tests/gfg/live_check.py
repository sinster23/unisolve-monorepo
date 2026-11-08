"""One-shot live check for the GFG adapter.

Reads GFG_COOKIE (or GFG_USERNAME / GFG_PASSWORD) from the root .env or the
environment, runs fetch_all(), and writes everything needed for verification to
tests/gfg/live_output.json:

  - the normalized PlatformData
  - the raw (pre-normalization) fetch: solved listing, per-problem submissions,
    failed sections and warnings
  - a log of every HTTP request made (URL, status, timing, start offset; no bodies,
    no headers)
  - peak number of requests in flight (proves the fetch really ran in parallel)
  - automatic consistency checks

Run from apps/backend:   python -m tests.gfg.live_check
Cookie values and the password are never written; the script aborts if they would be.
"""

import asyncio
import json
import time
from pathlib import Path

from app.config import get_settings
from app.scrapers.gfg.client import GFGConfig
from app.scrapers.gfg.normalizer import is_correct
from app.scrapers.gfg.scraper import GFGScraper

OUTPUT = Path(__file__).with_name("live_output.json")


def peak_concurrency(requests: list[dict]) -> int:
    events = []
    for r in requests:
        if "start" in r and "seconds" in r:
            events.append((r["start"], 1))
            events.append((r["start"] + r["seconds"], -1))
    peak = cur = 0
    for _, delta in sorted(events, key=lambda e: (e[0], e[1])):
        cur += delta
        peak = max(peak, cur)
    return peak


def run_checks(data, raw, peak: int, n_problem_requests: int) -> list[dict]:
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
    unknown_diff = [p.problem_id for p in solved if p.difficulty.value == "unknown"]
    raw_sub_total = sum(len(d.submissions) for d in raw.details.values()) if raw else None
    raw_correct = (
        sum(is_correct(s) for d in raw.details.values() for s in d.submissions) if raw else None
    )
    dated = [s.submitted_at for s in subs if s.submitted_at]
    newest_first = all(a >= b for a, b in zip(dated, dated[1:]))
    act = data.activity

    return [
        check("auth status", meta.auth_status.value == "authenticated", meta.auth_status.value),
        check("profile url present", data.profile_url is not None, str(data.profile_url)),
        check("solved list complete", meta.solved_problems_status.value == "complete",
              f"{meta.solved_problems_status.value}, expected={meta.solved_problems_expected}"),
        check("expected total known", meta.solved_problems_expected is not None,
              str(meta.solved_problems_expected)),
        check("solved count == statistics.total_solved", len(solved) == stats.total_solved,
              f"{len(solved)} vs {stats.total_solved}"),
        check("solved count == profile total", len(solved) == meta.solved_problems_expected,
              f"{len(solved)} vs {meta.solved_problems_expected}"),
        check("solved count == raw listing", raw is not None and len(solved) == len(raw.listing),
              f"{len(solved)} vs {len(raw.listing) if raw else None}"),
        check("no duplicate solved problems", len(keys) == len(set(keys))),
        check("every solved problem has first_submitted_at", not any(f is None for f in firsts),
              f"missing for {missing_dates[:10]}" if missing_dates else ""),
        check("every solved problem has last_submitted_at", not any(l is None for l in lasts),
              f"missing for {missing_dates[:10]}" if missing_dates else ""),
        check("first_submitted_at <= last_submitted_at", not bad_order, f"violations: {bad_order[:10]}"),
        check("no unknown difficulty", not unknown_diff, f"unknown for {unknown_diff[:10]}"),
        check("difficulty buckets sum to total",
              (stats.easy_solved + stats.medium_solved + stats.hard_solved + len(unknown_diff))
              == stats.total_solved),
        check("submissions == raw submissions", raw_sub_total == len(subs),
              f"{len(subs)} vs {raw_sub_total}"),
        check("total_submissions == len(submissions)", stats.total_submissions == len(subs)),
        check("accepted_submissions == raw correct", stats.accepted_submissions == raw_correct,
              f"{stats.accepted_submissions} vs {raw_correct}"),
        check("submission ids unique", len({s.submission_id for s in subs}) == len(subs)),
        check("submissions newest first", newest_first),
        check("language stats <= total submissions", sum(stats.language_stats.values()) <= len(subs),
              str(stats.language_stats)),
        check("all timestamps timezone-aware", all(t is None or t.tzinfo is not None for t in stamps)),
        check("activity present", act is not None and act.total_active_days == len(act.daily_counts),
              f"active_days={act.total_active_days if act else None}, "
              f"streak={act.current_streak if act else None}"),
        check("one request per solved problem", n_problem_requests >= len(solved),
              f"{n_problem_requests} problem requests for {len(solved)} problems"),
        check("requests ran in parallel", len(solved) < 2 or peak > 1, f"peak in flight={peak}"),
        check("is_complete", meta.is_complete, f"failed_sections={meta.failed_sections[:10]}"),
    ]


async def main() -> int:
    settings = get_settings()
    cfg = GFGConfig.from_settings(settings)
    print(f"Cookie configured   : {bool(cfg.cookie)}")
    print(f"Username configured : {bool(cfg.username)}")
    print(f"Credentials present : {cfg.has_credentials}")
    print(f"Concurrency         : {cfg.concurrency}")

    requests: list[dict] = []
    captured: dict = {}
    started = time.perf_counter()
    result: dict = {"error": None}
    secrets: list[str] = []

    async with GFGScraper(settings=settings) as scraper:
        http = scraper._client._http
        original_request = http.request
        original_fetch_profile = scraper.fetch_profile

        async def recording_request(method, url, *args, **kwargs):
            # Only method, URL and timing are logged: never headers (cookies) or bodies (password).
            entry = {"method": method, "url": str(url), "start": round(time.perf_counter() - started, 3)}
            requests.append(entry)
            t0 = time.perf_counter()
            try:
                resp = await original_request(method, url, *args, **kwargs)
            except Exception as exc:  # recorded, then re-raised for the scraper to handle
                entry["error"] = type(exc).__name__
                raise
            finally:
                entry["seconds"] = round(time.perf_counter() - t0, 3)
            entry["status"] = resp.status_code
            return resp

        async def capturing_fetch_profile(*args, **kwargs):
            raw = await original_fetch_profile(*args, **kwargs)
            captured["raw"] = raw
            return raw

        http.request = recording_request
        scraper.fetch_profile = capturing_fetch_profile
        try:
            data = await scraper.fetch_all()
            result["platform_data"] = json.loads(data.model_dump_json())
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        secrets = [v for v in scraper._client._cookies.values() if len(v) >= 16]

    raw = captured.get("raw")
    peak = peak_concurrency(requests)
    n_problem = sum("/submissions/user/" in r["url"] for r in requests)
    if "platform_data" in result and raw is not None:
        from app.models.common import PlatformData

        result["checks"] = run_checks(PlatformData.model_validate(result["platform_data"]), raw, peak, n_problem)
    elif "platform_data" in result:
        result["checks"] = []

    result["elapsed_seconds"] = round(time.perf_counter() - started, 2)
    result["request_count"] = len(requests)
    result["peak_requests_in_flight"] = peak
    result["requests"] = requests
    if raw is not None:
        result["raw_fetch"] = {
            "handle": raw.handle,
            "expected": raw.expected,
            "list_ok": raw.list_ok,
            "failed_sections": raw.failed_sections,
            "warnings": raw.warnings,
            "listing": {k: v.model_dump() for k, v in raw.listing.items()},
            "details": {k: v.model_dump() for k, v in raw.details.items()},
        }
    text = json.dumps(result, indent=2, ensure_ascii=False)

    if cfg.password:
        secrets.append(cfg.password)
    if cfg.cookie:
        secrets.extend(v for v in (p.split("=", 1)[-1].strip() for p in cfg.cookie.split(";")) if len(v) >= 16)
    for secret in secrets:
        if secret and secret in text:
            print("ABORTED: output contained a credential value; nothing was written.")
            return 2

    OUTPUT.write_text(text, encoding="utf-8")
    print(f"\nWritten: {OUTPUT}")
    print(f"{len(requests)} requests, peak in flight {peak}, {result['elapsed_seconds']}s")
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
