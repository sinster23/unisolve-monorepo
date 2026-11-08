import asyncio

from app.scrapers.codeforces import normalizer as n
from app.scrapers.codeforces.scraper import CodeforcesScraper


async def main() -> None:
    async with CodeforcesScraper() as scraper:
        fetched = await scraper.fetch_submissions("maroonrk")
    accepted = [s for s in fetched.submissions if s.verdict == "OK"]
    all_keys = {n._problem_key(s) for s in accepted}
    gym_keys = {n._problem_key(s) for s in accepted if n.is_gym(s)}
    print(f"solved incl. gym: {len(all_keys)}  gym only: {len(gym_keys)}  excl. gym: {len(all_keys - gym_keys)}")


asyncio.run(main())