# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Setup
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Run dev server (http://127.0.0.1:5000)
python run.py

# Seed default roles (Admin, Editor, Viewer)
flask --app run init-roles
```

No test suite or linter is configured — tests are out of scope for this project.

**PostgreSQL is required even in development**: `create_app()` raises at startup unless `DATABASE_URL` is a `postgresql://` URI (only `FLASK_ENV=testing` uses SQLite in-memory). It also fails fast if the DB is unreachable (`SELECT 1` on boot) or if `SECRET_KEY`/`JWT_SECRET_KEY` are missing, weak, or under 16 chars. Copy `.env.example` to `.env` before first run.

## Architecture

Factory + Blueprints + Service/Repository pattern.

```
app/
├── __init__.py        # create_app() — validates DB + secrets on boot, registers blueprints
├── config.py          # Configs + pg8000 URL normalization (postgres:// → postgresql+pg8000://,
│                      #   strips sslmode/channel_binding, translates SSL to connect_args)
├── extensions.py      # db, cors, limiter (Flask-Limiter) singletons; login_manager in auth/
├── logging_setup.py   # init_logging() + register_request_logging(): per-request X-Request-ID,
│                      #   before/after logs (health endpoints at DEBUG), LOG_LEVEL env var
├── blueprints.py      # register_blueprints() — auth_bp, reports_bp, exports_bp
├── errors.py          # api_success()/api_error() envelope + global error handlers
├── models/__init__.py # Academic domain: Major, Student, Subject, Grade, AcademicPeriod,
│                      #   AcademicPeriodAudit, UserAudit
├── auth/              # auth_bp — login/refresh/logout (JWT), user CRUD, roles
│   ├── routes.py
│   ├── jwt_utils.py   # _build_token/_decode_token, @require_auth, @require_role
│   └── models/        # User, Role, RevokedToken (JWT revocation by jti)
├── reports/           # reports_bp — .REP ingestion, periods, students, careers, audit
│   ├── routes.py
│   ├── parser.py      # Regex parsing of .REP formats: ACTA, CON_CAL, ACUMULADOS
│   ├── services.py    # process_and_save_report(), get_students_by_period(), careers CRUD
│   └── repository.py  # SubjectRepository, MajorRepository, StudentRepository, etc.
├── exports/           # exports_bp — student academic record as PDF (xhtml2pdf)
└── utils.py           # Compatibility façade — re-exports from services.py
```

**Request flow**: Route → Service → Repository → Model (SQLAlchemy)

**Response envelope**: every endpoint returns `{"status", "message", "data"}` via `api_success()`/`api_error()` from `app/errors.py`. `api_error`'s `codigo`/`campo` params are kept for call-site compatibility but not emitted.

**Auth**: Bearer JWT (PyJWT, HS256). Access + refresh tokens with distinct `type` claims; logout revokes by `jti` into `revoked_tokens`. Routes are protected with `@require_auth` or `@require_role('Admin', 'Editor', ...)`; decoded payload lives in `g.current_user` (helpers: `current_user_id/email/fullname`). Single role per user via `user.set_role()`; the last Admin cannot be demoted or removed.

**Auditing**: user mutations write `UserAudit` rows (actor, IP, description of changes); report uploads/deletions write `academic_periods_audit`. Audit inserts share the transaction with the change they describe.

**Rate limiting**: `limiter` (Flask-Limiter, keyed by client IP) throttles a few public/sensitive endpoints via `@limiter.limit(...)` — login (5/min), refresh (10/min), exports PDF (10/min). Storage is `memory://`, so the limit is **per gunicorn worker**; use a Redis `storage_uri` for a shared global limit.

**Logging**: `logging_setup.py` attaches a stderr handler with a `[request_id]` field. Each request gets a `request_id` (from `X-Request-ID` header or an 8-char UUID), logged on entry/exit with method, status, duration and user, and echoed back in the `X-Request-ID` response header. Health checks (`/`, `/api/v1/status`) log at DEBUG; everything else at INFO.

**Pagination — two coexisting schemes** (don't confuse them):
- **Offset-based** (`_parse_pagination` in `reports/routes.py`): query params `init` (offset, ≥0) and `limit` (1–100). Used by `GET /academic-periods/<code>/students`.
- **Page-based** (`_parse_page_size` + `_pagination_meta`): query params `page` (≥1) and `size`, response `data` carries `{page, pages, per_page, total, has_prev, has_next}` alongside the list. Used by `GET /auth/user`, `GET /reports`, `GET /reports/audit/users`.

## Domain

Processes `.REP` academic report files from UNA (Universidad Nacional Abierta). Each file contains structured student/subject/grade data for one academic period.

Key parsing rules (`app/reports/parser.py`):
- Files must be read with `latin-1` encoding (not UTF-8)
- Valid files contain "UNIVERSIDAD NACIONAL ABIERTA" in the first 256 bytes
- Three layouts are auto-detected: ACTA, CON_CAL, ACUMULADOS (subject/period/cédula/grade extracted by regex)
- If any step fails mid-transaction, the entire report is rolled back and the uploaded file is deleted

## API Surface

Prefix configurable via `API_PREFIX`/`API_VERSION` (default `/api/v1`). Roles in parentheses.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` , `/api/v1/status` | Health checks (public) |
| POST | `/auth/login` | Returns access + refresh JWT (public) |
| POST | `/auth/refresh-token` | Rotate tokens (public) |
| POST | `/auth/logout` | Revoke current token (auth) |
| POST/GET | `/auth/user` | Create / list users (Admin) |
| GET/PUT | `/auth/user/<id>` | Get / update user (Admin or self) |
| DELETE | `/auth/user/<id>` | Delete user (Admin) |
| GET | `/auth/roles` | List roles (Admin) |
| POST / DELETE | `/auth/user/<id>/role[/<name>]` | Assign / remove role (Admin) |
| POST | `/reports` | Upload & process `.REP` (Admin, Editor) |
| GET | `/reports` | Report upload audit history (Admin, Editor) |
| GET | `/reports/audit/users` | User audit history (Admin) |
| GET | `/academic-periods` | List periods (any role) |
| DELETE | `/academic-periods/<code>` | Delete period + cascade grades/orphan students (Admin) |
| GET | `/academic-periods/<code>/students` | Paginated students (`init`, `limit`≤100, `order`, `asc`, `carrera`, `nombre`) |
| GET | `/students/<identification>` | Student by cédula, optional `?period=` (any role) |
| GET/POST | `/careers` | List / batch-create careers (POST: Admin, Editor; body is an array) |
| GET/PUT/DELETE | `/careers/<id>` | Career CRUD (DELETE: Admin; blocked if students exist) |
| GET | `/exports/pdf/<identification>/<period_id>` | Academic record PDF (public — no auth decorator) |

Bruno API collections live in `bruno/` at the repo root (environments Local/Prod).

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `FLASK_ENV` | `development` | Selects config class |
| `DATABASE_URL` | — (required) | PostgreSQL URI; `URL_DB`/`DB_ENGINE` accepted as legacy fallbacks |
| `SECRET_KEY` / `JWT_SECRET_KEY` | — (required) | Min 16 chars each, validated at startup |
| `JWT_EXP_HOURS` / `JWT_REFRESH_EXP_HOURS` | `1` / `2` | Token lifetimes |
| `API_PREFIX` / `API_VERSION` | `/api` / `v1` | Route prefix |
| `UPLOAD_FOLDER` | `data` | Directory for `.REP` files |
| `MAX_CONTENT_LENGTH` / `MAX_FILE_SIZE` | 16MB / 5MB | Request / per-file limits |
| `ALLOWED_EXTENSIONS` | `rep` | Accepted upload extensions |
| `PROXY_FORWARDED_HOPS` | `1` | Trusted proxy hops for reading client IP from `X-Forwarded-For` (ProxyFix); `0` disables |
| `CORS_ORIGINS` | — (all) | Comma-separated allowed origins; unset means open to all |
| `LOG_LEVEL` | `INFO` | Root log level (`logging_setup.py`) |
| `PORT` | `5000` | Dev server port (`run.py`) |

## Deployment

- `render.yaml` — Render web service (gunicorn, gthread workers), deploys from `main`.
- `.github/workflows/deploy.yml` — on push to `main`, SSHs into an OCI VM and runs `docker compose up -d --build`. Full runbook in `DEPLOY.md` (phase 2 = migration to Oracle Autonomous DB, not yet applied).
- `Dockerfile` (python:3.12-slim, gunicorn gthread, non-root user) and `docker-compose.yml` (app + postgres:16) back the OCI deploy.
- The Postgres pool is tuned for Neon (pre-ping, recycle 300s); pg8000-specific quirks are handled in `config.py` and `app/__init__.py` (`use_insertmanyvalues_wo_returning`).

## Key Conventions

- New extensions go in `extensions.py`, imported in `create_app()` via `init_app()`.
- New blueprints are registered in `blueprints.py` only — `create_app()` calls `register_blueprints()`.
- `utils.py` is a compatibility shim — do not add new logic there; put it in `services.py`.
- `flask --app run` is required for CLI commands because the entry point is `run.py`, not `app.py`.
- User-facing messages (API `message` fields, log text) are in Spanish; code identifiers in English.
- CORS is open to all origins by default — restrict in production.
- Database schema is managed by the DBA directly with SQL. `doc/DATABASE.md` is the authoritative DDL (`doc/schema_create.sql` / `doc/schema_rollback.sql` are the scripts). Do not use `flask db migrate` — Flask-Migrate has been removed from the project.
