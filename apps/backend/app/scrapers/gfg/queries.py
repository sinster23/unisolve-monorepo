"""All GFG fetch/parse routines.

GFG has no public API; these are the undocumented endpoints its website uses.
If GFG changes anything, this is the only file that should need updating.

Parallelism (the whole point of this module):

  1. getUserInfo, the profile page and the solved list run concurrently
     (`asyncio.gather`) when the handle is already known.
  2. Every solved problem's submission list is fetched concurrently by
     `fetch_all_problem_submissions`; the client's semaphore caps in-flight requests.

  * fetch_user_info                  - who am I (validates the cookies, gives the handle)
  * fetch_expected_total             - total_problems_solved scraped from the profile page
  * fetch_solved_listing             - slugs of all solved problems
  * fetch_problem_submissions        - submissions of one problem
  * fetch_all_problem_submissions    - the above for many slugs in parallel
"""

import asyncio
import json
import re
from urllib.parse import quote

from pydantic import ValidationError

from app.scrapers.gfg.client import GFGClient
from app.scrapers.gfg.exceptions import AuthenticationError, GFGError, ResponseParsingError
from app.scrapers.gfg.models import ListingEntry, ProblemSubmissions

USER_INFO_URL = "https://commentapi.geeksforgeeks.org/api/v1/getUserInfo"
SOLVED_LIST_URL = "https://practiceapi.geeksforgeeks.org/api/v1/user/problems/submissions/"
PROBLEM_SUBMISSIONS_URL = (
    "https://practiceapi.geeksforgeeks.org/api/latest/problems/{slug}/submissions/user/"
)
PROFILE_PAGE_URL = "https://www.geeksforgeeks.org/profile/{handle}?tab=activity"

_BUCKETS = {"school", "basic", "easy", "medium", "hard"}


# ---- pure parsers --------------------------------------------------------------
def parse_solved_listing(payload) -> dict[str, ListingEntry]:
    """{slug: ListingEntry} from the solved-list response.

    Deliberately shape-tolerant: walks the JSON and collects every object with a 'slug'.
    """
    found: dict[str, ListingEntry] = {}

    def walk(node, bucket):
        if isinstance(node, dict):
            if isinstance(node.get("slug"), str):
                title = node.get("pname") or node.get("problem_name")
                found.setdefault(node["slug"], ListingEntry(title=title, bucket=bucket))
                return
            for k, v in node.items():
                key = k.lower() if isinstance(k, str) else None
                walk(v, key if key in _BUCKETS else bucket)
        elif isinstance(node, list):
            for v in node:
                walk(v, bucket)

    if isinstance(payload, dict):
        walk(payload.get("result", payload), None)
    else:
        walk(payload, None)
    return found


def extract_expected_total(html: str) -> int | None:
    """Total solved count embedded in the profile page's Next.js RSC chunks."""
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', html, re.S)
    try:
        full = "".join(json.loads('"' + c + '"') for c in chunks)
    except json.JSONDecodeError:
        return None
    m = re.search(r'"total_problems_solved":(\d+)', full)
    return int(m.group(1)) if m else None


# ---- fetchers ------------------------------------------------------------------
async def fetch_user_info(client: GFGClient) -> dict:
    body = await client.get_json(USER_INFO_URL, "getUserInfo")
    if not isinstance(body, dict) or not body.get("handle"):
        # 200 without a handle is how GFG answers an expired/invalid session.
        raise AuthenticationError("GFG getUserInfo returned no handle; cookies invalid or expired.")
    return body


async def fetch_expected_total(client: GFGClient, handle: str) -> int | None:
    html = await client.get_text(
        PROFILE_PAGE_URL.format(handle=quote(handle, safe="")), "profile page"
    )
    return extract_expected_total(html)


async def fetch_solved_listing(
    client: GFGClient, handle: str
) -> tuple[dict[str, ListingEntry], int | None]:
    """Returns ({slug: ListingEntry}, count reported by the endpoint or None)."""
    body = await client.post_json(
        SOLVED_LIST_URL,
        {"handle": handle, "requestType": "", "year": "", "month": ""},
        "solved list",
    )
    listing = parse_solved_listing(body)
    count = body.get("count") if isinstance(body, dict) else None
    return listing, count if isinstance(count, int) else None


async def fetch_problem_submissions(client: GFGClient, slug: str) -> ProblemSubmissions:
    url = PROBLEM_SUBMISSIONS_URL.format(slug=quote(slug, safe="")) + "?"
    body = await client.get_json(url, f"submissions of {slug}")
    try:
        return ProblemSubmissions.model_validate(body["results"])
    except (KeyError, TypeError, ValidationError) as exc:
        raise ResponseParsingError(f"Unexpected submissions shape for {slug}.") from exc


async def fetch_all_problem_submissions(
    client: GFGClient, slugs: list[str]
) -> tuple[dict[str, ProblemSubmissions], dict[str, str]]:
    """Fetch every problem's submissions concurrently.

    Returns (details, failures): `failures` maps slug -> short error message. One bad
    problem never aborts the rest.
    """

    async def one(slug: str):
        try:
            return slug, await fetch_problem_submissions(client, slug), None
        except GFGError as exc:
            return slug, None, str(exc)

    details: dict[str, ProblemSubmissions] = {}
    failures: dict[str, str] = {}
    for slug, result, err in await asyncio.gather(*(one(s) for s in slugs)):
        if result is not None:
            details[slug] = result
        else:
            failures[slug] = err or "unknown error"
    return details, failures
