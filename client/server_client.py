# ============ client/server_client.py ============

import json
import struct
import time
import threading
import requests
import websocket
import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal

BUF_SIZE = 2048


class ServerClient(QObject):
    # Сигналы live-данных
    rms_received = pyqtSignal(int, int, int, float, float, float, int, float)
    spectrum_received = pyqtSignal(int, int, int, object)
    waveform_received = pyqtSignal(int, int, int, object)
    device_status = pyqtSignal(int, str)
    cycle_status_received = pyqtSignal(int, str, float)
    params_received = pyqtSignal(int, dict)
    test_rms_received = pyqtSignal(int, int, int, float, float, float, int, int, int)
    test_spectrum_received = pyqtSignal(int, int, object, int, int)
    alarm_rms_received = pyqtSignal(int, int, int, float, float, float, int, int, float)
    alarm_spectrum_received = pyqtSignal(int, int, object, int)
    alarm_waveform_received = pyqtSignal(int, int, object, int)
    status_updated = pyqtSignal(str)

    # Сигналы асинхронных REST-запросов
    history_rms_loaded = pyqtSignal(int, int, int, list)
    tests_loaded = pyqtSignal(list)
    alarms_loaded = pyqtSignal(list)
    alarm_loaded = pyqtSignal(dict)
    cycle_events_loaded = pyqtSignal(int, list)
    current_cycle_start_loaded = pyqtSignal(int, object)

    def __init__(self, server_url="ws://localhost:8000/ws", api_url="http://localhost:8000"):
        super().__init__()
        self.server_url = server_url
        self.api_url = api_url
        self.ws = None
        self.running = False
        self.thread = None
        self.sensor_axis = {}
        self._history_loading = False
        self._current_cycle_loading = False
        self._cycle_events_loading = False

    def connect(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run_forever, daemon=True)
        self.thread.start()
        self.status_updated.emit(f"Подключение к серверу {self.server_url}...")

    def disconnect(self):
        self.running = False
        if self.ws:
            self.ws.close()
        if self.thread:
            self.thread.join(timeout=2)
        self.status_updated.emit("Отключено от сервера")

    def _run_forever(self):
        while self.running:
            try:
                self.ws = websocket.WebSocketApp(
                    self.server_url,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                    on_open=self._on_open
                )
                self.ws.run_forever()
            except Exception as e:
                self.status_updated.emit(f"Ошибка WebSocket: {e}")
            if self.running:
                time.sleep(2)

    def _on_open(self, ws):
        self.status_updated.emit("WebSocket подключён")

    def _on_error(self, ws, error):
        self.status_updated.emit(f"Ошибка WebSocket: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        if self.running:
            self.status_updated.emit("Соединение потеряно, переподключение...")

    def _on_message(self, ws, message):
        try:
            if isinstance(message, bytes):
                self._handle_binary(message)
            else:
                data = json.loads(message)
                self._handle_json(data)
        except Exception as e:
            print(f"Ошибка обработки сообщения: {e}")

    def _handle_json(self, data: dict):
        msg_type = data.get('type')
        if msg_type == 'rms':
            self.rms_received.emit(
                data['dev_id'], data['sensor_id'], data['axis'],
                data['rms_speed'], data['rms_accel'], data['crest_factor'],
                data['alarm'], data['timestamp']
            )
        elif msg_type == 'device_status':
            self.device_status.emit(data['dev_id'], data['status'])
        elif msg_type == 'cycle_status':
            self.cycle_status_received.emit(data['dev_id'], data['status'], data['timestamp'])
        elif msg_type == 'params':
            self.params_received.emit(data['dev_id'], data['params'])
        elif msg_type == 'test_rms':
            self.test_rms_received.emit(
                data['dev_id'], data['sensor_id'], data['axis'],
                data['rms_speed'], data['rms_accel'], data['crest_factor'],
                data.get('alarm', 0), data.get('frame_id', 0), data.get('frame_type', 0)
            )
        elif msg_type == 'alarm_rms':
            self.alarm_rms_received.emit(
                data['dev_id'], data['sensor_id'], data['axis'],
                data['rms_speed'], data['rms_accel'], data['crest_factor'],
                data.get('alarm', 0), data.get('frame_id', 0), data['timestamp']
            )

    def _handle_binary(self, data: bytes):
        if len(data) < 12:
            return
        dev_id, sensor_id, param1 = struct.unpack('<iii', data[:12])
        payload = data[12:]
        spec_len = 4 + (BUF_SIZE // 2) * 4
        wave_len = 4 + BUF_SIZE * 4
        if len(payload) == spec_len:
            frame_id = struct.unpack('<I', payload[:4])[0]
            spectrum = np.frombuffer(payload[4:], dtype=np.float32)
            if 0 <= param1 <= 3:
                self.spectrum_received.emit(dev_id, sensor_id, param1, spectrum)
            else:
                self.alarm_spectrum_received.emit(dev_id, sensor_id, spectrum, param1)
        elif len(payload) == wave_len:
            frame_id = struct.unpack('<I', payload[:4])[0]
            waveform = np.frombuffer(payload[4:], dtype=np.float32)
            if 0 <= param1 <= 3:
                self.waveform_received.emit(dev_id, sensor_id, param1, waveform)
            else:
                self.alarm_waveform_received.emit(dev_id, sensor_id, waveform, param1)
        else:
            if len(payload) == 4 + spec_len:
                frame_id, spec_type = struct.unpack('<ii', payload[:8])
                spectrum = np.frombuffer(payload[8:], dtype=np.float32)
                self.test_spectrum_received.emit(dev_id, sensor_id, spectrum, frame_id, spec_type)

    # ---------- Синхронные REST-методы ----------
    def get_history_rms(self, dev_id, sensor_id, axis, start_ts, end_ts):
        try:
            resp = requests.get(
                f"{self.api_url}/history/rms",
                params={
                    'dev_id': dev_id,
                    'sensor_id': sensor_id,
                    'axis': axis,
                    'start_ts': start_ts,
                    'end_ts': end_ts
                },
                timeout=10
            )
            if resp.status_code == 200:
                return resp.json()
            return []
        except Exception as e:
            self.status_updated.emit(f"Ошибка получения истории: {e}")
            return []

    def get_cycle_events(self, dev_id, start_ts, end_ts):
        try:
            resp = requests.get(
                f"{self.api_url}/cycle_events",
                params={'dev_id': dev_id, 'start_ts': start_ts, 'end_ts': end_ts},
                timeout=10
            )
            if resp.status_code == 200:
                return resp.json()
            return []
        except Exception as e:
            self.status_updated.emit(f"Ошибка получения циклов: {e}")
            return []

    def get_current_cycle_start(self, dev_id):
        try:
            resp = requests.get(
                f"{self.api_url}/current_cycle_start",
                params={'dev_id': dev_id},
                timeout=10
            )
            if resp.status_code == 200:
                return resp.json().get('start_ts')
            return None
        except Exception as e:
            self.status_updated.emit(f"Ошибка получения текущего цикла: {e}")
            return None

    def get_tests_list(self):
        try:
            resp = requests.get(f"{self.api_url}/tests", timeout=10)
            if resp.status_code == 200:
                return resp.json()
            return []
        except Exception as e:
            self.status_updated.emit(f"Ошибка получения списка тестов: {e}")
            return []

    def get_test(self, test_id):
        try:
            resp = requests.get(f"{self.api_url}/tests/{test_id}", timeout=10)
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception as e:
            self.status_updated.emit(f"Ошибка получения теста: {e}")
            return None

    def save_test(self, test_data):
        try:
            resp = requests.post(f"{self.api_url}/tests", json=test_data, timeout=10)
            if resp.status_code == 200:
                self.status_updated.emit("Тест сохранён")
                return resp.json().get('id')
            return None
        except Exception as e:
            self.status_updated.emit(f"Ошибка сохранения теста: {e}")
            return None

    def delete_test(self, test_id):
        try:
            resp = requests.delete(f"{self.api_url}/tests/{test_id}", timeout=10)
            if resp.status_code == 200:
                self.status_updated.emit("Тест удалён")
                return True
            return False
        except Exception as e:
            self.status_updated.emit(f"Ошибка удаления теста: {e}")
            return False

    def set_reference_test(self, test_id):
        try:
            resp = requests.post(f"{self.api_url}/tests/{test_id}/set_reference", timeout=10)
            if resp.status_code == 200:
                self.status_updated.emit("Эталонный тест установлен")
                return True
            return False
        except Exception as e:
            self.status_updated.emit(f"Ошибка установки эталона: {e}")
            return False

    def get_alarms(self, dev_id=None, sensor_id=None, start_ts=None, end_ts=None):
        params = {}
        if dev_id is not None:
            params['dev_id'] = dev_id
        if sensor_id is not None:
            params['sensor_id'] = sensor_id
        if start_ts is not None:
            params['start_ts'] = start_ts
        if end_ts is not None:
            params['end_ts'] = end_ts
        try:
            resp = requests.get(f"{self.api_url}/alarms", params=params, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            return []
        except Exception as e:
            self.status_updated.emit(f"Ошибка получения аварий: {e}")
            return []

    def get_alarm_by_id(self, alarm_id):
        try:
            resp = requests.get(f"{self.api_url}/alarms/{alarm_id}", timeout=10)
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception as e:
            self.status_updated.emit(f"Ошибка получения аварии: {e}")
            return None

    def delete_alarm(self, alarm_id):
        try:
            resp = requests.delete(f"{self.api_url}/alarms/{alarm_id}", timeout=10)
            if resp.status_code == 200:
                self.status_updated.emit("Авария удалена")
                return True
            return False
        except Exception as e:
            self.status_updated.emit(f"Ошибка удаления аварии: {e}")
            return False

    def delete_all_alarms(self):
        """Удалить все аварии на сервере."""
        try:
            resp = requests.delete(f"{self.api_url}/alarms/all", timeout=30)
            if resp.status_code == 200:
                self.status_updated.emit("Все аварии удалены")
                return True
            return False
        except Exception as e:
            self.status_updated.emit(f"Ошибка удаления всех аварий: {e}")
            return False

    def clear_history(self, action="all"):
        try:
            resp = requests.delete(f"{self.api_url}/history/clear", params={"action": action}, timeout=30)
            if resp.status_code == 200:
                self.status_updated.emit(resp.json().get('message', 'История очищена'))
                return True
            return False
        except Exception as e:
            self.status_updated.emit(f"Ошибка очистки: {e}")
            return False

    def import_history(self, file_path):
        try:
            with open(file_path, 'rb') as f:
                files = {'file': f}
                resp = requests.post(f"{self.api_url}/history/import", files=files, timeout=60)
            if resp.status_code == 200:
                self.status_updated.emit(resp.json().get('message', 'Импорт выполнен'))
                return True
            return False
        except Exception as e:
            self.status_updated.emit(f"Ошибка импорта: {e}")
            return False

    # ---------- Асинхронные обёртки ----------
    def get_history_rms_async(self, dev_id, sensor_id, axis, start_ts, end_ts):
        threading.Thread(target=self._get_history_rms_worker,
                         args=(dev_id, sensor_id, axis, start_ts, end_ts), daemon=True).start()

    def _get_history_rms_worker(self, dev_id, sensor_id, axis, start_ts, end_ts):
        data = self.get_history_rms(dev_id, sensor_id, axis, start_ts, end_ts)
        self.history_rms_loaded.emit(dev_id, sensor_id, axis, data)

    def get_cycle_events_async(self, dev_id, start_ts, end_ts):
        threading.Thread(target=self._get_cycle_events_worker,
                         args=(dev_id, start_ts, end_ts), daemon=True).start()

    def _get_cycle_events_worker(self, dev_id, start_ts, end_ts):
        events = self.get_cycle_events(dev_id, start_ts, end_ts)
        self.cycle_events_loaded.emit(dev_id, events)

    def get_current_cycle_start_async(self, dev_id):
        threading.Thread(target=self._get_current_cycle_start_worker,
                         args=(dev_id,), daemon=True).start()

    def _get_current_cycle_start_worker(self, dev_id):
        start_ts = self.get_current_cycle_start(dev_id)
        self.current_cycle_start_loaded.emit(dev_id, start_ts)

    def get_tests_list_async(self):
        threading.Thread(target=self._get_tests_list_worker, daemon=True).start()

    def _get_tests_list_worker(self):
        tests = self.get_tests_list()
        self.tests_loaded.emit(tests)

    def get_alarms_async(self, dev_id=None, sensor_id=None, start_ts=None, end_ts=None):
        threading.Thread(target=self._get_alarms_worker,
                         args=(dev_id, sensor_id, start_ts, end_ts), daemon=True).start()

    def _get_alarms_worker(self, dev_id, sensor_id, start_ts, end_ts):
        alarms = self.get_alarms(dev_id, sensor_id, start_ts, end_ts)
        self.alarms_loaded.emit(alarms)

    def get_alarm_by_id_async(self, alarm_id):
        threading.Thread(target=self._get_alarm_by_id_worker, args=(alarm_id,), daemon=True).start()

    def _get_alarm_by_id_worker(self, alarm_id):
        alarm = self.get_alarm_by_id(alarm_id)
        if alarm:
            self.alarm_loaded.emit(alarm)

    # ---------- Отправка команд ----------
    def send_command(self, dev_id, cmd):
        if self.ws and self.ws.sock and self.ws.sock.connected:
            message = json.dumps({'type': 'command', 'dev_id': dev_id, 'cmd': cmd})
            self.ws.send(message)
            self.status_updated.emit(f"Команда '{cmd}' отправлена")
        else:
            self.status_updated.emit("Нет подключения к серверу")