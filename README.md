ESP32 публикует данные в топики:
devices/{DEVICE_ID}/sensors/{SENSOR_ID}/rms -- JSON строка (rms_speed, rns_accel, crest_factor, alarm, frame_id, axis)
devices/{DEVICE_ID}/sensors/{SENSOR_ID}/spectrum -- двоичный bin
devices/{DEVICE_ID}/sensors/{SENSOR_ID}/waveform -- двоичный bin

alarm/sensors/{sid}/rms -- JSON
alarm/sensors/{sid}/spectrum -- binary
alarm/sensors/{sid}/waveform -- binary

Служебные:
status — "online" (qos=1, retain), LWT "offline" (qos=1, retain)
cycle_status — "cycle_on" / "cycle_off" (qos=1, retain)

все бинарники публикуюстя как [ frame_id : uint32 LE (4 байта) ][ float32 LE × N ]
где N = 1024 для spectrum (BUF_SIZE/2) и 2048 для waveform (BUF_SIZE).
