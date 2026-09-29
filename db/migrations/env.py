from alembic import context
from fiatium.db import engine

with engine().connect() as connection:
    context.configure(connection=connection, transactional_ddl=True)
    with context.begin_transaction():
        context.run_migrations()
