"""Codeforces API endpoints and parameter builders.

Public, unauthenticated methods only. The API is documented at
https://codeforces.com/apiHelp but may change; keep every endpoint here so it can
be updated in one place.
"""

BASE_URL = "https://codeforces.com/api"

USER_INFO = "user.info"  # profile + current/max rating
USER_STATUS = "user.status"  # submissions, newest first
USER_RATING = "user.rating"  # rated-contest history, oldest first


def user_info_params(handle: str) -> dict[str, str]:
    return {"handles": handle}


def user_status_params(handle: str, start: int, count: int) -> dict[str, str | int]:
    """`start` is 1-based: from=1 is the newest submission."""
    return {"handle": handle, "from": start, "count": count}


def user_rating_params(handle: str) -> dict[str, str]:
    return {"handle": handle}