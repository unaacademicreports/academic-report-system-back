"""
Repository Layer for Reports
Encapsulates all database access logic for academic models.
Follows Single Responsibility Principle: each repository handles one model.

NOTE: create/update methods use flush() instead of commit() so the calling
service controls the transaction boundary with a single commit.
"""

from datetime import datetime, timezone

from sqlalchemy import asc as asc_fn, desc as desc_fn, func, insert
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import Subject, Major, Student, Grade, AcademicPeriod, AcademicPeriodAudit, Sede


def _escape_like(value: str) -> str:
    """Escape LIKE special characters to prevent wildcard injection."""
    return value.replace('\\', '\\\\').replace('%', r'\%').replace('_', r'\_')


class SubjectRepository:
    """Repository for Subject (Asignatura) operations."""

    @staticmethod
    def find_by_code(code: str) -> Subject:
        return Subject.query.filter_by(code=code).first()

    @staticmethod
    def get_all_map() -> dict:
        """{code: Subject} para todos los subjects (1 SELECT)."""
        return {s.code: s for s in Subject.query.all()}

    @staticmethod
    def create(code: str, name: str) -> Subject:
        subject = Subject(code=code, name=name)
        db.session.add(subject)
        db.session.flush()
        return subject


class MajorRepository:
    """Repository for Major (Carrera) operations."""

    @staticmethod
    def find_by_code(code: str) -> Major:
        return Major.query.filter_by(code=code).first()

    @staticmethod
    def find_by_id(major_id: int) -> Major:
        return db.session.get(Major, major_id)

    @staticmethod
    def get_all_map() -> dict:
        """{code: Major} para todos los majors (1 SELECT)."""
        return {m.code: m for m in Major.query.all()}

    @staticmethod
    def get_all() -> list:
        return Major.query.order_by(Major.code).all()

    @staticmethod
    def create(code: str, name: str | None = None) -> Major:
        major = Major(code=code, name=name)
        db.session.add(major)
        db.session.flush()
        return major

    @staticmethod
    def update(major: Major, code: str | None = None, name: str | None = None) -> Major:
        if code is not None:
            major.code = code
        if name is not None:
            major.name = name
        db.session.flush()
        return major

    @staticmethod
    def delete(major: Major) -> None:
        db.session.delete(major)
        db.session.flush()


class StudentRepository:
    """Repository for Student operations."""

    @staticmethod
    def find_by_identification(identification: str) -> Student:
        return (
            Student.query
            .options(
                joinedload(Student.major),
                joinedload(Student.grades).joinedload(Grade.subject),
                joinedload(Student.grades).joinedload(Grade.academic_period),
            )
            .filter_by(identification=identification)
            .first()
        )

    @staticmethod
    def find_by_id(student_id: int) -> Student:
        return db.session.get(Student, student_id)

    @staticmethod
    def get_map_for(identifications) -> dict:
        """{identification: Student} para las cédulas dadas, sin joinedload.
        Pensado para ingesta: sólo columnas base, acotado con IN (...)."""
        ids = list({i for i in identifications if i})
        if not ids:
            return {}
        students = Student.query.filter(Student.identification.in_(ids)).all()
        return {s.identification: s for s in students}

    @staticmethod
    def find_by_prefix(prefix: str) -> list:
        escaped = _escape_like(prefix)
        return (
            Student.query
            .options(
                joinedload(Student.major),
                joinedload(Student.grades).joinedload(Grade.subject),
                joinedload(Student.grades).joinedload(Grade.academic_period),
            )
            .filter(Student.identification.like(f"{escaped}%", escape='\\'))
            .all()
        )

    @staticmethod
    def find_by_period_paginated(
        period_code: str,
        init: int,
        limit: int,
        order: str,
        ascending: bool,
        carrera: str | None,
        nombre: str | None,
    ) -> tuple[list, int]:
        order_map = {
            "cedula": Student.identification,
            "nombre": Student.full_name,
            "carrera": Major.code,
        }
        col = order_map.get(order, Student.full_name)

        base_query = (
            db.session.query(Student.id)
            .join(Major, Student.major_id == Major.id)
            .join(Grade, Grade.student_id == Student.id)
            .join(AcademicPeriod, Grade.academic_period_id == AcademicPeriod.id)
            .filter(AcademicPeriod.code == period_code)
            .distinct()
        )

        if carrera:
            base_query = base_query.filter(Major.code == carrera)
        if nombre:
            escaped = _escape_like(nombre)
            base_query = base_query.filter(Student.full_name.ilike(f"%{escaped}%", escape='\\'))

        total = db.session.query(func.count()).select_from(base_query.subquery()).scalar()

        data_query = (
            db.session.query(Student)
            .join(Major, Student.major_id == Major.id)
            .join(Grade, Grade.student_id == Student.id)
            .join(AcademicPeriod, Grade.academic_period_id == AcademicPeriod.id)
            .filter(AcademicPeriod.code == period_code)
            .options(joinedload(Student.major))
            .distinct()
        )

        if carrera:
            data_query = data_query.filter(Major.code == carrera)
        if nombre:
            escaped = _escape_like(nombre)
            data_query = data_query.filter(Student.full_name.ilike(f"%{escaped}%", escape='\\'))

        students = data_query.order_by(asc_fn(col) if ascending else desc_fn(col)).offset(init).limit(limit).all()
        return students, total

    @staticmethod
    def create(identification: str, full_name: str, major_id: int) -> Student:
        student = Student(
            identification=identification,
            full_name=full_name,
            major_id=major_id,
        )
        db.session.add(student)
        db.session.flush()
        return student


