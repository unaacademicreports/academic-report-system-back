import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app, request
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import Subject, Major, Student, Grade, AcademicPeriod
from app.reports.parser import parse_report
from app.reports.repository import (
    SubjectRepository,
    MajorRepository,
    StudentRepository,
    SedeRepository,
    AcademicPeriodRepository,
    AcademicPeriodAuditRepository,
    GradeRepository,
)

logger = logging.getLogger(__name__)


def get_path_data() -> Path:
    directory = Path(current_app.root_path) / '../data'
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def get_reports_list() -> list[str]:
    try:
        directory = get_path_data()
        return [f.name for f in directory.iterdir() if f.is_file()]
    except OSError:
        logger.exception("Error listing report files")
        return []


def add_report_to_list(archive) -> bool:
    if archive:
        secure_name = secure_filename(archive.filename)
        save_directory = get_path_data() / secure_name
        archive.save(str(save_directory))
        return True
    return False


def delete_report(filename: str) -> bool:
    file_directory = get_path_data() / secure_filename(filename)
    if file_directory.exists():
        os.remove(file_directory)
        return True
    return False


def process_and_save_report(
    file_path: Path,
    encoding: str = 'latin-1',
    uploaded_by_id: int | None = None,
    uploaded_by_email: str | None = None,
    uploaded_by_fullname: str | None = None,
    source_file: str | None = None,
) -> tuple[bool, str | None, list[str], int]:
    """
    file_path: ruta absoluta al archivo ya guardado (la resuelve el route).
    Returns (True, None, missing_codes, discarded_lines) on success,
    (False, error_code, [], 0) on failure.
    error_code values: "REPORTE_VACIO" | "ERROR_LECTURA" | "ERROR_DB"
    missing_codes: career codes present in the file whose Major has no name yet.
    """
    file_name = file_path.name
    logger.info("Iniciando procesamiento del reporte '%s' (subido por %s)", file_name, uploaded_by_email)
    try:
        parsed_grades, discarded_lines = parse_report(file_path, encoding=encoding)
    except (OSError, UnicodeDecodeError) as e:
        logger.error("Error reading report file '%s': %s", file_name, e)
        return False, "ERROR_LECTURA", [], 0

    if not parsed_grades:
        logger.warning("Report '%s' produced 0 records — no valid data detected.", file_name)
        return False, "REPORTE_VACIO", [], 0

    try:
        first = parsed_grades[0]

        # 1. Sede — una sola por archivo
        sede_id = None
        if first.universidad:
            sede = SedeRepository.find_or_create(first.universidad, first.centro_local, first.oficina)
            sede_id = sede.id

        # 2. Período — uno solo por archivo
        period = None
        period_code = first.period_code
        if period_code:
            period = AcademicPeriodRepository.find_by_code(period_code, sede_id=sede_id)
            is_new_period = period is None
            if is_new_period:
                period = AcademicPeriodRepository.create(
                    period_code,
                    sede_id=sede_id,
                    uploaded_by_id=uploaded_by_id,
                    uploaded_by_email=uploaded_by_email,
                    uploaded_by_fullname=uploaded_by_fullname,
                    uploaded_at=datetime.now(timezone.utc),
                    source_file=source_file,
                )
            # Auditoría siempre: CREACIÓN si es nuevo, ACTUALIZACIÓN si ya existía
            AcademicPeriodAuditRepository.create(
                period_code=period_code,
                operation='CREACIÓN' if is_new_period else 'ACTUALIZACIÓN',
                sede_id=sede_id,
                user_email=uploaded_by_email,
                user_fullname=uploaded_by_fullname,
                operation_at=datetime.now(timezone.utc),
                source_file=source_file,
                ip_address=request.remote_addr,
            )

        period_id = period.id if period else None

        # === Fase 0: precarga en memoria (1 SELECT por tabla) ===
        subjects_map = SubjectRepository.get_all_map()
        majors_map   = MajorRepository.get_all_map()
        students_map = StudentRepository.get_map_for(r.cedula for r in parsed_grades)
        grades_map   = GradeRepository.get_existing_map_for_period(period_id)

        # Carreras del archivo que aún no tienen nombre registrado.
        missing_codes = set()

        # === Fase 1: Subjects/Majors nuevos (add_all + un flush) ===
        new_subjects = {}
        new_majors = {}
        for record in parsed_grades:
            if record.subject_code not in subjects_map and record.subject_code not in new_subjects:
                new_subjects[record.subject_code] = Subject(
                    code=record.subject_code, name=record.subject_name
                )
            if record.carrera_codigo not in majors_map and record.carrera_codigo not in new_majors:
                new_majors[record.carrera_codigo] = Major(code=record.carrera_codigo)

        if new_subjects or new_majors:
            db.session.add_all([*new_subjects.values(), *new_majors.values()])
            db.session.flush()
        subjects_map.update(new_subjects)
        majors_map.update(new_majors)

        for record in parsed_grades:
            major = majors_map[record.carrera_codigo]
            if not major.name:
                missing_codes.add(record.carrera_codigo)

        # === Fase 2: Students nuevos (add_all + un flush) ===
        new_students = {}
        for record in parsed_grades:
            if record.cedula in students_map or record.cedula in new_students:
                continue
            major = majors_map[record.carrera_codigo]
            new_students[record.cedula] = Student(
                identification=record.cedula,
                full_name=record.full_name,
                major_id=major.id,
            )

        if new_students:
            db.session.add_all(new_students.values())
            db.session.flush()
        students_map.update(new_students)

        # === Fase 3: Grades upsert (UPDATE en memoria + bulk insert) ===
        new_grades_by_key = {}  # "última gana" para claves repetidas en el archivo
        for record in parsed_grades:
            student = students_map[record.cedula]
            subject = subjects_map[record.subject_code]
            key = (student.id, subject.id, period_id)

            existing = grades_map.get(key)
            if existing is not None:
                # Update en memoria; el commit final persiste todo en un solo batch
                # (evita un flush por fila como haría GradeRepository.update).
                existing.condition           = record.condicion
                existing.absent              = record.absent
                existing.objectives_achieved = record.objectives_achieved
                existing.objectives_total    = record.objectives_total
                existing.calificacion        = record.calificacion
            else:
                new_grades_by_key[key] = {
                    "condition": record.condicion,
                    "student_id": student.id,
                    "subject_id": subject.id,
                    "academic_period_id": period_id,
                    "absent": record.absent,
                    "objectives_achieved": record.objectives_achieved,
                    "objectives_total": record.objectives_total,
                    "calificacion": record.calificacion,
                }

        GradeRepository.bulk_create(list(new_grades_by_key.values()))

        db.session.commit()
        logger.info(
            "Reporte '%s' procesado: período=%s, %d registros, %d estudiantes nuevos, "
            "%d notas nuevas, %d asignaturas nuevas, %d carreras sin nombre",
            file_name, period_code, len(parsed_grades), len(new_students),
            len(new_grades_by_key), len(new_subjects), len(missing_codes),
        )
        return True, None, sorted(missing_codes), discarded_lines

    except Exception as e:
        # No solo SQLAlchemyError: un KeyError/ValueError a mitad de la
        # transacción dejaría la sesión sucia para el siguiente request.
        db.session.rollback()
        logger.exception("Error processing report '%s': %s", file_name, e)
        return False, "ERROR_DB", [], 0


