"""
Конфигурация сервера. config.py
"""

# --- MQTT ---
MQTT_BROKER = "localhost"       # IP-адрес или hostname MQTT-брокера
MQTT_PORT = 1883                # Порт MQTT (по умолчанию 1883)

# --- База данных ---
# SQLite: файл vibration.db будет создан в текущей рабочей директории
DATABASE_URL = "sqlite:///vibration.db"

# --- HTTP/WebSocket API ---
API_HOST = "0.0.0.0"            # Слушать все сетевые интерфейсы
API_PORT = 8000                 # Порт для REST API и WebSocket

# --- Прочие настройки ---
LOG_LEVEL = "INFO"              # Уровень логирования