"""
Модели базы данных (SQLAlchemy). models.py
"""

from sqlalchemy import Column, Integer, Float, String, Text
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class RmsRecord(Base):
    """История RMS-значений (виброскорость, виброускорение, пик-фактор)."""
    __tablename__ = 'rms_records'

    id = Column(Integer, primary_key=True)
    dev_id = Column(Integer, index=True)
    sensor_id = Column(Integer, index=True)
    axis = Column(Integer)
    rms_speed = Column(Float)
    rms_accel = Column(Float)
    crest_factor = Column(Float)
    alarm = Column(Integer)               # 0/1 – флаг аварии
    timestamp = Column(Float, index=True) # UNIX timestamp


class CycleEvent(Base):
    """События циклов (включение/выключение станка)."""
    __tablename__ = 'cycle_events'

    id = Column(Integer, primary_key=True)
    dev_id = Column(Integer, index=True)
    status = Column(String)               # 'cycle_on' или 'cycle_off'
    timestamp = Column(Float, index=True)


class AlarmEvent(Base):
    """Полная информация об аварии."""
    __tablename__ = 'alarm_events'

    id = Column(Integer, primary_key=True)
    dev_id = Column(Integer, index=True)
    sensor_id = Column(Integer, index=True)
    axis = Column(Integer)
    rms_speed = Column(Float)
    rms_accel = Column(Float)
    crest_factor = Column(Float)
    frame_id = Column(Integer, index=True) # идентификатор кадра
    timestamp = Column(Float, index=True)
    spectrum = Column(Text)               # JSON-строка со списком floats
    waveform = Column(Text)               # JSON-строка со списком floats
    cause = Column(String)                # текстовое описание причины (опционально)


class TestRecord(Base):
    """Сохранённые тесты."""
    __tablename__ = 'test_records'

    id = Column(Integer, primary_key=True)
    dev_id = Column(Integer, index=True)
    name = Column(String)
    is_reference = Column(Integer, default=0)  # 1 – эталон, 0 – обычный
    total_alarms = Column(Integer, default=0)
    start_time = Column(Float)
    end_time = Column(Float)
    data = Column(Text)                    # JSON-строка со всеми данными теста