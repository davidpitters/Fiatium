"""Compose-only bootstrap against its dedicated SQL Server container."""

import os

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, create_engine, text

from fiatium.db import engine
from fiatium.seed import main as seed


def main():
    password = os.environ["MSSQL_SA_PASSWORD"]
    app_password = os.environ["FIATIUM_APP_PASSWORD"]
    master = create_engine(
        URL.create(
            "mssql+pyodbc",
            username="sa",
            password=password,
            host="sqlserver",
            database="master",
            query={"driver": "ODBC Driver 18 for SQL Server", "TrustServerCertificate": "yes"},
        ),
        isolation_level="AUTOCOMMIT",
    )
    with master.connect() as conn:
        conn.exec_driver_sql("IF DB_ID('FiatiumDev') IS NULL CREATE DATABASE FiatiumDev")
        # Passwords are generated URL-safe; SQL DDL does not support bound password parameters.
        escaped = app_password.replace("'", "''")
        conn.exec_driver_sql(
            f"IF SUSER_ID('fiatium_app') IS NULL CREATE LOGIN fiatium_app WITH PASSWORD='{escaped}'"
        )
    command.upgrade(Config("alembic.ini"), "head")
    with engine().begin() as conn:
        conn.execute(
            text("""
            IF USER_ID('fiatium_app') IS NULL CREATE USER fiatium_app FOR LOGIN fiatium_app;
            GRANT SELECT,INSERT,UPDATE ON SCHEMA::dbo TO fiatium_app;
            DENY INSERT,UPDATE,DELETE ON journal_transactions TO fiatium_app;
            DENY INSERT,UPDATE,DELETE ON journal_lines TO fiatium_app;
            DENY INSERT,UPDATE,DELETE ON accounts TO fiatium_app;
            DENY INSERT,UPDATE,DELETE ON alembic_version TO fiatium_app;
            GRANT EXECUTE ON post_journal TO fiatium_app;
        """)
        )
    seed()


if __name__ == "__main__":
    main()
