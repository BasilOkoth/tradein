from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from .config import settings
from .models import Base

def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url

database_url = normalize_database_url(settings.database_url)
connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}

engine = create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=engine)


# Lightweight additive migration for existing installations. No user data is deleted.
from sqlalchemy import inspect, text
with engine.begin() as conn:
    columns = {c['name'] for c in inspect(conn).get_columns('trading_sessions')}
    for col, sqltype in (
        ('v1_gate_threshold', 'FLOAT'),
        ('v1_gate_state', "VARCHAR(20) DEFAULT 'WAIT_HIGH'"),
        ('v1_gate_trades', 'INTEGER DEFAULT 0'),
    ):
        if col not in columns:
            conn.execute(text(f'ALTER TABLE trading_sessions ADD COLUMN {col} {sqltype}'))
