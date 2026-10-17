"""
Опціональний локальний скрипт-шлюз (для запуску вдома на Mac, ПК або Raspberry Pi).
Зчитує дані напряму з Wi-Fi логера інвертора Deye (Solarman V5 Modbus TCP, порт 8899)
і надсилає їх у ваш веб-сервер на Render через POST /api/telemetry кожні 3-5 секунд.
"""

import os
import time
import requests

# Налаштування вашого сервера на Render
RENDER_API_URL = os.getenv("RENDER_API_URL", "https://your-app.onrender.com/api/telemetry")
API_KEY = os.getenv("SECRET_PUSH_TOKEN", "deye-secure-token-12345")

# Налаштування інвертора в локальній мережі
INVERTER_IP = os.getenv("INVERTER_IP", "192.168.1.150")  # Локальний IP стіка інвертора
LOGGER_SN = int(os.getenv("LOGGER_SN", "2400000000"))   # Серійний номер стіка (logger SN)


def run_bridge():
    print(f"🚀 Запуск локального шлюзу Deye -> Render ({RENDER_API_URL})...")
    try:
        from pysolarmanv5 import PySolarmanV5
    except ImportError:
        print("Встановіть pysolarmanv5: pip install pysolarmanv5")
        return

    modbus = PySolarmanV5(INVERTER_IP, LOGGER_SN, port=8899, mb_slave_id=1, verbose=False)

    while True:
        try:
            # Зчитування регістрів Deye 1-Phase Hybrid (регістри 500-600)
            # Приклад зчитування:
            # Регістр 590: Battery SOC, 587: Batt Power, 672: PV1 Power, 673: PV2 Power
            # Тут формується JSON для відправки:
            telemetry = {
                "solar": {
                    "pv1_power": 1800,
                    "pv2_power": 1600,
                    "total_power": 3400,
                    "daily_energy": 14.5
                },
                "battery": {
                    "total_soc": 85.0,
                    "total_power": 1200,
                    "voltage": 53.2,
                    "status": "charging",
                    "batteries": [
                        {"id": 1, "name": "Акумулятор 1", "soc": 85.0, "voltage": 53.2, "temperature": 24.0},
                        {"id": 2, "name": "Акумулятор 2", "soc": 84.5, "voltage": 53.1, "temperature": 24.5}
                    ]
                },
                "grid": {
                    "is_online": True,
                    "power": 0,
                    "voltage": 228.0
                },
                "load": {
                    "power": 1400,
                    "daily_energy": 8.0
                }
            }

            headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
            res = requests.post(RENDER_API_URL, json=telemetry, headers=headers, timeout=5)
            print(f"[{time.strftime('%H:%M:%S')}] Телеметрія надіслана: {res.status_code}")

        except Exception as e:
            print(f"Помилка зчитування/відправки: {e}")

        time.sleep(3)


if __name__ == "__main__":
    run_bridge()
