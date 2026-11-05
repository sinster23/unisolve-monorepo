"""All CSES page fetch/parse routines.

CSES has no API: everything is read from HTML pages with regular expressions.
The functions below are the original scraping logic, copied unchanged. If CSES
changes its markup, this is the only file that should need updating.

  * fetch_problemset      - problem set page, fetched once (needs a session);
                            also extracts the profile href (class "account")
  * parse_task_list       - HTML -> list of task dicts
  * fetch_solved_details  - runs get_oldest_solve for every solved task in parallel
  * get_solve_range       - walks a task's paginated submission list, returns the
                            oldest and newest Accepted solve, plus the latest
                            submission of any status
  * get_oldest_solve      - oldest Accepted solve only (wrapper)
"""

import asyncio
import re
from datetime import datetime

from app.scrapers.cses.client import fetch_url


# Result icon class (row's "task-score ..." class) -> human readable status.
SCORE_STATUS = {
    'full': 'Accepted',
    'partial': 'Partial',
    'zero': 'Not Accepted',
    'pending': 'Pending',
    'compile-err': 'Compile Error',
    'skipped': 'Skipped',
}


def score_to_status(score_class):
    """Map the CSES result icon class to a status string."""
    for key, label in SCORE_STATUS.items():
        if key in score_class:
            return label
    return score_class.replace('icon', '').strip() or 'Unknown'


async def get_solve_range(client, task_id):
    """
    Fetch the view page for a solved task and find the oldest and newest
    successful solves.

    The view page lists all submissions (newest first), paginated. We walk
    through every page and keep the rows with the smallest and largest
    timestamp where the result is "full" (Accepted).

    While walking the same pages it also records the latest submission of ANY
    status (accepted or not), so no extra requests are needed.

    Returns:
        (oldest, newest, latest):
          oldest/newest: dict with 'time_str', 'language', 'unix_time', or None
                         if no Accepted submission was found.
          latest:        dict with 'time_str', 'language', 'unix_time', 'status',
                         or None if no submission row could be parsed.
    """
    page = 1
    oldest = None
    oldest_time = None
    newest = None
    newest_time = None
    latest = None
    latest_time = None

    while True:
        if page == 1:
            url = f"https://cses.fi/problemset/view/{task_id}/"
        else:
            url = f"https://cses.fi/problemset/view/{task_id}/{page}/"

        html = await fetch_url(client, url)

        # Each submission row: <tr><td >TIME</td><td >LANG</td>...<td class="task-score icon STATUS"></td>
        rows = re.findall(r'<tr>(.*?)</tr>', html, re.DOTALL)

        for row in rows:
            if '<th' in row:  # skip header row
                continue

            time_m = re.search(r'<td >([^<]*)</td>', row)
            if not time_m:
                continue
            lang_m = re.search(r'<td >([^<]*)</td>', row[time_m.end():])
            score_m = re.search(r'class="task-score([^"]*)"', row)

            if not lang_m:
                continue

            time_str = time_m.group(1).strip()
            lang = lang_m.group(1).strip()
            score_class = score_m.group(1).strip() if score_m else ""

            try:
                dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
                ts = int(dt.timestamp())
            except ValueError:
                continue

            # Latest submission of any status. Strict ">" keeps the first row seen
            # on ties (the list is newest first).
            if latest_time is None or ts > latest_time:
                latest_time = ts
                latest = {
                    'time_str': time_str,
                    'language': lang,
                    'unix_time': ts,
                    'status': score_to_status(score_class),
                }

            # Oldest/newest only count Accepted ("full") submissions
            if 'full' not in score_class:
                continue

            solve = {
                'time_str': time_str,
                'language': lang,
                'unix_time': ts,
            }
            if oldest_time is None or ts < oldest_time:
                oldest_time = ts
                oldest = solve
            if newest_time is None or ts > newest_time:
                newest_time = ts
                newest = solve

        # Follow to next page if it exists
        if f'href="/problemset/view/{task_id}/{page + 1}/"' not in html:
            break
        page += 1

    return oldest, newest, latest


async def get_oldest_solve(client, task_id):
    """Oldest successful solve of a task (see get_solve_range), or None."""
    oldest, _, _ = await get_solve_range(client, task_id)
    return oldest


def parse_task_list(html):
    """Parse the problem set HTML into a list of task dicts."""
    # Tasks have no </li> closing tag - split on <li class="task">
    parts = html.split('<li class="task">')[1:]
    results = []
    for part in parts:
        end = re.search(r'<li[^>]*>|</ul>', part)
        content = part[:end.start()] if end else part

        id_m = re.search(r'/problemset/task/(\d+)', content)
        name_m = re.search(r'>([^<]+)</a>', content)
        detail_m = re.search(r'class="detail">([^<]*)<', content)
        score_m = re.search(r'class="task-score([^"]*)"', content)
        score_class = score_m.group(1).strip() if score_m else ""

        results.append({
            'id': id_m.group(1) if id_m else "?",
            'name': name_m.group(1) if name_m else "?",
            'detail': detail_m.group(1) if detail_m else "?",
            'score': score_class,
        })
    return results


async def fetch_problemset(client):
    """
    Fetch the CSES problem set page ONCE and parse task information.

    Returns:
        dict: Statistics and task lists categorized by status
    """
    html = await fetch_url(client, "https://cses.fi/problemset/")

    results = parse_task_list(html)

    # Profile link: the href of the element with class "account" (navbar).
    # The tag itself may carry the href; otherwise take the first href right after it.
    profile_href = None
    account_m = re.search(r'<[^>]*class="(?:[^"]*\s)?account(?:\s[^"]*)?"[^>]*>', html)
    if account_m:
        href_m = (
            re.search(r'href="([^"]+)"', account_m.group(0))
            or re.search(r'href="([^"]+)"', html[account_m.end():account_m.end() + 300])
        )
        if href_m:
            profile_href = href_m.group(1)

    # Categorize by score status
    solved = [r for r in results if 'full' in r['score']]
    attempted = [r for r in results if 'zero' in r['score']]
    partial = [r for r in results if 'partial' in r['score']]
    skipped = [r for r in results if 'skipped' in r['score']]
    pending = [r for r in results if 'pending' in r['score']]
    compile_err = [r for r in results if 'compile-err' in r['score']]
    not_attempted = [r for r in results if r['score'] in ('', ' icon', ' ')]

    return {
        'total': len(results),
        'solved': solved,
        'attempted': attempted,
        'partial': partial,
        'skipped': skipped,
        'pending': pending,
        'compile_err': compile_err,
        'not_attempted': not_attempted,
        'profile_href': profile_href,
    }


async def fetch_solved_details(client, solved_tasks):
    """
    For the successfully solved problems only, fetch each task's view page
    in parallel to get the oldest and newest successful solve details and the
    latest submission (any status).
    """
    if not solved_tasks:
        return

    tasks = [get_solve_range(client, task['id']) for task in solved_tasks]
    solves = await asyncio.gather(*tasks)
    for task, (oldest, newest, latest) in zip(solved_tasks, solves):
        task['oldest_solve'] = oldest
        task['newest_solve'] = newest
        task['latest_submission'] = latest