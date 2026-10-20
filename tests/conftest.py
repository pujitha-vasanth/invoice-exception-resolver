import pytest
from agents import set_tracing_disabled

from resolver import db, matching, seed_data

set_tracing_disabled(True)  # tests never send traces anywhere


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    """A fresh database with sample data and the three-way match already run."""
    monkeypatch.setenv("AP_DB_PATH", str(tmp_path / "test.db"))
    seed_data.reset_database()
    connection = db.connect()
    matching.run_matching(connection)
    yield connection
    connection.close()
