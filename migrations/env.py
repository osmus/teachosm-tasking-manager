from __future__ import with_statement
from alembic import context
from sqlalchemy import engine_from_config, pool
from geoalchemy2 import alembic_helpers
from logging.config import fileConfig
from flask import current_app
import logging

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
fileConfig(config.config_file_name)
logger = logging.getLogger("alembic.env")

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata

config.set_main_option(
    "sqlalchemy.url", current_app.config.get("SQLALCHEMY_DATABASE_URI")
)
target_metadata = current_app.extensions["migrate"].db.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.
exclude_tables = config.get_section("alembic:exclude").get("tables", "").split(",")
exclude_index = config.get_section("alembic:exclude").get("index", "").split(",")


def include_object(object, name, type_, reflected, compare_to):
    """
    Custom helper function that enables us to ignore our excluded tables in the autogen sweep
    """
    if type_ == "table" and name in exclude_tables:
        return False
    elif type_ == "index" and name in exclude_index:
        return False
    else:
        return alembic_helpers.include_object(
            object, name, type_, reflected, compare_to
        )


def run_migrations_offline():
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        include_object=include_object,
        process_revision_directives=alembic_helpers.writer,
        render_item=alembic_helpers.render_item,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    # this callback is used to prevent an auto-migration from being generated
    # when there are no changes to the schema
    # reference: http://alembic.readthedocs.org/en/latest/cookbook.html
    def process_revision_directives(context, revision, directives):
        alembic_helpers.writer(context, revision, directives)
        if getattr(config.cmd_opts, "autogenerate", False):
            script = directives[0]
            if script.upgrade_ops.is_empty():
                directives[:] = []
                logger.info("No changes in schema detected.")

    engine = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    connection = engine.connect()
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        process_revision_directives=process_revision_directives,
        include_object=include_object,
        render_item=alembic_helpers.render_item,
        **current_app.extensions["migrate"].configure_args,
    )

    try:
        with context.begin_transaction():
            context.run_migrations()
    finally:
        connection.close()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
