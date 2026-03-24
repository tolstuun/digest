import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://digest:digest@localhost:5432/digest"
)

engine = create_engine(DATABASE_URL)
TestingSessionLocal = sessionmaker(engine)


@pytest.fixture(scope="session", autouse=True)
def setup_database():
    """Create all tables once for the test session; drop them afterwards."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db(setup_database):
    """Provide a DB session; roll back and truncate all tables after each test."""
    session = TestingSessionLocal()
    yield session
    session.rollback()
    # Truncate in reverse dependency order so FK constraints don't fire.
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.commit()
    session.close()


@pytest.fixture()
def client(db):
    """HTTP test client wired to the per-test DB session."""

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
