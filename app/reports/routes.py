import logging
import os
from pathlib import Path
from uuid import uuid4

from flask import request, current_app
from werkzeug.utils import secure_filename

from app.errors import api_error, api_success
from app.extensions import limiter
from app.auth.jwt_utils import require_auth, require_role, current_user_id, current_user_email, current_user_fullname
from app.models import UserAudit
from app.reports import reports_bp as report
from app.reports.services import (
    get_student_data_from_db,
    get_students_by_period,
    process_and_save_report,
    get_all_academic_periods,
    get_all_audit_records,
    delete_academic_period,
    get_all_careers,
    get_career,
    create_careers,
    update_career,
    delete_career,
)

logger = logging.getLogger(__name__)


def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']


def _parse_pagination():
    """Lee init/limit de la query string. Returns (init, limit, error_response)."""
    try:
        init = int(request.args.get('init', '0'))
        if init < 0:
            raise ValueError
    except ValueError:
        return None, None, api_error("PARAM_INVALIDO", "'init' debe ser un entero >= 0.", campo="init")

    try:
        limit = int(request.args.get('limit', '20'))
        if not (1 <= limit <= 100):
            raise ValueError
    except ValueError:
        return None, None, api_error("PARAM_INVALIDO", "'limit' debe ser un entero entre 1 y 100.", campo="limit")

    return init, limit, None


def _parse_page_size():
    """Lee page/size de la query string. Returns (page, size, error_response)."""
    try:
        page = int(request.args.get('page', '1'))
        if page < 1:
            raise ValueError
    except ValueError:
        return None, None, api_error("PARAM_INVALIDO", "'page' debe ser un entero >= 1.", campo="page")

    try:
        size = int(request.args.get('size', '30'))
        if size < 1:
            raise ValueError
    except ValueError:
        return None, None, api_error("PARAM_INVALIDO", "'size' debe ser un entero >= 1.", campo="size")

    return page, size, None


def _pagination_meta(page: int, size: int, total: int, count: int) -> dict:
    """Metadatos de paginación. 'per_page' es la cantidad de registros de esta página."""
    pages = (total + size - 1) // size if total else 0
    return {
        "page": page,
        "pages": pages,
        "per_page": count,
        "total": total,
        "has_prev": page > 1,
        "has_next": page < pages,
    }


###
#
# ✅ ADD NEW REPORT
#
###
@report.route("/reports", methods=['POST'])
@require_role('Admin', 'Editor')
def report_add():
    if 'file' not in request.files:
        return api_error("ARCHIVO_NO_ENVIADO", "No se envió ningún archivo.", campo="file")

    file = request.files['file']

    if file.filename == '':
        return api_error("ARCHIVO_NO_SELECCIONADO", "Archivo no seleccionado.", campo="file")

    if not allowed_file(file.filename):
        return api_error("FORMATO_INVALIDO", "Solo se permiten archivos .rep", campo="file")

    file.seek(0, os.SEEK_END)
    file_length = file.tell()
    file.seek(0, 0)

    if file_length > current_app.config['MAX_FILE_SIZE']:
        return api_error("ARCHIVO_MUY_GRANDE", "El archivo excede el tamaño máximo permitido.", campo="file")

    try:
        cabecera = file.read(256).decode('latin-1')
        file.seek(0, 0)
        if "UNIVERSIDAD NACIONAL ABIERTA" not in cabecera:
            return api_error("ARCHIVO_INVALIDO", "El archivo no es un reporte UNA válido.", campo="file")
    except UnicodeDecodeError:
        return api_error("ARCHIVO_CORRUPTO", "El archivo parece estar dañado o no es texto plano.", campo="file")

    filename = secure_filename(file.filename)
    upload_dir = Path(current_app.config['UPLOAD_FOLDER']).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)
    # Prefijo único: evita colisiones/sobrescritura entre cargas concurrentes
    # del mismo nombre de archivo.
    stored_name = f"{uuid4().hex}_{filename}"
    save_path = (upload_dir / stored_name).resolve()

    if save_path.parent != upload_dir:
        return api_error("ARCHIVO_INVALIDO", "Nombre de archivo inválido.", campo="file")

    try:
        file.save(str(save_path))
    except OSError:
        logger.exception("Error saving uploaded file: %s", filename)
        return api_error("ERROR_GUARDADO", "No se pudo guardar el archivo en disco.", http_status=500)

    ok, error_code, missing_codes, discarded_lines = process_and_save_report(
        save_path,
        uploaded_by_id=current_user_id(),
        uploaded_by_email=current_user_email(),
        uploaded_by_fullname=current_user_fullname(),
        source_file=filename,
    )

    # El archivo ya no se necesita tras procesarlo (éxito o fallo): sus datos
    # quedan en BD y retenerlo acumula PII en disco.
    try:
        os.remove(save_path)
    except OSError:
        logger.warning("Could not remove uploaded file after processing: %s", save_path)

    if not ok:
        if error_code == "REPORTE_VACIO":
            return api_error(
                "REPORTE_VACIO",
                "El archivo no contiene registros válidos de estudiantes.",
                campo="file",
                http_status=422,
            )
        if error_code == "ERROR_LECTURA":
            return api_error(
                "ARCHIVO_CORRUPTO",
                "No se pudo leer el archivo. Verifique la codificación.",
                campo="file",
                http_status=422,
            )
        return api_error("ERROR_PROCESAMIENTO", "Error al procesar el reporte en la base de datos.", http_status=500)

    return api_success(
        data={
            "filename": filename,
            "carreras_sin_nombre": missing_codes,
            "lineas_descartadas": discarded_lines,
        },
        mensaje="Archivo cargado, validado y guardado correctamente.",
        http_status=201,
    )


