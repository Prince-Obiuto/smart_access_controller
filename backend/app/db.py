from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker, Session
from .config import Settings


class Database:
    def __init__(self, settings: Settings):
        self.engine = create_engine(settings.database_url, pool_pre_ping=True)
        self.session = sessionmaker(self.engine, expire_on_commit=False)

    def health(self):
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))


# This small installation serializes access-domain mutations; constraints also guard
# independent writers. Lock ordering: domain lock, entity rows, operation rows.
def domain_lock(db):
    db.execute(text("SELECT pg_advisory_xact_lock(7314201)"))


@event.listens_for(Session, "after_commit")
def committed_uploads(session):
    session.info.pop("new_upload_paths", None)


@event.listens_for(Session, "after_rollback")
def rollback_uploads(session):
    for path in session.info.pop("new_upload_paths", []):
        path.unlink(missing_ok=True)
