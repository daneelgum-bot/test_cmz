"""
Точка входа для запуска сервера. main.py
"""

import uvicorn
from .api import app
from .config import API_HOST, API_PORT, LOG_LEVEL

if __name__ == "__main__":
    uvicorn.run(
        app,
        host=API_HOST,
        port=API_PORT,
        log_level=LOG_LEVEL.lower()
    )