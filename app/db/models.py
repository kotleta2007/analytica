"""SQLAlchemy models. One file until it hurts.

Empty for now: the telemetry tables arrive in phase 01. `Base` lives here so
alembic has a metadata object to autogenerate against from the start.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
