-- =========================================================
-- newsletter_db — Rollback completo
-- Elimina todos los objetos en orden inverso al de creación
-- respetando dependencias de foreign keys.
-- =========================================================

-- Índices (opcionales — DROP TABLE los elimina, pero los listamos
-- para poder hacer rollbacks parciales si fuera necesario)
DROP INDEX IF EXISTS uq_sedes;
DROP INDEX IF EXISTS idx_sedes_centro_local;
DROP INDEX IF EXISTS idx_users_audit_operation_at;
DROP INDEX IF EXISTS idx_users_audit_username;
DROP INDEX IF EXISTS idx_ap_audit_operation_at;
DROP INDEX IF EXISTS idx_ap_audit_period_code;
DROP INDEX IF EXISTS ix_revoked_tokens_jti;
DROP INDEX IF EXISTS idx_users_role_id;
DROP INDEX IF EXISTS idx_users_email;
DROP INDEX IF EXISTS idx_users_username;
DROP INDEX IF EXISTS idx_grades_academic_period_id;
DROP INDEX IF EXISTS idx_grades_subject_id;
DROP INDEX IF EXISTS idx_grades_student_id;
DROP INDEX IF EXISTS idx_students_major_id;
DROP INDEX IF EXISTS idx_students_identification;

-- Tablas en orden inverso al de creación
DROP TABLE IF EXISTS grades                 CASCADE;
DROP TABLE IF EXISTS academic_periods_audit CASCADE;
DROP TABLE IF EXISTS academic_periods       CASCADE;
DROP TABLE IF EXISTS sedes                  CASCADE;
DROP TABLE IF EXISTS subjects               CASCADE;
DROP TABLE IF EXISTS students               CASCADE;
DROP TABLE IF EXISTS majors                 CASCADE;
DROP TABLE IF EXISTS revoked_tokens         CASCADE;
DROP TABLE IF EXISTS users                  CASCADE;
DROP TABLE IF EXISTS users_audit            CASCADE;
DROP TABLE IF EXISTS roles                  CASCADE;
