"""
Настройка подключения к базе данных и сессий. datebase.py
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from .config import DATABASE_URL
from .models import Base

# Создаём движок SQLAlchemy
engine = create_engine(
    DATABASE_URL,
    echo=False,                # не выводить SQL-запросы в консоль
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
)

# Фабрика сессий (scoped_session для потокобезопасности)
SessionLocal = scoped_session(sessionmaker(bind=engine))


def init_db():
    """Создать все таблицы в базе данных (если их нет)."""
    Base.metadata.create_all(engine)


def get_db():
    """Генератор сессий для FastAPI Depends."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()