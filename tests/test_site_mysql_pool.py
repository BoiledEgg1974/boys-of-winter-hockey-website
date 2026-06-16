"""Shared site MySQL engine must not open a pool per Flask app mount."""
from __future__ import annotations

import pytest
from sqlalchemy.engine import Engine

from app.config import shared_site_mysql_engine, site_bind_engine_config


def test_site_bind_reuses_one_mysql_engine_per_process():
    pymysql = pytest.importorskip("pymysql")
    del pymysql

    shared_site_mysql_engine.cache_clear()
    try:
        uri = "mysql+pymysql://user:pass@localhost/testdb"
        first = site_bind_engine_config(uri)
        second = site_bind_engine_config(uri)
        assert isinstance(first, Engine)
        assert first is second
    finally:
        shared_site_mysql_engine.cache_clear()


def test_site_bind_sqlite_uri_unchanged():
    uri = "sqlite:///instance/site_membership.db"
    assert site_bind_engine_config(uri) == uri
