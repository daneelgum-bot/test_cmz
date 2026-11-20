#ESP32 публикует данные в топики:


Устройство публикует телеметрию вибрации (RMS, спектр, осциллограмма) с двух датчиков по трём осям и принимает команды управления. Весь обмен идёт в namespace `devices/{DEVICE_ID}/`, где `DEVICE_ID` задаётся в `param.h`.


- BUF_SIZE = 2048 — размер буфера осциллограммы (float32)
- Спектр = 1024 значения (BUF_SIZE / 2)
- SENSOR_ID = 1 или 2 — идентификатор датчика
- AXIS: 0 = модуль, 1 = X, 2 = Y, 3 = Z


## Дерево топиков

```
devices/{DEVICE_ID}/
|
+-- status                                    (online / offline, LWT)
+-- cycle_status                              (cycle_on / cycle_off)
+-- cmd                                       (входящие команды, подписка)
|
+-- sensors/{SENSOR_ID}/
|   +-- rms                                   -> JSON
|   +-- spectrum                              -> binary
|   +-- waveform                              -> binary
|
+-- test/sensors/{SENSOR_ID}/
|   +-- rms                                   -> JSON
|   +-- spectrum1                             -> binary
|   +-- spectrum2                             -> binary
|
+-- alarm/sensors/{SENSOR_ID}/
    +-- rms                                   -> JSON
    +-- spectrum                              -> binary
    +-- waveform                              -> binary
```

---


Служебные:
status — "online" (qos=1, retain), LWT "offline" (qos=1, retain)
cycle_status — "cycle_on" / "cycle_off" (qos=1, retain)

все бинарники публикуюстя как [ frame_id : uint32 LE (4 байта) ][ float32 LE × N ]
где N = 1024 для spectrum (BUF_SIZE/2) и 2048 для waveform (BUF_SIZE).


### sensors/{SENSOR_ID}/rms — JSON

    {
      "rms_speed":    2.514,
      "rms_accel":    0.873,
      "crest_factor": 3.14,
      "alarm":        0,
      "frame_id":     12345,
      "axis":         1
    }

Поля:

- rms_speed (float) — СКЗ виброскорости, мм/с
- rms_accel (float) — СКЗ виброускорения, м/с²
- crest_factor (float) — пик-фактор
- alarm (int) — 0/1, флаг аварии
- frame_id (uint32) — сквозной счётчик кадров
- axis (int) — 0..3


## Входящие команды

Подписка: devices/{DEVICE_ID}/cmd, QoS 0.

    Команда              | Аргумент   | Действие
    ---------------------|------------|-----------------------------------------
    STREAM               | —          | Полный поток: RMS + spectrum + waveform
    STREAM_RMS           | —          | Только RMS
    STOP                 | —          | Остановить публикацию
    TEST                 | —          | Запустить тест
    ALARM_STREAM ON      | —          | Включить аварийную публикацию
    ALARM_STREAM OFF     | —          | Выключить аварийную публикацию
    RMS_ALARM            | float      | Порог СКЗ виброскорости
    CREST_ALARM          | float      | Порог пик-фактора
    FIRST_TEST           | int (с)    | Длительность первого спектра
    SECOND_TEST          | int (с)    | Длительность второго спектра
    DUR_TEST             | int (с)    | Общая длительность теста
    AXIS                 | sid ax     | Ось датчика (sid ∈ {1,2}, ax ∈ {0..3})
