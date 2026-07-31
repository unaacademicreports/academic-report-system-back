from app.extensions import db

###
#
# MAJOR # Carrera
#
###
class Major(db.Model):
    __tablename__ = 'majors'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(10), unique=True, nullable=False)
    name = db.Column(db.String(150), nullable=True)

    students = db.relationship('Student', backref='major', lazy=True)

    def __str__(self):
        return f"Major: {self.code}"

    def to_dict(self):
        return {
            'id': self.id,
            'code': self.code,
            'name': self.name,
        }

###
#
# STUDENT
#
###
class Student(db.Model):
    __tablename__ = 'students'

    id = db.Column(db.Integer, primary_key=True)
    identification = db.Column(db.String(20), unique=True, nullable=False)
    full_name = db.Column(db.String(150), nullable=False)

    major_id = db.Column(db.Integer, db.ForeignKey('majors.id'), nullable=False, index=True)

    grades = db.relationship('Grade', backref='student', lazy=True)

    def __str__(self):
        return f"Student: {self.identification}, Name: {self.full_name}"

    def to_dict(self):
        return {
            'id': self.id,
            'identification': self.identification,
            'full_name': self.full_name,
        }

###
#
# SUBJECT # Asignatura
#
###
class Subject(db.Model):
    __tablename__ = 'subjects'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(10), unique=True, nullable=False)
    name = db.Column(db.String(150), nullable=False)

    def __str__(self):
        return f"Subject: {self.code}, Name: {self.name}"

    def to_dict(self):
        return {
            'id': self.id,
            'code': self.code,
            'name': self.name,
        }

###
#
# SEDE
#
###
class Sede(db.Model):
    __tablename__ = 'sedes'

    id           = db.Column(db.Integer, primary_key=True)
    universidad  = db.Column(db.String(200), nullable=False)
    centro_local = db.Column(db.String(200), nullable=False)
    oficina      = db.Column(db.String(200), nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'universidad': self.universidad,
            'centro_local': self.centro_local,
            'oficina': self.oficina,
        }

###
#
# ACADEMIC PERIOD # Periodo Academico
#
###
class AcademicPeriod(db.Model):
    __tablename__ = 'academic_periods'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), nullable=False)
    sede_id = db.Column(db.Integer, db.ForeignKey('sedes.id'), nullable=True)
    uploaded_by_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True,
    )
    uploaded_by_email    = db.Column(db.String(100), nullable=True)
    uploaded_by_fullname = db.Column(db.String(200), nullable=True)
    uploaded_at = db.Column(db.DateTime, nullable=True)
    source_file = db.Column(db.String(255), nullable=True)

    uploaded_by = db.relationship('User', foreign_keys=[uploaded_by_id])
    sede        = db.relationship('Sede')

    __table_args__ = (
        db.UniqueConstraint('code', 'sede_id', name='uq_period_code_sede'),
    )

    def __str__(self):
        return f"Academic Period: {self.code}"

    def to_dict(self):
        return {
            'id': self.id,
            'code': self.code,
            'sede_id': self.sede_id,
            'uploaded_by_id': self.uploaded_by_id,
            'uploaded_by_email': self.uploaded_by_email,
            'uploaded_by_fullname': self.uploaded_by_fullname,
            'uploaded_at': self.uploaded_at.isoformat() if self.uploaded_at else None,
            'source_file': self.source_file,
        }

###
#
# USER AUDIT # Auditoría de usuarios
#
###
class UserAudit(db.Model):
    __tablename__ = 'users_audit'

    id            = db.Column(db.Integer, primary_key=True)
    user_username = db.Column(db.String(100), nullable=False)
    operation     = db.Column(db.String(20),  nullable=False)
    user_email     = db.Column(db.String(100), nullable=True)
    user_fullname  = db.Column(db.String(200), nullable=True)
    actor_email    = db.Column(db.String(100), nullable=True)
    actor_fullname = db.Column(db.String(200), nullable=True)
    operation_at   = db.Column(db.DateTime,    nullable=False)
    ip_address     = db.Column(db.String(45),  nullable=True)
    affected_data  = db.Column(db.JSON,        nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'user_username': self.user_username,
            'operation': self.operation,
            'user_email': self.user_email,
            'user_fullname': self.user_fullname,
            'actor_email': self.actor_email,
            'actor_fullname': self.actor_fullname,
            'operation_at': self.operation_at.isoformat() if self.operation_at else None,
            'ip_address': self.ip_address,
            'affected_data': self.affected_data,
        }

###
#
# ACADEMIC PERIOD AUDIT # Auditoría de períodos académicos
#
###
class AcademicPeriodAudit(db.Model):
    __tablename__ = 'academic_periods_audit'

    id            = db.Column(db.Integer, primary_key=True)
    period_code   = db.Column(db.String(20), nullable=False)
    operation     = db.Column(db.String(20), nullable=False)
    sede_id       = db.Column(db.Integer, nullable=True)
    user_email    = db.Column(db.String(100), nullable=True)
    user_fullname = db.Column(db.String(200), nullable=True)
    operation_at  = db.Column(db.DateTime, nullable=False)
    source_file   = db.Column(db.String(255), nullable=True)
    ip_address    = db.Column(db.String(45), nullable=True)
    affected_rows = db.Column(db.JSON, nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'period_code': self.period_code,
            'operation': self.operation,
            'sede_id': self.sede_id,
            'user_email': self.user_email,
            'user_fullname': self.user_fullname,
            'operation_at': self.operation_at.isoformat() if self.operation_at else None,
            'source_file': self.source_file,
            'ip_address': self.ip_address,
            'affected_rows': self.affected_rows,
        }

###
#
# GRADE # Nota
#
###
class Grade(db.Model):
    __tablename__ = 'grades'

    id                  = db.Column(db.Integer, primary_key=True)
    condition           = db.Column(db.String(20))          # RG | RP
    absent              = db.Column(db.Boolean, nullable=False, default=False, server_default='false')
    objectives_achieved = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    objectives_total    = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    calificacion        = db.Column(db.String(10), nullable=True)

    student_id         = db.Column(db.Integer, db.ForeignKey('students.id'), nullable=False, index=True)
    subject_id         = db.Column(db.Integer, db.ForeignKey('subjects.id'), nullable=False, index=True)
    academic_period_id = db.Column(db.Integer, db.ForeignKey('academic_periods.id'), nullable=True, index=True)

    subject         = db.relationship('Subject')
    academic_period = db.relationship('AcademicPeriod')

    __table_args__ = (
        db.UniqueConstraint('student_id', 'subject_id', 'academic_period_id', name='uq_grade_student_subject_period'),
    )

    def __str__(self):
        return f"Grade: {self.objectives_achieved}/{self.objectives_total}, Condition: {self.condition}"

    def to_dict(self):
        return {
            'id': self.id,
            'condition': self.condition,
            'absent': self.absent,
            'objectives_achieved': self.objectives_achieved,
            'objectives_total': self.objectives_total,
            'calificacion': self.calificacion,
            'student_id': self.student_id,
            'subject_id': self.subject_id,
            'academic_period_id': self.academic_period_id,
        }
