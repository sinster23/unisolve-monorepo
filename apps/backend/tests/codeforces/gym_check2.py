import asyncio
from collections import Counter

from app.scrapers.codeforces import normalizer as n
from app.scrapers.codeforces.scraper import CodeforcesScraper


async def main() -> None:
    async with CodeforcesScraper() as scraper:
        fetched = await scraper.fetch_submissions("maroonrk")
    subs = fetched.submissions
    ok = [s for s in subs if s.verdict == "OK"]

    def distinct(key, rows) -> int:
        return len({key(s) for s in rows})

    def prob(s):
        return s.problem

    print("current string key         :", distinct(n._problem_key, ok))
    print("(contest, index) tuple     :", distinct(lambda s: (n.contest_id_of(s), prob(s).index), ok))
    print("(set, contest, index, name):", distinct(
        lambda s: (prob(s).problemset_name, n.contest_id_of(s), prob(s).index, prob(s).name), ok))
    print("by name only               :", distinct(lambda s: prob(s).name, ok))
    print("OK + PARTIAL               :", distinct(n._problem_key, [s for s in subs if s.verdict in ("OK", "PARTIAL")]))
    print("accepted, no contest id    :", sum(1 for s in ok if n.contest_id_of(s) is None))
    print("accepted, numeric index    :", sum(1 for s in ok if prob(s).index[:1].isdigit()))
    print("verdicts                   :", dict(Counter(s.verdict for s in subs)))


asyncio.run(main())