###
#
# ✅ LIST REPORTS
#
###
@report.route("/reports", methods=['GET'])
@require_role('Admin', 'Editor')
def reports_get():
    page, size, err = _parse_page_size()
    if err:
        return err
    init = (page - 1) * size
    records, total = get_all_audit_records(init, size)
    return api_success(
        data={"audit_records": records, **_pagination_meta(page, size, total, len(records))},
        mensaje="Historial de auditoría.",
    )


###
#
# ✅ ALL ACADEMIC PERIODS LIST
#
###
@report.route("/academic-periods", methods=['GET'])
@require_role('Admin', 'Editor', 'Viewer')
def academic_periods_get():
    try:
        periods = get_all_academic_periods()
        data = [{"id": p["id"], "code": p["code"], "source_file": p["source_file"]} for p in periods]
        return api_success(data={"academic_periods": data}, mensaje="Períodos académicos.")
    except Exception:
        logger.exception("Error fetching academic periods")
        return api_error("ERROR_INTERNO", "Error al obtener los períodos académicos.", http_status=500)


###
#
# ✅ STUDENTS BY ACADEMIC PERIOD (paginated, filtered, ordered)
#
###
@report.route("/academic-periods/<string:period_code>/students", methods=['GET'])
@require_role('Admin', 'Editor', 'Viewer')
def students_by_period(period_code):
    raw_order = request.args.get('order', 'nombre')
    raw_asc   = request.args.get('asc',   'true')
    carrera   = request.args.get('carrera') or None
    nombre    = request.args.get('nombre')  or None

    init, limit, err = _parse_pagination()
    if err:
        return err

    valid_orders = {'cedula', 'nombre', 'carrera'}
    if raw_order not in valid_orders:
        return api_error(
            "PARAM_INVALIDO",
            f"'order' debe ser uno de: {', '.join(sorted(valid_orders))}.",
            campo="order",
        )

    ascending = raw_asc.lower() != 'false'
    result = get_students_by_period(period_code, init, limit, raw_order, ascending, carrera, nombre)

    if result is None:
        return api_error("PERIODO_NO_ENCONTRADO", f"Período académico '{period_code}' no encontrado.", http_status=404)

    return api_success(data=result, mensaje="Listado de estudiantes.")


###
#
# ✅ DELETE ACADEMIC PERIOD (cascade: grades + orphan students)
#
###
@report.route("/academic-periods/<string:period_code>", methods=['DELETE'])
@require_role('Admin')
def academic_period_delete(period_code):
    try:
        result = delete_academic_period(
            period_code,
            deleted_by_email=current_user_email(),
            deleted_by_fullname=current_user_fullname(),
        )
    except Exception:
        logger.exception("Error deleting academic period: %s", period_code)
        return api_error("ERROR_INTERNO", "Error al eliminar el período académico.", http_status=500)

    if result is None:
        return api_error(
            "PERIODO_NO_ENCONTRADO",
            f"Período académico '{period_code}' no encontrado.",
            http_status=404,
        )

    return api_success(
        data={
            "period_code": period_code,
            "grades_deleted": result["grades_deleted"],
            "students_deleted": result["students_deleted"],
        },
        mensaje=f"Período '{period_code}' eliminado correctamente.",
    )


###
#
# ✅ STUDENT SEARCH BY IDENTIFICATION
#
###
# Endpoint público por decisión de producto: el rate limit mitiga la
# enumeración de cédulas y el scraping de PII (mismo patrón que /exports/pdf).
@report.route("/students/<string:identification>", methods=['GET'])
@limiter.limit("30 per minute")
def student_search(identification):
    period_filter = request.args.get('period')
    result = get_student_data_from_db(identification, period_filter=period_filter)

    if result:
        return api_success(data=result, mensaje="Estudiante encontrado.")

    return api_error("ESTUDIANTE_NO_ENCONTRADO", "Estudiante no encontrado.", http_status=404)


