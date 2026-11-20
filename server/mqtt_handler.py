#mqtt_handler.py
import json
import struct
import time
import logging
import asyncio
import numpy as np
import paho.mqtt.client as mqtt
from collections import defaultdict
from .config import MQTT_BROKER, MQTT_PORT, LOG_LEVEL
from .database import SessionLocal
from .models import RmsRecord, CycleEvent, AlarmEvent

logging.basicConfig(level=getattr(logging, LOG_LEVEL))
logger = logging.getLogger(__name__)

BUF_SIZE = 2048


class MQTTHandler:
    def __init__(self):
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

        self.device_statuses = {}
        self.sensor_axis = defaultdict(lambda: -1)
        self.websocket_clients = set()
        self.loop = None

    def start(self):
        self.loop = asyncio.get_running_loop()
        self.client.connect(MQTT_BROKER, MQTT_PORT, 60)
        self.client.loop_start()
        logger.info(f"MQTT client connected to {MQTT_BROKER}:{MQTT_PORT}")

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            logger.info("MQTT connected, subscribing to topics")
            client.subscribe("devices/+/sensors/+/rms")
            client.subscribe("devices/+/sensors/+/spectrum")
            client.subscribe("devices/+/sensors/+/waveform")
            client.subscribe("devices/+/status")
            client.subscribe("devices/+/cycle_status")
            client.subscribe("devices/+/params")
            client.subscribe("devices/+/test/sensors/+/rms")
            client.subscribe("devices/+/test/sensors/+/spectrum1")
            client.subscribe("devices/+/test/sensors/+/spectrum2")
            client.subscribe("devices/+/alarm/sensors/+/rms")
            client.subscribe("devices/+/alarm/sensors/+/spectrum")
            client.subscribe("devices/+/alarm/sensors/+/waveform")
        else:
            logger.error(f"MQTT connection error, code={rc}")

    def _on_message(self, client, userdata, msg):
        print(f"[MQTT] {msg.topic} -> {msg.payload}")
        try:
            topic = msg.topic
            payload = msg.payload
            parts = topic.split('/')
            if len(parts) < 3 or parts[0] != 'devices':
                return
            dev_id = int(parts[1])
            timestamp = time.time()

            if len(parts) >= 5 and parts[2] == 'sensors':
                self._mark_device_online(dev_id)
                sensor_id = int(parts[3])
                data_type = parts[4]
                if data_type == 'rms':
                    self._handle_rms(dev_id, sensor_id, payload, timestamp)
                elif data_type == 'spectrum':
                    self._handle_spectrum(dev_id, sensor_id, payload)
                elif data_type == 'waveform':
                    self._handle_waveform(dev_id, sensor_id, payload)

            elif len(parts) == 3 and parts[2] == 'status':
                status_str = payload.decode()
                self.device_statuses[dev_id] = status_str
                print(f"[STATUS] Saved status for device {dev_id}: {status_str}")
                self._broadcast_json({
                    'type': 'device_status',
                    'dev_id': dev_id,
                    'status': status_str,
                    'timestamp': timestamp
                })

            elif len(parts) == 3 and parts[2] == 'cycle_status':
                status_str = payload.decode()
                db = SessionLocal()
                db.add(CycleEvent(dev_id=dev_id, status=status_str, timestamp=timestamp))
                db.commit()
                db.close()
                self._broadcast_json({
                    'type': 'cycle_status',
                    'dev_id': dev_id,
                    'status': status_str,
                    'timestamp': timestamp
                })

            elif len(parts) == 3 and parts[2] == 'params':
                self._mark_device_online(dev_id)
                try:
                    params = json.loads(payload.decode())
                    self._broadcast_json({
                        'type': 'params',
                        'dev_id': dev_id,
                        'params': params,
                        'timestamp': timestamp
                    })
                except Exception as e:
                    logger.error(f"Error parsing params: {e}")

            elif len(parts) >= 5 and parts[2] == 'test':
                self._mark_device_online(dev_id)
                self._handle_test_data(dev_id, parts, payload, timestamp)

            elif len(parts) >= 5 and parts[2] == 'alarm':
                self._mark_device_online(dev_id)
                self._handle_alarm_data(dev_id, parts, payload, timestamp)

        except Exception as e:
            logger.error(f"Error in _on_message: {e}", exc_info=True)

    def _mark_device_online(self, dev_id):
        if self.device_statuses.get(dev_id) != 'online':
            self.device_statuses[dev_id] = 'online'
            self._broadcast_json({
                'type': 'device_status',
                'dev_id': dev_id,
                'status': 'online',
                'timestamp': time.time()
            })

    def _handle_rms(self, dev_id, sensor_id, payload, timestamp):
        d = json.loads(payload.decode())
        axis = d.get('axis', 0)
        self.sensor_axis[(dev_id, sensor_id)] = axis

        record = RmsRecord(
            dev_id=dev_id,
            sensor_id=sensor_id,
            axis=axis,
            rms_speed=d['rms_speed'],
            rms_accel=d['rms_accel'],
            crest_factor=d['crest_factor'],
            alarm=int(d.get('alarm', 0)),
            timestamp=timestamp
        )
        db = SessionLocal()
        db.add(record)
        db.commit()
        db.close()

        self._broadcast_json({
            'type': 'rms',
            'dev_id': dev_id,
            'sensor_id': sensor_id,
            'axis': axis,
            'rms_speed': d['rms_speed'],
            'rms_accel': d['rms_accel'],
            'crest_factor': d['crest_factor'],
            'alarm': int(d.get('alarm', 0)),
            'timestamp': timestamp
        })

    def _handle_spectrum(self, dev_id, sensor_id, payload):
        axis = self.sensor_axis.get((dev_id, sensor_id), -1)
        if axis < 0 or len(payload) < 4:
            return
        binary_msg = struct.pack('<iii', dev_id, sensor_id, axis) + payload
        self._broadcast_binary(binary_msg)

    def _handle_waveform(self, dev_id, sensor_id, payload):
        axis = self.sensor_axis.get((dev_id, sensor_id), -1)
        if axis < 0 or len(payload) < 4:
            return
        binary_msg = struct.pack('<iii', dev_id, sensor_id, axis) + payload
        self._broadcast_binary(binary_msg)

    def _handle_test_data(self, dev_id, parts, payload, timestamp):
        if len(parts) < 5:
            return
        sensor_id = int(parts[4])
        data_type = parts[5] if len(parts) > 5 else ''

        if data_type == 'rms':
            d = json.loads(payload.decode())
            self._broadcast_json({
                'type': 'test_rms',
                'dev_id': dev_id,
                'sensor_id': sensor_id,
                'axis': d.get('axis'),
                'rms_speed': d.get('rms_speed'),
                'rms_accel': d.get('rms_accel'),
                'crest_factor': d.get('crest_factor'),
                'alarm': d.get('alarm', 0),
                'frame_id': d.get('frame_id'),
                'frame_type': d.get('type'),
                'timestamp': timestamp
            })
        elif data_type in ('spectrum1', 'spectrum2'):
            spec_type = 2 if data_type == 'spectrum1' else 3
            if len(payload) >= 4:
                frame_id = struct.unpack('<I', payload[:4])[0]
                binary_msg = struct.pack('<iiii', dev_id, sensor_id, frame_id, spec_type) + payload[4:]
                self._broadcast_binary(binary_msg)

    def _handle_alarm_data(self, dev_id, parts, payload, timestamp):
        if len(parts) < 5:
            return
        sensor_id = int(parts[4])
        data_type = parts[5] if len(parts) > 5 else ''

        if data_type == 'rms':
            d = json.loads(payload.decode())
            alarm_event = AlarmEvent(
                dev_id=dev_id,
                sensor_id=sensor_id,
                axis=d.get('axis', 0),
                rms_speed=d['rms_speed'],
                rms_accel=d['rms_accel'],
                crest_factor=d['crest_factor'],
                frame_id=d.get('frame_id'),
                timestamp=timestamp,
                spectrum=None,
                waveform=None,
                cause=''
            )
            db = SessionLocal()
            db.add(alarm_event)
            db.commit()
            db.close()

            self._broadcast_json({
                'type': 'alarm_rms',
                'dev_id': dev_id,
                'sensor_id': sensor_id,
                'axis': d.get('axis', 0),
                'rms_speed': d['rms_speed'],
                'rms_accel': d['rms_accel'],
                'crest_factor': d['crest_factor'],
                'alarm': d.get('alarm', 0),
                'frame_id': d.get('frame_id'),
                'timestamp': timestamp
            })

        elif data_type == 'spectrum':
            if len(payload) >= 4:
                frame_id = struct.unpack('<I', payload[:4])[0]
                spectrum = np.frombuffer(payload[4:], dtype=np.float32)
                db = SessionLocal()
                alarm = db.query(AlarmEvent).filter(
                    AlarmEvent.dev_id == dev_id,
                    AlarmEvent.sensor_id == sensor_id,
                    AlarmEvent.frame_id == frame_id
                ).first()
                if alarm:
                    alarm.spectrum = json.dumps(spectrum.tolist())
                    db.commit()
                db.close()
                binary_msg = struct.pack('<iii', dev_id, sensor_id, frame_id) + payload[4:]
                self._broadcast_binary(binary_msg)

        elif data_type == 'waveform':
            if len(payload) >= 4:
                frame_id = struct.unpack('<I', payload[:4])[0]
                waveform = np.frombuffer(payload[4:], dtype=np.float32)
                db = SessionLocal()
                alarm = db.query(AlarmEvent).filter(
                    AlarmEvent.dev_id == dev_id,
                    AlarmEvent.sensor_id == sensor_id,
                    AlarmEvent.frame_id == frame_id
                ).first()
                if alarm:
                    alarm.waveform = json.dumps(waveform.tolist())
                    db.commit()
                db.close()
                binary_msg = struct.pack('<iii', dev_id, sensor_id, frame_id) + payload[4:]
                self._broadcast_binary(binary_msg)

    # ---------- Рассылка сообщений WebSocket-клиентам ----------
    def _broadcast_json(self, data: dict):
        if not self.loop:
            return
        text = json.dumps(data)
        print(f"[BROADCAST] to {len(self.websocket_clients)} clients: {data}")
        for ws in list(self.websocket_clients):
            try:
                asyncio.run_coroutine_threadsafe(ws.send_text(text), self.loop)
            except Exception as e:
                logger.error(f"Error sending to websocket: {e}")
                self.websocket_clients.discard(ws)

    def _broadcast_binary(self, data: bytes):
        if not self.loop:
            return
        for ws in list(self.websocket_clients):
            try:
                asyncio.run_coroutine_threadsafe(ws.send_bytes(data), self.loop)
            except Exception as e:
                logger.error(f"Error sending binary to websocket: {e}")
                self.websocket_clients.discard(ws)

    # ---------- Управление WebSocket-соединениями ----------
    def register_websocket(self, ws):
        self.websocket_clients.add(ws)
        print(f"[WEBSOCKET] New client connected. Known devices: {self.device_statuses}")
        if self.loop:
            for dev_id, status in self.device_statuses.items():
                text = json.dumps({
                    'type': 'device_status',
                    'dev_id': dev_id,
                    'status': status,
                    'timestamp': time.time()
                })
                try:
                    asyncio.run_coroutine_threadsafe(ws.send_text(text), self.loop)
                except Exception as e:
                    logger.error(f"Error sending initial status: {e}")

    def unregister_websocket(self, ws):
        self.websocket_clients.discard(ws)

    def publish_command(self, dev_id: int, cmd: str):
        self.client.publish(f"devices/{dev_id}/cmd", cmd + ' ')
        logger.info(f"Command '{cmd}' sent to device {dev_id}")