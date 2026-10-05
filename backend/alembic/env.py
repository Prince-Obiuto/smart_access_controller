from alembic import context
from app.config import Settings
from app.db import Database
from app.models import Base

config = context.config
settings = Settings()
if context.is_offline_mode():
    context.configure(
        url=settings.database_url, target_metadata=Base.metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    with Database(settings).engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=Base.metadata, compare_type=True
        )
        with context.begin_transaction():
            context.run_migrations()
