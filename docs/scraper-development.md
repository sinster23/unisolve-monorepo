# Scraper development

Each platform provider must be independent of FastAPI routes and frontend code.
Each provider will return a validated platform-specific Pydantic model.

Initial provider ownership:
- Developer 1: GitHub and LeetCode
- Developer 2: Codeforces and HackerRank

Agree on the shared provider contract and error conventions before parallel
implementation. Prefer documented APIs and respect platform policies and limits.
