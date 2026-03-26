import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Import Base and all models so Alembic detects them.
from app.database import Base
from app.models import digest_entry, digest_page, digest_publication, digest_run, event_cluster, event_cluster_assessment, llm_usage, pipeline_run, pipeline_run_step, raw_item, source, story, story_facts  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_url() -> str:
    # Use YAML-based settings (same source as the app).
    # APP_CONFIG_PATH selects which file to load.
    from app.config import settings
    return settings.database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _get_url()
    connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
