"""Shared site MySQL engine must not open a pool per Flask app mount."""
from __future__ import annotations

import pytest
from sqlalchemy.engine import Engine

from app.config import (
    install_shared_site_mysql_engine,
    shared_site_mysql_engine,
    site_bind_engine_config,
)


def test_site_bind_mysql_returns_engine_options_dict():
    cfg = site_bind_engine_config("mysql+pymysql://user:pass@localhost/testdb")
    assert isinstance(cfg, dict)
    assert str(cfg["url"]).startswith("mysql")
    assert cfg["pool_size"] == 3
    assert cfg["max_overflow"] == 5


def test_shared_site_mysql_engine_is_singleton():
    pytest.importorskip("pymysql")
    shared_site_mysql_engine.cache_clear()
    try:
        uri = "mysql+pymysql://user:pass@localhost/testdb"
        first = shared_site_mysql_engine(uri)
        second = shared_site_mysql_engine(uri)
        assert isinstance(first, Engine)
        assert first is second
    finally:
        shared_site_mysql_engine.cache_clear()


def test_install_shared_site_mysql_engine_replaces_site_bind():
    pytest.importorskip("pymysql")
    from flask import Flask

    from app.league_db import db

    shared_site_mysql_engine.cache_clear()
    try:
        uri = "mysql+pymysql://user:pass@localhost/testdb"
        app = Flask(__name__)
        app.config.from_mapping(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_BINDS={"site": site_bind_engine_config(uri)},
            SITE_SQLALCHEMY_DATABASE_URI=uri,
        )
        db.init_app(app)
        with app.app_context():
            before = db.engines["site"]
            install_shared_site_mysql_engine(db, app)
            after = db.engines["site"]
            assert after is shared_site_mysql_engine(uri)
            assert before is not after
    finally:
        shared_site_mysql_engine.cache_clear()


def test_site_bind_sqlite_uri_unchanged():
    uri = "sqlite:///instance/site_membership.db"
    assert site_bind_engine_config(uri) == uri
