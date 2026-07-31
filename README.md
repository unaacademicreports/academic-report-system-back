# Newsletter Generator UNA — Backend (Flask API)

API REST en Python/Flask que procesa reportes académicos `.REP` de la **Universidad Nacional Abierta
(UNA)**. Ingesta los archivos planos, los almacena de forma relacional en PostgreSQL y expone la
información académica de los estudiantes agrupada por período, con autenticación por JWT, control de
acceso por roles, auditoría de cambios y exportación de la ficha académica en PDF.

## Tabla de contenido

- [Stack y arquitectura](#stack-y-arquitectura)
- [Puesta en marcha (local)](#puesta-en-marcha-local)
- [Variables de entorno](#variables-de-entorno)
- [Superficie de la API](#superficie-de-la-api)
- [Documentación](#documentación)

## Stack y arquitectura

- **Python 3.12 + Flask** con patrón *Application Factory* + *Blueprints* + *Service/Repository*.
- **PostgreSQL** (driver `pg8000`); requerido incluso en desarrollo. La app valida en el arranque la
  conexión a la BD y la fortaleza de los secretos, y falla rápido si algo falta.
- **Autenticación JWT** (PyJWT, HS256): tokens de acceso y de refresco; `logout` revoca por `jti`.
- **Roles**: `Admin`, `Editor`, `Viewer` (un único rol por usuario).
- **Auditoría** transaccional de mutaciones de usuarios y de cargas/eliminaciones de períodos.
- **Rate limiting** por IP (Flask-Limiter) en login, refresh y exportación de PDF.
- **Exportación PDF** de la ficha académica (xhtml2pdf).

Flujo de una petición: **Ruta → Servicio → Repositorio → Modelo (SQLAlchemy)**. Toda respuesta usa la
envoltura `{ "status", "message", "data" }`. El detalle de arquitectura y convenciones está en
[`CLAUDE.md`](CLAUDE.md); el manual para mantener y extender el proyecto en
[`doc/MANTENIMIENTO.md`](doc/MANTENIMIENTO.md).

```
app/
├── __init__.py     # create_app(): valida BD + secretos, registra extensiones y blueprints
├── config.py       # Configs y normalización de URL para pg8000
├── extensions.py   # db, cors, limiter
├── blueprints.py   # registro de auth_bp, reports_bp, exports_bp
├── errors.py       # envoltura api_success/api_error + manejadores globales
├── logging_setup.py# logging con X-Request-ID por petición
├── models/         # dominio académico
├── auth/           # login/refresh/logout, CRUD de usuarios, roles (JWT)
├── reports/        # ingesta .REP, períodos, estudiantes, carreras, auditoría
└── exports/        # ficha académica en PDF
```

## Puesta en marcha (local)

```powershell
# 1. Entorno virtual
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # Windows PowerShell
# source .venv/bin/activate         # Linux/macOS

# 2. Dependencias
pip install -r requirements.txt

# 3. Configuración: copiar la plantilla y ajustar credenciales/secretos
copy .env.example .env              # cp .env.example .env  en Linux/macOS

# 4. Base de datos: aplicar el esquema autoritativo sobre una BD PostgreSQL vacía
#    psql "$DATABASE_URL" -f doc/schema_create.sql

# 5. Servidor de desarrollo (http://127.0.0.1:5000)
python run.py

# 6. (opcional) Sembrar los roles por defecto Admin, Editor, Viewer
flask --app run init-roles
```

`SECRET_KEY` y `JWT_SECRET_KEY` son obligatorios (mínimo 16 caracteres) y `DATABASE_URL` debe ser una URI
`postgresql://`; de lo contrario el arranque aborta. No hay suite de tests ni linter configurados.

## Variables de entorno

| Variable | Default | Propósito |
|----------|---------|-----------|
| `FLASK_ENV` | `development` | Selecciona la clase de configuración |
| `DATABASE_URL` | — (requerido) | URI PostgreSQL (`URL_DB`/`DB_ENGINE` como fallback heredado) |
| `SECRET_KEY` / `JWT_SECRET_KEY` | — (requerido) | Mínimo 16 caracteres cada uno; validados al arranque |
| `JWT_EXP_HOURS` / `JWT_REFRESH_EXP_HOURS` | `1` / `2` | Duración de los tokens |
| `API_PREFIX` / `API_VERSION` | `/api` / `v1` | Prefijo de rutas |
| `UPLOAD_FOLDER` | `data` | Directorio para archivos `.REP` |
| `MAX_CONTENT_LENGTH` / `MAX_FILE_SIZE` | 16MB / 5MB | Límites de request / por archivo |
| `ALLOWED_EXTENSIONS` | `rep` | Extensiones de carga aceptadas |
| `PROXY_FORWARDED_HOPS` | `1` | Saltos de proxy confiables para leer la IP del cliente (`0` desactiva) |
| `CORS_ORIGINS` | — (todos) | Orígenes permitidos (separados por coma); sin definir = abierto |
| `LOG_LEVEL` | `INFO` | Nivel de log raíz |
| `PORT` | `5000` | Puerto del servidor de desarrollo |

## Superficie de la API

Prefijo por defecto `/api/v1`. El rol requerido va entre paréntesis. Contrato completo (body, respuestas,
status y casos de error) en [`doc/API.md`](doc/API.md).

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/` , `/api/v1/status` | Health checks (público) |
| POST | `/auth/login` | Devuelve JWT de acceso + refresco (público) |
| POST | `/auth/refresh-token` | Rota los tokens (público) |
| POST | `/auth/logout` | Revoca el token actual (autenticado) |
| POST/GET | `/auth/user` | Crear / listar usuarios (Admin) |
| GET/PUT | `/auth/user/<id>` | Obtener / actualizar usuario (Admin o el propio) |
| DELETE | `/auth/user/<id>` | Eliminar usuario (Admin) |
| GET | `/auth/roles` | Listar roles (Admin) |
| POST / DELETE | `/auth/user/<id>/role[/<name>]` | Asignar / quitar rol (Admin) |
| POST | `/reports` | Cargar y procesar `.REP` (Admin, Editor) |
| GET | `/reports` | Auditoría de cargas de períodos (Admin, Editor) |
| GET | `/reports/audit/users` | Auditoría de usuarios (Admin) |
| GET | `/academic-periods` | Listar períodos (cualquier rol) |
| DELETE | `/academic-periods/<code>` | Eliminar período + cascada (Admin) |
| GET | `/academic-periods/<code>/students` | Estudiantes paginados del período |
| GET | `/students/<identification>` | Estudiante por cédula, opcional `?period=` |
| GET/POST | `/careers` | Listar / crear carreras en lote (POST: Admin, Editor) |
| GET/PUT/DELETE | `/careers/<id>` | CRUD de carrera (DELETE: Admin) |
| GET/POST | `/exports/pdf/<identification>/<period_id>` | Ficha académica en PDF (público) |

## Documentación

| Documento | Contenido |
|-----------|-----------|
| [`doc/API.md`](doc/API.md) | Contrato completo de endpoints: body, respuestas, status y errores |
| [`doc/DATABASE.md`](doc/DATABASE.md) | Esquema de BD narrado y diagrama de relaciones |
| [`doc/especificacion-bd.xlsx`](doc/especificacion-bd.xlsx) | Especificación de BD campo por campo (Excel) |
| [`doc/MANTENIMIENTO.md`](doc/MANTENIMIENTO.md) | Manual: cómo corregir, depurar y añadir un endpoint |
| [`doc/schema_create.sql`](doc/schema_create.sql) | DDL autoritativo del esquema |
| [`CLAUDE.md`](CLAUDE.md) | Guía técnica de arquitectura y convenciones |
| [`DEPLOY.md`](DEPLOY.md) | Runbook de despliegue (OCI / Docker) |
| `bruno/` | Colecciones Bruno (entornos Local / Prod) para probar la API |
