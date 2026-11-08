"""Alembic environment.

The bot runs migrations at startup and passes its own connection in
config.attributes["connection"]. The alembic CLI has no connection and
opens one from DATABASE_URL (read from .env).
"""
import asyncio
import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine

from spr.db import async_database_url
from spr.models import Base

target_metadata = Base.metadata

# Only the CLI has alembic.ini; the bot keeps its own output
if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name, disable_existing_loggers=False)


def database_url():
    load_dotenv(".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is missing. Set it in .env.")
    return async_database_url(url)


def run_migrations(connection):
    context.configure(
        connection=connection, target_metadata=target_metadata, compare_type=True
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_from_cli():
    engine = create_async_engine(database_url())
    async with engine.connect() as connection:
        await connection.run_sync(run_migrations)
        await connection.commit()
    await engine.dispose()


def run_migrations_offline():
    """`alembic upgrade head --sql`: print the SQL instead of running it"""
    context.configure(
        url=database_url().render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
elif context.config.attributes.get("connection") is not None:
    run_migrations(context.config.attributes["connection"])
else:
    asyncio.run(run_migrations_from_cli())