###
#
# ✅ CAREERS (Majors) CRUD
#
###
@report.route("/careers", methods=['GET'])
@require_role('Admin', 'Editor', 'Viewer')
def careers_get():
    return api_success(data={"careers": get_all_careers()}, mensaje="Carreras.")


@report.route("/careers/<int:major_id>", methods=['GET'])
@require_role('Admin', 'Editor', 'Viewer')
def career_get(major_id):
    career = get_career(major_id)
    if not career:
        return api_error("CARRERA_NO_ENCONTRADA", f"Carrera {major_id} no encontrada.", http_status=404)
    return api_success(data=career, mensaje="Carrera encontrada.")


@report.route("/careers", methods=['POST'])
@require_role('Admin', 'Editor')
def career_create():
    data = request.get_json(silent=True)
    if not isinstance(data, list):
        return api_error("FORMATO_INVALIDO", "El cuerpo debe ser un arreglo de carreras.")
    if not data:
        return api_error("CUERPO_REQUERIDO", "El arreglo no puede estar vacío.")

    processed, failed, db_error = create_careers(data)
    if db_error:
        return api_error("ERROR_BASE_DATOS", "No se pudieron guardar las carreras.", http_status=500)

    if failed:
        return api_success(
            data={"procesadas": processed, "fallidas": failed},
            mensaje=f"Códigos no guardados: {', '.join(failed)}.",
            http_status=207,
            status="error",
        )

    return api_success(
        data={"procesadas": processed},
        mensaje="Carreras guardadas correctamente.",
        http_status=201,
    )


@report.route("/careers/<int:major_id>", methods=['PUT'])
@require_role('Admin', 'Editor')
def career_update(major_id):
    data = request.get_json()
    if not data:
        return api_error("CUERPO_REQUERIDO", "El cuerpo de la solicitud es requerido.")

    code = data.get('code')
    if code is not None:
        code = code.strip()
        if not code:
            return api_error("CAMPO_REQUERIDO", "El campo 'code' no puede estar vacío.", campo="code")

    name = data.get('name')
    if name is not None:
        name = name.strip() or None

    career, error_code = update_career(major_id, code=code, name=name)
    if error_code == "NO_ENCONTRADO":
        return api_error("CARRERA_NO_ENCONTRADA", f"Carrera {major_id} no encontrada.", http_status=404)
    if error_code == "CODIGO_DUPLICADO":
        return api_error("CODIGO_DUPLICADO", "Ya existe una carrera con ese código.", campo="code", http_status=409)
    if error_code:
        return api_error("ERROR_BASE_DATOS", "No se pudo actualizar la carrera.", http_status=500)

    return api_success(data=career, mensaje="Carrera actualizada correctamente.")


@report.route("/careers/<int:major_id>", methods=['DELETE'])
@require_role('Admin')
def career_delete(major_id):
    error_code = delete_career(major_id)
    if error_code == "NO_ENCONTRADO":
        return api_error("CARRERA_NO_ENCONTRADA", f"Carrera {major_id} no encontrada.", http_status=404)
    if error_code == "TIENE_ESTUDIANTES":
        return api_error(
            "CARRERA_CON_ESTUDIANTES",
            "No se puede eliminar: la carrera tiene estudiantes asociados.",
            http_status=409,
        )
    if error_code:
        return api_error("ERROR_BASE_DATOS", "No se pudo eliminar la carrera.", http_status=500)

    return api_success(data={"id": major_id}, mensaje="Carrera eliminada correctamente.")


###
#
# ✅ USER AUDIT — Admin
#
###
@report.route("/reports/audit/users", methods=['GET'])
@require_role('Admin')
def user_audit_list():
    page, size, err = _parse_page_size()
    if err:
        return err
    init = (page - 1) * size
    query = UserAudit.query.order_by(UserAudit.operation_at.desc())
    total = query.count()
    records = query.offset(init).limit(size).all()
    data = [{
        "ip_address": r.ip_address,
        "operation": r.operation,
        "operation_at": r.operation_at.isoformat() if r.operation_at else None,
        "user_email": r.actor_email,
        "user_modify": r.user_email,
        "descripcion": r.affected_data or [],
    } for r in records]
    return api_success(
        data={"audit_records": data, **_pagination_meta(page, size, total, len(data))},
        mensaje="Historial de auditoría de usuarios.",
    )
