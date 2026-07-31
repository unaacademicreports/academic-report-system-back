-- =========================================================
-- newsletter_db — Schema completo
-- Aplica sobre una base de datos vacía y recién creada.
-- =========================================================

-- ─────────────────────────────────────────
-- AUTH
-- ─────────────────────────────────────────

CREATE TABLE roles (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    description TEXT
);

CREATE TABLE users (
    id               SERIAL PRIMARY KEY,
    firstname        VARCHAR(100) NOT NULL,
    lastname         VARCHAR(100) NOT NULL,
    username         VARCHAR(100) NOT NULL UNIQUE,
    email            VARCHAR(100) NOT NULL UNIQUE,
    password_hash    VARCHAR(255) NOT NULL,
    phone            VARCHAR(100),
    create_at        TIMESTAMP NOT NULL DEFAULT NOW(),
    modificated_at   TIMESTAMP,
    role_id          INTEGER REFERENCES roles(id) ON DELETE SET NULL,
    is_active        BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE revoked_tokens (
    id         SERIAL PRIMARY KEY,
    jti        VARCHAR(36) NOT NULL,
    revoked_at TIMESTAMP   NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_revoked_tokens_jti UNIQUE (jti)
);

-- ─────────────────────────────────────────
-- ACADEMIC DOMAIN
-- ─────────────────────────────────────────

CREATE TABLE majors (
    id   SERIAL PRIMARY KEY,
    code VARCHAR(10)  NOT NULL UNIQUE,
    name VARCHAR(150)
);

CREATE TABLE students (
    id             SERIAL PRIMARY KEY,
    identification VARCHAR(20)  NOT NULL UNIQUE,
    full_name      VARCHAR(150) NOT NULL,
    major_id       INTEGER NOT NULL REFERENCES majors(id)
);

CREATE TABLE subjects (
    id   SERIAL PRIMARY KEY,
    code VARCHAR(10)  NOT NULL UNIQUE,
    name VARCHAR(150) NOT NULL
);

CREATE TABLE sedes (
    id            SERIAL PRIMARY KEY,
    universidad   VARCHAR(200) NOT NULL,
    centro_local  VARCHAR(200) NOT NULL,
    oficina       VARCHAR(200)
);

CREATE TABLE academic_periods (
    id                   SERIAL PRIMARY KEY,
    code                 VARCHAR(20) NOT NULL,
    sede_id              INTEGER REFERENCES sedes(id),
    uploaded_by_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
    uploaded_by_email    VARCHAR(100),
    uploaded_by_fullname VARCHAR(200),
    uploaded_at          TIMESTAMP,
    source_file          VARCHAR(255),
    CONSTRAINT uq_period_code_sede UNIQUE (code, sede_id)
);

CREATE TABLE academic_periods_audit (
    id            SERIAL PRIMARY KEY,
    period_code   VARCHAR(20)  NOT NULL,
    operation     VARCHAR(20)  NOT NULL,  -- CREACIÓN | ACTUALIZACIÓN | ELIMINACIÓN
    sede_id       INTEGER,
    user_email    VARCHAR(100),
    user_fullname VARCHAR(200),
    operation_at  TIMESTAMP    NOT NULL,
    source_file   VARCHAR(255),
    ip_address    VARCHAR(45),
    affected_rows JSON
);

CREATE TABLE users_audit (
    id             SERIAL PRIMARY KEY,
    user_username  VARCHAR(100) NOT NULL,
    operation      VARCHAR(20)  NOT NULL,  -- CREACIÓN | ACTUALIZACIÓN | ELIMINACIÓN
    user_email     VARCHAR(100),
    user_fullname  VARCHAR(200),
    actor_email    VARCHAR(100),
    actor_fullname VARCHAR(200),
    operation_at   TIMESTAMP    NOT NULL,
    ip_address     VARCHAR(45),
    affected_data  JSON
);

CREATE TABLE grades (
    id                  SERIAL PRIMARY KEY,
    condition           VARCHAR(20),              -- RG | RP
    absent              BOOLEAN NOT NULL DEFAULT FALSE,
    objectives_achieved INTEGER NOT NULL DEFAULT 0,
    objectives_total    INTEGER NOT NULL DEFAULT 0,
    calificacion        VARCHAR(10),
    student_id          INTEGER NOT NULL REFERENCES students(id),
    subject_id          INTEGER NOT NULL REFERENCES subjects(id),
    academic_period_id  INTEGER REFERENCES academic_periods(id),
    CONSTRAINT uq_grade_student_subject_period
        UNIQUE (student_id, subject_id, academic_period_id)
);

-- ─────────────────────────────────────────
-- ÍNDICES
-- ─────────────────────────────────────────

CREATE INDEX idx_students_identification    ON students(identification);
CREATE INDEX idx_students_major_id          ON students(major_id);

CREATE INDEX idx_grades_student_id          ON grades(student_id);
CREATE INDEX idx_grades_subject_id          ON grades(subject_id);
CREATE INDEX idx_grades_academic_period_id  ON grades(academic_period_id);

CREATE INDEX idx_users_username             ON users(username);
CREATE INDEX idx_users_email               ON users(email);
CREATE INDEX idx_users_role_id             ON users(role_id);

CREATE UNIQUE INDEX ix_revoked_tokens_jti  ON revoked_tokens(jti);

CREATE INDEX idx_ap_audit_period_code      ON academic_periods_audit(period_code);
CREATE INDEX idx_ap_audit_operation_at     ON academic_periods_audit(operation_at);

CREATE INDEX idx_users_audit_username      ON users_audit(user_username);
CREATE INDEX idx_users_audit_operation_at  ON users_audit(operation_at);

CREATE INDEX idx_sedes_centro_local        ON sedes(centro_local);
CREATE UNIQUE INDEX uq_sedes               ON sedes(universidad, centro_local, COALESCE(oficina, ''));

-- ─────────────────────────────────────────
-- SEED DATA
-- ─────────────────────────────────────────

INSERT INTO roles (name, description) VALUES
    ('Admin',  'Acceso total: gestión de usuarios, roles y reportes.'),
    ('Editor', 'Puede cargar y gestionar reportes académicos.'),
    ('Viewer', 'Solo lectura: consulta de estudiantes y períodos.');

-- Usuario admin inicial: NO versionar un hash real. Generar el propio con:
--   python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('Admin1234!'))"
-- y reemplazar <PASSWORD_HASH_GENERADO> antes de ejecutar este script.
INSERT INTO users (firstname, lastname, username, email, password_hash, role_id) VALUES
    ('Admin', 'Sistema', 'admin', 'admin@sistema.com', 'scrypt:32768:8:1$WRr3hPhvcTjBTyQE$51db955aa6698ac900283b93c0e39558e7e348c3d45d71455ff4aee83d57ab2be8c9bad699017a609a4fece318807c05e407fbcdffadd2015d799189709d2517',
     (SELECT id FROM roles WHERE name = 'Admin'));