def _major_display(major) -> str:
    return major.name if major.name else major.code


def _format_student_data(student: Student, grades=None) -> dict:
    resultado = {
        "cedula": student.identification,
        "nombre": student.full_name,
        "carrera": _major_display(student.major),
        "periodos": [],
    }

    grade_list = grades if grades is not None else student.grades
    periodos_dict = {}
    for grade in grade_list:
        period_code = grade.academic_period.code if grade.academic_period else "Desconocido"
        period_id   = grade.academic_period.id   if grade.academic_period else None

        if period_code not in periodos_dict:
            periodos_dict[period_code] = {"id": period_id, "materias": []}

        if grade.absent or not grade.objectives_total or not grade.objectives_achieved:
            nota_final = "No Presento"
        else:
            nota_final = f"{grade.objectives_achieved}/{grade.objectives_total}"

        periodos_dict[period_code]["materias"].append({
            "codigo_asignatura": grade.subject.code,
            "asignatura":        grade.subject.name,
            "condicion":         grade.condition,
            "ausente":           grade.absent,
            "nota_final":        nota_final,
        })

    for period_code, val in periodos_dict.items():
        resultado["periodos"].append({
            "id":       val["id"],
            "codigo":   period_code,
            "materias": val["materias"],
        })

    return resultado


def get_student_data_from_db(target_cedula: str, mode: str = "exact", period_filter: str = None):
    if mode == "prefix":
        students = StudentRepository.find_by_prefix(target_cedula)
        if not students:
            return None
        if period_filter:
            return [
                _format_student_data(
                    s,
                    grades=GradeRepository.find_by_student_and_period(s.id, period_filter),
                )
                for s in students
            ]
        return [_format_student_data(s) for s in students]
    else:
        student = StudentRepository.find_by_identification(target_cedula)
        if not student:
            return None
        if period_filter:
            grades = GradeRepository.find_by_student_and_period(student.id, period_filter)
            return _format_student_data(student, grades=grades)
        return _format_student_data(student)


