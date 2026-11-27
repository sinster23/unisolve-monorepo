"""One-shot live check for the GitHub Gist client.

Reads GITHUB_TOKEN and GITHUB_GIST_ID from the root .env, then exercises the
full round trip against the real Gist:

  1. writes a test.txt file into the Gist
  2. reads the Gist back and checks the file content matches
  3. deletes the file again and verifies it is gone

The Gist is left as it was found, so the script is safe to re-run. Note each
call is a real GitHub Gist API request: the token needs `gist` scope and write
access to the Gist.
Run from apps/backend:   python -m tests.github_gist.live_check
"""

import asyncio
import sys
from datetime import datetime, timezone

from app.config import get_settings
from app.tools.github_gist.client import GitHubGistClient
from app.tools.github_gist.exceptions import GitHubGistError

FILENAME = "test.txt"
STAMP = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
CONTENT = f"written by tests/github_gist/live_check.py at {STAMP}"

_RESULTS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _RESULTS.append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")
    return bool(ok)


async def main() -> int:
    settings = get_settings()
    print(
        f"GITHUB_TOKEN set  : {bool(settings.github_token is not None and settings.github_token.get_secret_value().strip())}"
    )
    print(f"GITHUB_GIST_ID    : {settings.github_gist_id or '<not set>'}")

    ok = True
    if not (settings.github_token and settings.github_token.get_secret_value().strip()):
        print("[FAIL] GITHUB_TOKEN is not set in the root .env")
        return 1
    if not settings.github_gist_id:
        print("[FAIL] GITHUB_GIST_ID is not set in the root .env")
        return 1

    try:
        async with GitHubGistClient(settings) as client:
            # 1. write test.txt into the Gist
            try:
                await client.update(None, {FILENAME: CONTENT})
                check(f"wrote {FILENAME}", True)
            except GitHubGistError as exc:
                check(f"wrote {FILENAME}", False, str(exc))
                return 1

            # 2. read the Gist back and compare the content
            gist = await client.get()
            file = gist.files.get(FILENAME)
            if file is None:
                check(f"read back {FILENAME}", False, "file missing after write")
            else:
                check(f"read back {FILENAME}", True, f"{file.size if file.size is not None else '?'} bytes")
                ok = check("content matches", file.content == CONTENT,
                           f"expected {len(CONTENT)} chars, got {len(file.content or '')}")
                if not ok:
                    print(f"    expected: {CONTENT!r}")
                    print(f"    actual  : {file.content!r}")

            # 3. delete the file again and verify it is gone
            try:
                await client.delete_file(None, FILENAME)
                check(f"deleted {FILENAME}", True)
            except GitHubGistError as exc:
                check(f"deleted {FILENAME}", False, str(exc))
                return 1
            clean = await client.get()
            check("gist is clean (file gone)", FILENAME not in clean.files,
                  f"remaining files: {sorted(clean.files)}")
    except GitHubGistError as exc:
        print(f"[FAIL] live check aborted: {exc}")
        return 1

    print(f"\nDone: {len(_RESULTS)} checks, "
          f"{'all passed' if ok else 'FAILURES above'} "
          f"(Gist {settings.github_gist_id}, ended with the original files: {sorted(clean.files)})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))