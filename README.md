# fafo · Attendance

FastAPI + PostgreSQL backend (`app/`) and React + Vite frontend (`frontend/`).

## Run everything locally (Docker)

```sh
docker compose up -d --build
```

| URL | What |
|---|---|
| http://localhost:8080 | Web app (nginx serves the build and proxies `/api` to the API) |
| http://localhost:8000/docs | Swagger UI |

With `SEED_DEMO_DATA=true` (the compose default) the API creates demo accounts on first start.
All use the password `demo-password-123`:

- Teacher: `elena.ionescu@university.edu`
- Students: `alex.popescu@student.university.edu`, `maria.ionescu@…`, `andrei.pavel@…`, `sofia.dumitru@…`, `david.radu@…`

The login page also has "Student demo" / "Teacher demo" buttons. Reset all data with
`docker compose down -v`.

## Develop with hot reload

```sh
# Backend (SQLite by default, see .env.example)
cp .env.example .env            # then edit DATABASE_URL, or delete it to use SQLite
uv run uvicorn app.main:app --reload

# Frontend — http://localhost:5173, proxies /api to localhost:8000 (override with API_URL)
cd frontend && pnpm install && pnpm dev
```

Run backend tests with `uv run --with pytest --with httpx pytest`.