class SedeRepository:
    """Repository for Sede operations."""

    @staticmethod
    def find_or_create(universidad: str, centro_local: str, oficina: str | None = None) -> Sede:
        sede = Sede.query.filter_by(
            universidad=universidad,
            centro_local=centro_local,
            oficina=oficina,
        ).first()
        if not sede:
            sede = Sede(universidad=universidad, centro_local=centro_local, oficina=oficina)
            db.session.add(sede)
            db.session.flush()
        return sede


class AcademicPeriodRepository:
    """Repository for AcademicPeriod operations."""

    @staticmethod
    def find_by_code(code: str, sede_id: int | None = None) -> AcademicPeriod:
        return AcademicPeriod.query.filter_by(code=code, sede_id=sede_id).first()

    @staticmethod
    def exists_by_code(code: str) -> bool:
        return db.session.query(
            AcademicPeriod.query.filter_by(code=code).exists()
        ).scalar()

    @staticmethod
    def create(
        code: str,
        sede_id: int | None = None,
        uploaded_by_id: int | None = None,
        uploaded_by_email: str | None = None,
        uploaded_by_fullname: str | None = None,
        uploaded_at: datetime | None = None,
        source_file: str | None = None,
    ) -> AcademicPeriod:
        period = AcademicPeriod(
            code=code,
            sede_id=sede_id,
            uploaded_by_id=uploaded_by_id,
            uploaded_by_email=uploaded_by_email,
            uploaded_by_fullname=uploaded_by_fullname,
            uploaded_at=uploaded_at,
            source_file=source_file,
        )
        db.session.add(period)
        db.session.flush()
        return period

    @staticmethod
    def get_all() -> list:
        return AcademicPeriod.query.all()

    @staticmethod
    def delete_period_cascade(period_code: str) -> dict | None:
        period = AcademicPeriod.query.filter_by(code=period_code).first()
        if not period:
            return None

        affected_student_ids = [
            row[0]
            for row in (
                db.session.query(Grade.student_id)
                .filter(Grade.academic_period_id == period.id)
                .distinct()
                .all()
            )
        ]

        grades_deleted = Grade.query.filter_by(academic_period_id=period.id).delete()

        # Huérfanos en una sola consulta (NOT EXISTS), no un SELECT por estudiante.
        orphan_ids = []
        if affected_student_ids:
            orphan_ids = [
                row[0]
                for row in (
                    db.session.query(Student.id)
                    .filter(Student.id.in_(affected_student_ids))
                    .filter(~Grade.query.filter(Grade.student_id == Student.id).exists())
                    .all()
                )
            ]
        if orphan_ids:
            Student.query.filter(Student.id.in_(orphan_ids)).delete(synchronize_session=False)

        source_file = period.source_file
        sede_id = period.sede_id

        db.session.delete(period)

        return {
            "grades_deleted": grades_deleted,
            "students_deleted": len(orphan_ids),
            "source_file": source_file,
            "sede_id": sede_id,
        }


