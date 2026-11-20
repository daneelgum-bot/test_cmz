# ============ server/api.py ============ api.py

import json
import time
import logging
from typing import Optional, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session
from .database import get_db, init_db
from .models import RmsRecord, CycleEvent, AlarmEvent, TestRecord
from .mqtt_handler import MQTTHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Vibration Monitoring Server")
mqtt_handler = MQTTHandler()

@app.on_event("startup")
async def startup_event():
    init_db()
    mqtt_handler.start()
    logger.info("Server started, DB initialized, MQTT started")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    mqtt_handler.register_websocket(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                cmd_data = json.loads(data)
                if cmd_data.get('type') == 'command':
                    dev_id = cmd_data.get('dev_id')
                    cmd = cmd_data.get('cmd')
                    if dev_id is not None and cmd:
                        mqtt_handler.publish_command(dev_id, cmd)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON received: {data}")
            except Exception as e:
                logger.error(f"Error processing WebSocket message: {e}")
    except WebSocketDisconnect:
        mqtt_handler.unregister_websocket(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        mqtt_handler.unregister_websocket(websocket)

# ---------- История RMS ----------
@app.get("/history/rms")
async def get_rms_history(
    dev_id: int,
    sensor_id: int,
    axis: int,
    start_ts: float,
    end_ts: float,
    db: Session = Depends(get_db)
):
    """Возвращает все RMS-записи за интервал без агрегации."""
    records = db.query(RmsRecord).filter(
        RmsRecord.dev_id == dev_id,
        RmsRecord.sensor_id == sensor_id,
        RmsRecord.axis == axis,
        RmsRecord.timestamp >= start_ts,
        RmsRecord.timestamp <= end_ts
    ).order_by(RmsRecord.timestamp.asc()).all()

    return [
        {
            'ts': r.timestamp,
            'rms_speed': r.rms_speed,
            'rms_accel': r.rms_accel,
            'crest': r.crest_factor,
            'alarm': r.alarm
        }
        for r in records
    ]

@app.delete("/history/clear")
async def clear_history(
    action: str = Query("all", description="all | trim_15min | trim_1hour | trim_24hours"),
    db: Session = Depends(get_db)
):
    if action == "all":
        db.query(RmsRecord).delete()
        db.query(CycleEvent).delete()
        db.commit()
        return {"message": "Вся история удалена"}
    elif action.startswith("trim_"):
        minutes = int(action.split("_")[1])
        cutoff = time.time() - minutes * 60
        db.query(RmsRecord).filter(RmsRecord.timestamp < cutoff).delete()
        db.query(CycleEvent).filter(CycleEvent.timestamp < cutoff).delete()
        db.commit()
        return {"message": f"История обрезана, оставлены последние {minutes} минут"}
    else:
        raise HTTPException(status_code=400, detail="Неверное действие")

@app.post("/history/import")
async def import_history(file: UploadFile = File(...), db: Session = Depends(get_db)):
    try:
        content = await file.read()
        data = json.loads(content.decode('utf-8'))
        rms_list = data.get('rms_records', [])
        cycle_list = data.get('cycle_events', [])
        for r in rms_list:
            db.add(RmsRecord(**r))
        for c in cycle_list:
            db.add(CycleEvent(**c))
        db.commit()
        return {"message": f"Импортировано: RMS={len(rms_list)}, циклы={len(cycle_list)}"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Ошибка импорта: {e}")

# ---------- События цикла ----------
@app.get("/cycle_events")
async def get_cycle_events(dev_id: int, start_ts: float, end_ts: float, db: Session = Depends(get_db)):
    events = db.query(CycleEvent).filter(
        CycleEvent.dev_id == dev_id,
        CycleEvent.timestamp >= start_ts,
        CycleEvent.timestamp <= end_ts
    ).order_by(CycleEvent.timestamp.asc()).all()
    return [{'ts': e.timestamp, 'status': e.status} for e in events]

@app.get("/current_cycle_start")
async def get_current_cycle_start(dev_id: int, db: Session = Depends(get_db)):
    events = db.query(CycleEvent).filter(CycleEvent.dev_id == dev_id).order_by(CycleEvent.timestamp.desc()).all()
    last_on = None
    last_off = None
    for e in events:
        if e.status == 'cycle_on' and last_on is None:
            last_on = e.timestamp
        elif e.status == 'cycle_off' and last_off is None:
            last_off = e.timestamp
    if last_on is not None and (last_off is None or last_on > last_off):
        return {'start_ts': last_on}
    return {'start_ts': None}

# ---------- Тесты ----------
@app.post("/tests")
async def save_test(test_data: dict, db: Session = Depends(get_db)):
    try:
        new_test = TestRecord(
            dev_id=test_data['device_id'],
            name=test_data['name'],
            is_reference=1 if test_data.get('is_reference') else 0,
            total_alarms=test_data.get('total_alarms', 0),
            start_time=test_data['start_time'],
            end_time=test_data['end_time'],
            data=json.dumps(test_data['sensors'])
        )
        db.add(new_test)
        db.commit()
        db.refresh(new_test)
        return {"id": new_test.id, "message": "Тест сохранён"}
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"Отсутствует поле: {e}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка сохранения теста: {e}")

@app.get("/tests")
async def list_tests(db: Session = Depends(get_db)):
    tests = db.query(TestRecord).all()
    return [
        {
            'id': t.id,
            'dev_id': t.dev_id,
            'name': t.name,
            'is_reference': t.is_reference,
            'total_alarms': t.total_alarms,
            'start_time': t.start_time,
            'end_time': t.end_time
        }
        for t in tests
    ]

@app.get("/tests/{test_id}")
async def get_test(test_id: int, db: Session = Depends(get_db)):
    test = db.query(TestRecord).filter(TestRecord.id == test_id).first()
    if not test:
        raise HTTPException(status_code=404, detail="Тест не найден")
    return {
        'id': test.id,
        'device_id': test.dev_id,
        'name': test.name,
        'is_reference': test.is_reference,
        'total_alarms': test.total_alarms,
        'start_time': test.start_time,
        'end_time': test.end_time,
        'sensors': json.loads(test.data)
    }

@app.delete("/tests/{test_id}")
async def delete_test(test_id: int, db: Session = Depends(get_db)):
    test = db.query(TestRecord).filter(TestRecord.id == test_id).first()
    if not test:
        raise HTTPException(status_code=404, detail="Тест не найден")
    db.delete(test)
    db.commit()
    return {"message": "Тест удалён"}

@app.post("/tests/{test_id}/set_reference")
async def set_reference_test(test_id: int, db: Session = Depends(get_db)):
    db.query(TestRecord).filter(TestRecord.is_reference == 1).update({'is_reference': 0})
    test = db.query(TestRecord).filter(TestRecord.id == test_id).first()
    if not test:
        raise HTTPException(status_code=404, detail="Тест не найден")
    test.is_reference = 1
    db.commit()
    return {"message": "Эталонный тест установлен"}

# ---------- Аварии ----------
@app.get("/alarms")
async def list_alarms(
    dev_id: Optional[int] = Query(None),
    sensor_id: Optional[int] = Query(None),
    start_ts: Optional[float] = Query(None),
    end_ts: Optional[float] = Query(None),
    db: Session = Depends(get_db)
):
    query = db.query(AlarmEvent)
    if dev_id is not None:
        query = query.filter(AlarmEvent.dev_id == dev_id)
    if sensor_id is not None:
        query = query.filter(AlarmEvent.sensor_id == sensor_id)
    if start_ts is not None:
        query = query.filter(AlarmEvent.timestamp >= start_ts)
    if end_ts is not None:
        query = query.filter(AlarmEvent.timestamp <= end_ts)
    alarms = query.order_by(AlarmEvent.timestamp.desc()).all()
    return [
        {
            'id': a.id,
            'dev_id': a.dev_id,
            'sensor_id': a.sensor_id,
            'axis': a.axis,
            'rms_speed': a.rms_speed,
            'rms_accel': a.rms_accel,
            'crest_factor': a.crest_factor,
            'frame_id': a.frame_id,
            'timestamp': a.timestamp,
            'spectrum': json.loads(a.spectrum) if a.spectrum else None,
            'waveform': json.loads(a.waveform) if a.waveform else None,
            'cause': a.cause
        }
        for a in alarms
    ]

@app.get("/alarms/{alarm_id}")
async def get_alarm(alarm_id: int, db: Session = Depends(get_db)):
    alarm = db.query(AlarmEvent).filter(AlarmEvent.id == alarm_id).first()
    if not alarm:
        raise HTTPException(status_code=404, detail="Авария не найдена")
    return {
        'id': alarm.id,
        'dev_id': alarm.dev_id,
        'sensor_id': alarm.sensor_id,
        'axis': alarm.axis,
        'rms_speed': alarm.rms_speed,
        'rms_accel': alarm.rms_accel,
        'crest_factor': alarm.crest_factor,
        'frame_id': alarm.frame_id,
        'timestamp': alarm.timestamp,
        'spectrum': json.loads(alarm.spectrum) if alarm.spectrum else None,
        'waveform': json.loads(alarm.waveform) if alarm.waveform else None,
        'cause': alarm.cause
    }

@app.delete("/alarms/all")
async def delete_all_alarms(db: Session = Depends(get_db)):
    """Удалить все записи об авариях."""
    db.query(AlarmEvent).delete()
    db.commit()
    return {"message": "Все аварии удалены"}

@app.delete("/alarms/{alarm_id}")
async def delete_alarm(alarm_id: int, db: Session = Depends(get_db)):
    alarm = db.query(AlarmEvent).filter(AlarmEvent.id == alarm_id).first()
    if not alarm:
        raise HTTPException(status_code=404, detail="Авария не найдена")
    db.delete(alarm)
    db.commit()
    return {"message": "Авария удалена"}