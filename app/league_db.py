"""Shared Flask-SQLAlchemy handle: league tables (default bind) + site tables (``site`` bind)."""
from __future__ import annotations

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