def get_students_by_period(
    period_code: str,
    init: int,
    limit: int,
    order: str,
    ascending: bool,
    carrera: str | None,
    nombre: str | None,
) -> dict | None:
    if not AcademicPeriodRepository.exists_by_code(period_code):
        return None

    students, total = StudentRepository.find_by_period_paginated(
        period_code, init, limit, order, ascending, carrera, nombre
    )
    return {
        "students": [
            {
                "cedula": s.identification,
                "nombre": s.full_name,
                "carrera": _major_display(s.major),
            }
            for s in students
        ],
        "total": total,
        "init": init,
        "limit": limit,
    }


def get_all_academic_periods() -> list[dict]:
    periods = AcademicPeriodRepository.get_all()
    return [p.to_dict() for p in periods]


def get_all_audit_records(init: int = 0, limit: int = 20) -> tuple[list[dict], int]:
    records, total = AcademicPeriodAuditRepository.get_paginated(init, limit)
    return [{k: v for k, v in r.to_dict().items() if k != 'id'} for r in records], total


###
#
# CAREERS (Majors) CRUD
#
###
def get_all_careers() -> list[dict]:
    return [m.to_dict() for m in MajorRepository.get_all()]


def get_career(major_id: int) -> dict | None:
    major = MajorRepository.find_by_id(major_id)
    return major.to_dict() if major else None


def create_careers(items: list[dict]) -> tuple[list[dict], list[str], str | None]:
    """Upsert de carreras por lotes en una sola transacción.

    Devuelve (procesadas, fallidas, db_error):
    - code vacío/ausente -> se omite y se agrega a 'fallidas' como "(vacío)".
    - code existente -> actualiza su name (upsert).
    - code nuevo -> crea.
    db_error es "ERROR_DB" si la transacción falla (con rollback), si no None.
    """
    processed: list[dict] = []
    failed: list[str] = []
    try:
        majors_map = MajorRepository.get_all_map()
        for item in items:
            if not isinstance(item, dict):
                failed.append("(vacío)")
                continue
            code = (item.get('code') or '').strip()
            name = (item.get('name') or '').strip() or None
            if not code:
                failed.append("(vacío)")
                continue

            major = majors_map.get(code)
            if major:
                MajorRepository.update(major, name=name)
            else:
                major = MajorRepository.create(code, name)
                majors_map[code] = major
            processed.append(major.to_dict())

        db.session.commit()
        return processed, failed, None
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error("Database error creating careers in batch: %s", e)
        return [], [], "ERROR_DB"


def update_career(major_id: int, code: str | None = None, name: str | None = None) -> tuple[dict | None, str | None]:
    """error_code: "NO_ENCONTRADO" | "CODIGO_DUPLICADO" | "ERROR_DB"."""
    major = MajorRepository.find_by_id(major_id)
    if not major:
        return None, "NO_ENCONTRADO"
    if code is not None and code != major.code:
        existing = MajorRepository.find_by_code(code)
        if existing and existing.id != major_id:
            return None, "CODIGO_DUPLICADO"
    try:
        MajorRepository.update(major, code=code, name=name)
        db.session.commit()
        return major.to_dict(), None
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error("Database error updating career %d: %s", major_id, e)
        return None, "ERROR_DB"


def delete_career(major_id: int) -> str | None:
    """Returns None on success, error_code on failure.
    error_code: "NO_ENCONTRADO" | "TIENE_ESTUDIANTES" | "ERROR_DB"."""
    major = MajorRepository.find_by_id(major_id)
    if not major:
        return "NO_ENCONTRADO"
    if Student.query.filter_by(major_id=major_id).first():
        return "TIENE_ESTUDIANTES"
    try:
        MajorRepository.delete(major)
        db.session.commit()
        return None
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error("Database error deleting career %d: %s", major_id, e)
        return "ERROR_DB"


def delete_academic_period(
    period_code: str,
    deleted_by_email: str | None = None,
    deleted_by_fullname: str | None = None,
) -> dict | None:
    result = AcademicPeriodRepository.delete_period_cascade(period_code)
    if result is None:
        return None
    AcademicPeriodAuditRepository.create(
        period_code=period_code,
        operation='ELIMINACIÓN',
        sede_id=result['sede_id'],
        user_email=deleted_by_email,
        user_fullname=deleted_by_fullname,
        source_file=result['source_file'],
        ip_address=request.remote_addr,
        affected_rows={
            'grades_deleted': result['grades_deleted'],
            'students_deleted': result['students_deleted'],
        },
    )
    try:
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error("Database error deleting period '%s': %s", period_code, e)
        raise
    logger.info(
        "Período '%s' eliminado por %s: %d notas y %d estudiantes borrados",
        period_code, deleted_by_email,
        result['grades_deleted'], result['students_deleted'],
    )
    return result
