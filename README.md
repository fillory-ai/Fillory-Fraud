# React App

A full-stack template with React + TypeScript + Tailwind CSS + shadcn/ui on the frontend and FastAPI on the backend.

## Quick Start

```bash
bash start.sh
```

This installs dependencies and starts both the Vite dev server (frontend) and the FastAPI backend.

## Project Structure

```
├── src/                  # React frontend
│   ├── components/ui/    # shadcn/ui components
│   ├── lib/              # Utilities
│   ├── App.tsx           # Main app component
│   └── main.tsx          # Entry point
├── app.py                # FastAPI entry point
├── routes.py             # API routes + SPA fallback
├── secrets_utils.py      # OAuth token utility
├── start.sh              # Dev server launcher
└── deploy.sh             # Build & deploy script
```

## Development

- Frontend: Edit `src/App.tsx` and files in `src/`
- Backend API: Add routes in `routes.py`
- The Vite dev server proxies `/api` requests to the FastAPI backend

## Deploy

```bash
bash deploy.sh
```

Builds the frontend and deploys to Modal.
