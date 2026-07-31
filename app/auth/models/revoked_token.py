from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from app.extensions import db


class RevokedToken(db.Model):
    __tablename__ = 'revoked_tokens'

    id = db.Column(db.Integer, primary_key=True)
    jti = db.Column(db.String(36), unique=True, nullable=False, index=True)
    revoked_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    @classmethod
    def is_revoked(cls, jti: str) -> bool:
        return db.session.query(
            cls.query.filter_by(jti=jti).exists()
        ).scalar()

    @classmethod
    def revoke(cls, jti: str) -> None:
        token = cls(jti=jti)
        db.session.add(token)
        try:
            db.session.commit()
        except IntegrityError:
            # jti ya revocado por un request concurrente: mismo resultado final
            db.session.rollback()
