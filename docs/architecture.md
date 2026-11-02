# Architecture

The frontend is a Next.js application built with static export. Its generated
files are served by the FastAPI application in production.

During local development, Next.js dev server and Uvicorn run as two processes
through the root `npm run dev` command.

API routes use the `/api` prefix. The frontend must call APIs through a
configurable base URL: `http://127.0.0.1:8000` in development and an empty
base URL (same origin) in production.
