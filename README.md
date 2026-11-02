# Developer Profile Aggregator

An open-source, self-hostable dashboard that aggregates developer statistics from GitHub, LeetCode, Codeforces and HackerRank.

## Architecture

- **Monorepo:** npm workspaces
- **Frontend:** Next.js + TypeScript, configured for static export
- **Backend:** Python + FastAPI
- **Production serving:** FastAPI serves both `/api/*` endpoints and the exported frontend from one application and one public URL
- **Database:** None planned for the initial version
- **Docker:** Optional; not required for local development

During development, Next.js and FastAPI run as two local processes started together by `npm run dev`. In production, Next.js is built into static files under `apps/frontend/out`, and FastAPI serves those files.

## Requirements

- Node.js LTS and npm
- Python 3.11+

## Setup

### 1. Install frontend and root tooling

From the repository root:

```bash
npm install
```

### 2. Install backend dependencies

Windows PowerShell:

```powershell
cd apps/backend
py -3 -m venv .venv
.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
cd ../..
```

macOS/Linux:

```bash
cd apps/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd ../..
```

### 3. Run both apps together

Activate the backend virtual environment first, then from the repository root:

```bash
npm run dev
```

- Frontend: http://localhost:3000
- Backend: http://127.0.0.1:8000
- API health: http://127.0.0.1:8000/api/health
- API docs: http://127.0.0.1:8000/docs

### 4. Production build

```bash
npm run build
```

This generates `apps/frontend/out`. Start the combined production application with:

```bash
npm start
```

Then open http://localhost:8000.

## Hosting

The intended production model is one FastAPI application serving the frontend and API. Hosts that support Python/FastAPI can run this as one application. Vercel also documents FastAPI deployments and static frontend support; its function runtime, build configuration, duration and bundle limits must be tested and documented separately before claiming full compatibility.

Docker support may be added as an optional deployment path. It is not needed for local development.

## Initial provider ownership

- Developer 1: GitHub and LeetCode
- Developer 2: Codeforces and HackerRank

The provider interface, response models and error conventions should be agreed jointly before parallel scraper implementation.
