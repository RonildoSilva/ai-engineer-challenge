"""Adaptador SQLite: suíte de contrato + garantias específicas (somente leitura, injeção)."""

from __future__ import annotations

import sqlite3
import threading

import pytest

from emporio.adapters.sqlite_repo import SqliteRepository
from tests.contract.repository_contract import RepositoryContract


class TestSqliteRepository(RepositoryContract):
    @pytest.fixture
    def repo(self, built_store):
        r = SqliteRepository(built_store)
        yield r
        r.close()


def test_missing_db_has_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="emporio pipeline run"):
        SqliteRepository(tmp_path / "nao_existe.db")


def test_connection_is_read_only(built_store):
    repo = SqliteRepository(built_store)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        repo._con.execute("DELETE FROM products")


def test_filters_are_parameterized(built_store):
    """Valores maliciosos são tratados como dados, nunca como SQL."""
    repo = SqliteRepository(built_store)
    assert repo.list_products(availability=["disponivel' OR '1'='1"]) == []
    assert len(repo.list_products()) == 65


def test_one_connection_per_thread(built_store):
    repo = SqliteRepository(built_store)
    results: list[int] = []

    def worker():
        results.append(len(repo.list_products()))
        repo.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [65] * 4