class AcademicPeriodAuditRepository:
    """Repository for AcademicPeriodAudit operations."""

    @staticmethod
    def create(
        period_code: str,
        operation: str,
        sede_id: int | None = None,
        user_email: str | None = None,
        user_fullname: str | None = None,
        operation_at: datetime | None = None,
        source_file: str | None = None,
        ip_address: str | None = None,
        affected_rows: dict | None = None,
    ) -> AcademicPeriodAudit:
        audit = AcademicPeriodAudit(
            period_code=period_code,
            operation=operation,
            sede_id=sede_id,
            user_email=user_email,
            user_fullname=user_fullname,
            operation_at=operation_at or datetime.now(timezone.utc),
            source_file=source_file,
            ip_address=ip_address,
            affected_rows=affected_rows,
        )
        db.session.add(audit)
        return audit

    @staticmethod
    def get_paginated(init: int, limit: int) -> tuple[list, int]:
        query = AcademicPeriodAudit.query.order_by(AcademicPeriodAudit.operation_at.desc())
        total = query.count()
        return query.offset(init).limit(limit).all(), total


class GradeRepository:
    """Repository for Grade (Nota) operations."""

    @staticmethod
    def find_by_student_and_period(student_id: int, period_code: str) -> list:
        return (
            Grade.query
            .options(joinedload(Grade.subject), joinedload(Grade.academic_period))
            .join(AcademicPeriod, Grade.academic_period_id == AcademicPeriod.id)
            .filter(
                Grade.student_id == student_id,
                AcademicPeriod.code == period_code,
            )
            .all()
        )

    @staticmethod
    def find_existing(student_id: int, subject_id: int, academic_period_id: int = None) -> Grade:
        return Grade.query.filter_by(
            student_id=student_id,
            subject_id=subject_id,
            academic_period_id=academic_period_id,
        ).first()

    @staticmethod
    def get_existing_map_for_period(academic_period_id: int) -> dict:
        """{(student_id, subject_id, period_id): Grade} para un período (1 SELECT)."""
        if academic_period_id is None:
            return {}
        grades = Grade.query.filter_by(academic_period_id=academic_period_id).all()
        return {
            (g.student_id, g.subject_id, g.academic_period_id): g
            for g in grades
        }

    @staticmethod
    def bulk_create(mappings: list) -> None:
        """INSERT masivo de notas nuevas (Grade es hoja, no se repuebla PK).

        Usa insert(Grade).values(list): un único INSERT ... VALUES (...),(...)
        en una sola sentencia. Con execute(insert(), list) pg8000 fragmenta en
        ~3 filas por round-trip cuando los dicts tienen NULLs heterogéneos
        (calificacion/academic_period_id), lo que con Neon remoto convertía la
        carga de ~1000 notas en >90s. values(list) lo resuelve en una sentencia."""
        if mappings:
            db.session.execute(insert(Grade).values(mappings))

    @staticmethod
    def create(
        condition: str,
        student_id: int,
        subject_id: int,
        objectives_achieved: int,
        objectives_total: int,
        calificacion: str | None = None,
        academic_period_id: int = None,
        absent: bool = False,
    ) -> Grade:
        grade = Grade(
            condition=condition,
            student_id=student_id,
            subject_id=subject_id,
            academic_period_id=academic_period_id,
            absent=absent,
            objectives_achieved=objectives_achieved,
            objectives_total=objectives_total,
            calificacion=calificacion,
        )
        db.session.add(grade)
        db.session.flush()
        return grade

    @staticmethod
    def update(
        grade: Grade,
        condition: str,
        objectives_achieved: int,
        objectives_total: int,
        calificacion: str | None = None,
        absent: bool = False,
    ) -> Grade:
        grade.condition           = condition
        grade.absent              = absent
        grade.objectives_achieved = objectives_achieved
        grade.objectives_total    = objectives_total
        grade.calificacion        = calificacion
        db.session.flush()
        return grade
