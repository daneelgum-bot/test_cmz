#include "mqtt.h"
#include "esp_log.h"
#include "mqtt_client.h"
#include "cJSON.h"
#include "param.h"       // DEVICE_ID, MQTT_BROKER_URI
#include "buffers.h"     // adxl345_buffer_t, BUF_SIZE, send_queue, free_queue
#include "test_timers.h" // g_test_active, g_packet_type, start_test_timers()
#include <stdio.h>
#include <string.h>

static const char *TAG = "mqtt_app";
static esp_mqtt_client_handle_t mqtt_client = NULL;
static bool mqtt_connected = false;
static uint32_t g_frame_id = 0;
static bool g_alarm_stream_enabled = true; // По умолчанию аварийная отправка включена

/* Внешние переменные */
extern volatile uint8_t axis[2];
extern portMUX_TYPE axis_spinlock;
extern volatile float RMS_SPEED_ALARM;
extern volatile float CREST_FACTOR_ALARM;
extern volatile uint16_t FIRST_FRAME_SEC;
extern volatile uint16_t SECOND_FRAME_SEC;
extern volatile uint16_t DURATION;
extern volatile bool g_test_active;
extern volatile bool g_cycle_active;
extern volatile uint8_t g_packet_type[2];

static bool last_cycle_state = false; 

/* Прототип обработчика команд */
void handle_command(const char *data, size_t len);

/* ─── Публикация RMS (stream) ──────────────────────────────── */
static void publish_rms(esp_mqtt_client_handle_t client, adxl345_buffer_t *buf, uint32_t frame_id)
{
    char topic[64];
    snprintf(topic, sizeof(topic), "devices/%d/sensors/%d/rms", DEVICE_ID, buf->sensor_id);

    cJSON *root = cJSON_CreateObject();
    cJSON_AddNumberToObject(root, "rms_speed", buf->rms_speed);
    cJSON_AddNumberToObject(root, "rms_accel", buf->rms_accel);
    cJSON_AddNumberToObject(root, "crest_factor", buf->crest_factor);
    cJSON_AddNumberToObject(root, "alarm", buf->ALARM);
    cJSON_AddNumberToObject(root, "frame_id", frame_id);
    cJSON_AddNumberToObject(root, "axis", buf->axis);

    char *json_str = cJSON_PrintUnformatted(root);
    if (json_str)
    {
        esp_mqtt_client_publish(client, topic, json_str, 0, 1, 0);
        free(json_str);
    }
    cJSON_Delete(root);
}

/* ─── Публикация RMS (test) ────────────────────────────────── */
static void publish_rms_test(esp_mqtt_client_handle_t client, adxl345_buffer_t *buf, uint32_t frame_id)
{
    char topic[80];
    snprintf(topic, sizeof(topic), "devices/%d/test/sensors/%d/rms", DEVICE_ID, buf->sensor_id);

    cJSON *root = cJSON_CreateObject();
    cJSON_AddNumberToObject(root, "rms_speed", buf->rms_speed);
    cJSON_AddNumberToObject(root, "rms_accel", buf->rms_accel);
    cJSON_AddNumberToObject(root, "crest_factor", buf->crest_factor);
    cJSON_AddNumberToObject(root, "alarm", buf->ALARM);
    cJSON_AddNumberToObject(root, "frame_id", frame_id);
    cJSON_AddNumberToObject(root, "axis", buf->axis);
    cJSON_AddNumberToObject(root, "type", buf->TYPE);

    char *json_str = cJSON_PrintUnformatted(root);
    if (json_str)
    {
        esp_mqtt_client_publish(client, topic, json_str, 0, 1, 0);
        free(json_str);
    }
    cJSON_Delete(root);
}

/* ─── Публикация спектра (stream/test) ──────────────────────── */
static void publish_spectrum(esp_mqtt_client_handle_t client, adxl345_buffer_t *buf, uint32_t frame_id)
{
    char topic[64];
    snprintf(topic, sizeof(topic), "devices/%d/sensors/%d/spectrum", DEVICE_ID, buf->sensor_id);

    size_t data_len = BUF_SIZE / 2 * sizeof(float);
    size_t header_len = sizeof(uint32_t);
    size_t total_len = header_len + data_len;
    uint8_t *packet = malloc(total_len);
    if (!packet)
        return;

    memcpy(packet, &frame_id, sizeof(frame_id));
    memcpy(packet + header_len, buf->spectrum, data_len);
    esp_mqtt_client_publish(client, topic, (const char *)packet, total_len, 0, 0);
    free(packet);
}

static void publish_test_spectrum(esp_mqtt_client_handle_t client,
                                  adxl345_buffer_t *buf,
                                  uint32_t frame_id,
                                  const char *subtopic) // "spectrum1" или "spectrum2"
{
    char topic[80];
    snprintf(topic, sizeof(topic), "devices/%d/test/sensors/%d/%s",
             DEVICE_ID, buf->sensor_id, subtopic);

    size_t data_len = BUF_SIZE / 2 * sizeof(float);
    size_t header_len = sizeof(uint32_t);
    size_t total_len = header_len + data_len;
    uint8_t *packet = malloc(total_len);
    if (!packet)
        return;

    memcpy(packet, &frame_id, sizeof(frame_id));
    memcpy(packet + header_len, buf->spectrum, data_len);
    esp_mqtt_client_publish(client, topic, (const char *)packet, total_len, 0, 0);
    free(packet);
}

/* ─── Публикация осциллограммы (stream) ─────────────────────── */
static void publish_waveform(esp_mqtt_client_handle_t client, adxl345_buffer_t *buf, uint32_t frame_id)
{
    char topic[64];
    snprintf(topic, sizeof(topic), "devices/%d/sensors/%d/waveform", DEVICE_ID, buf->sensor_id);

    size_t data_len = BUF_SIZE * sizeof(float);
    size_t header_len = sizeof(uint32_t);
    size_t total_len = header_len + data_len;
    uint8_t *packet = malloc(total_len);
    if (!packet)
        return;

    memcpy(packet, &frame_id, sizeof(frame_id));
    memcpy(packet + header_len, buf->accel, data_len);
    esp_mqtt_client_publish(client, topic, (const char *)packet, total_len, 0, 0);
    free(packet);
}

/* ─── Публикация аварийных данных ────────────────────────────── */
static void publish_alarm_rms(esp_mqtt_client_handle_t client, adxl345_buffer_t *buf, uint32_t frame_id)
{
    char topic[80];
    snprintf(topic, sizeof(topic), "devices/%d/alarm/sensors/%d/rms", DEVICE_ID, buf->sensor_id);
    cJSON *root = cJSON_CreateObject();
    cJSON_AddNumberToObject(root, "rms_speed", buf->rms_speed);
    cJSON_AddNumberToObject(root, "rms_accel", buf->rms_accel);
    cJSON_AddNumberToObject(root, "crest_factor", buf->crest_factor);
    cJSON_AddNumberToObject(root, "alarm", buf->ALARM);
    cJSON_AddNumberToObject(root, "frame_id", frame_id);
    cJSON_AddNumberToObject(root, "axis", buf->axis);
    char *json_str = cJSON_PrintUnformatted(root);
    if (json_str)
    {
        esp_mqtt_client_publish(client, topic, json_str, 0, 1, 0);
        free(json_str);
    }
    cJSON_Delete(root);
}

static void publish_alarm_spectrum(esp_mqtt_client_handle_t client, adxl345_buffer_t *buf, uint32_t frame_id)
{
    char topic[80];
    snprintf(topic, sizeof(topic), "devices/%d/alarm/sensors/%d/spectrum", DEVICE_ID, buf->sensor_id);
    size_t data_len = BUF_SIZE / 2 * sizeof(float);
    size_t header_len = sizeof(uint32_t);
    size_t total_len = header_len + data_len;
    uint8_t *packet = malloc(total_len);
    if (!packet)
        return;
    memcpy(packet, &frame_id, sizeof(frame_id));
    memcpy(packet + header_len, buf->spectrum, data_len);
    esp_mqtt_client_publish(client, topic, (const char *)packet, total_len, 0, 0);
    free(packet);
}

static void publish_alarm_waveform(esp_mqtt_client_handle_t client, adxl345_buffer_t *buf, uint32_t frame_id)
{
    char topic[80];
    snprintf(topic, sizeof(topic), "devices/%d/alarm/sensors/%d/waveform", DEVICE_ID, buf->sensor_id);
    size_t data_len = BUF_SIZE * sizeof(float);
    size_t header_len = sizeof(uint32_t);
    size_t total_len = header_len + data_len;
    uint8_t *packet = malloc(total_len);
    if (!packet)
        return;
    memcpy(packet, &frame_id, sizeof(frame_id));
    memcpy(packet + header_len, buf->accel, data_len);
    esp_mqtt_client_publish(client, topic, (const char *)packet, total_len, 0, 0);
    free(packet);
}

/* ─── Обработчик команд ──────────────────────────────────────── */
void handle_command(const char *data, size_t len)
{
    ESP_LOGI(TAG, "Command: %.*s", (int)len, data);
    if (strncmp(data, "TEST", 4) == 0)
    {
        if (!g_test_active)
        {
            g_packet_type[0] = 0x01;
            g_packet_type[1] = 0x01;
            start_test_timers();
            ESP_LOGI(TAG, "Test mode activated");
        }
        else
        {
            ESP_LOGW(TAG, "Test already running, ignoring");
        }
    }
    else if (strncmp(data, "STREAM_RMS", 10) == 0)
    {
        if (!g_test_active)
        {
            g_packet_type[0] = 0x06;
            g_packet_type[1] = 0x06;
            ESP_LOGI(TAG, "RMS Stream mode activated");
        }
        else
        {
            ESP_LOGW(TAG, "Test running, ignoring rms stream");
        }
    }
    else if (strncmp(data, "STREAM", 6) == 0)
    {
        if (!g_test_active)
        {
            g_packet_type[0] = 0x05;
            g_packet_type[1] = 0x05;
            ESP_LOGI(TAG, "Stream mode activated");
        }
        else
        {
            ESP_LOGW(TAG, "Test running, ignoring stream");
        }
    }
    else if (strncmp(data, "STOP", 4) == 0)
    {
        g_packet_type[0] = 0x00;
        g_packet_type[1] = 0x00;
        ESP_LOGI(TAG, "Stream mode stopped");
    }
    else if (strncmp(data, "RMS_ALARM ", 10) == 0)
    {
        float val;
        if (sscanf(data, "RMS_ALARM %f", &val) == 1)
        {
            RMS_SPEED_ALARM = val;
            ESP_LOGI(TAG, "RMS_ALARM set to %.2f", val);
        }
    }
    else if (strncmp(data, "CREST_ALARM ", 12) == 0)
    {
        float val;
        if (sscanf(data, "CREST_ALARM %f", &val) == 1)
        {
            CREST_FACTOR_ALARM = val;
            ESP_LOGI(TAG, "CREST_ALARM set to %.2f", val);
        }
    }
    else if (strncmp(data, "FIRST_TEST ", 11) == 0)
    {
        int val;
        if (sscanf(data, "FIRST_TEST %d", &val) == 1)
        {
            FIRST_FRAME_SEC = (uint16_t)val;
            ESP_LOGI(TAG, "FIRST_TEST set to %d", val);
        }
    }
    else if (strncmp(data, "SECOND_TEST ", 12) == 0)
    {
        int val;
        if (sscanf(data, "SECOND_TEST %d", &val) == 1)
        {
            SECOND_FRAME_SEC = (uint16_t)val;
            ESP_LOGI(TAG, "SECOND_TEST set to %d", val);
        }
    }
    else if (strncmp(data, "DUR_TEST ", 9) == 0)
    {
        int val;
        if (sscanf(data, "DUR_TEST %d", &val) == 1)
        {
            DURATION = (uint16_t)val;
            ESP_LOGI(TAG, "DUR_TEST set to %d", val);
        }
    }
    else if (strncmp(data, "AXIS ", 5) == 0)
    {
        int sid, ax;
        if (sscanf(data, "AXIS %d %d", &sid, &ax) == 2)
        {
            if (sid >= 1 && sid <= 2 && ax >= 0 && ax <= 3)
            {
                portENTER_CRITICAL(&axis_spinlock);
                axis[sid - 1] = ax;
                portEXIT_CRITICAL(&axis_spinlock);
                ESP_LOGI(TAG, "Sensor %d axis set to %d", sid, ax);
            }
        }
    }
    else if (strncmp(data, "ALARM_STREAM ON", 15) == 0)
    {
        g_alarm_stream_enabled = true;
        ESP_LOGI(TAG, "Alarm stream enabled");
    }
    else if (strncmp(data, "ALARM_STREAM OFF", 16) == 0)
    {
        g_alarm_stream_enabled = false;
        ESP_LOGI(TAG, "Alarm stream disabled");
    }
}

static void publish_cycle_status(esp_mqtt_client_handle_t client, bool active)
{
    char topic[64];
    snprintf(topic, sizeof(topic), "devices/%d/cycle_status", DEVICE_ID);
    const char *msg = active ? "cycle_on" : "cycle_off";
    esp_mqtt_client_publish(client, topic, msg, 0, 1, 1); // qos=1, retain=1
    ESP_LOGI(TAG, "Cycle status published: %s", msg);
}

/* ─── Обработчик событий MQTT ────────────────────────────────── */
static void mqtt_event_handler(void *handler_args, esp_event_base_t base,
                               int32_t event_id, void *event_data)
{
    esp_mqtt_event_handle_t event = event_data;
    switch ((esp_mqtt_event_id_t)event_id)
    {
    case MQTT_EVENT_CONNECTED:
        ESP_LOGI(TAG, "MQTT connected");
        mqtt_connected = true;
        {
            char cmd_topic[64];
            snprintf(cmd_topic, sizeof(cmd_topic), "devices/%d/cmd", DEVICE_ID);
            esp_mqtt_client_subscribe(mqtt_client, cmd_topic, 0);

            char status_topic[64];
            snprintf(status_topic, sizeof(status_topic), "devices/%d/status", DEVICE_ID);
            esp_mqtt_client_publish(mqtt_client, status_topic, "online", 0, 1, 1);
            publish_cycle_status(mqtt_client, g_cycle_active);
            last_cycle_state = g_cycle_active;
        }
        break;
    case MQTT_EVENT_DISCONNECTED:
        mqtt_connected = false;
        ESP_LOGW(TAG, "MQTT disconnected");
        break;
    case MQTT_EVENT_DATA:
        if (event->data_len > 0)
        {
            ESP_LOGI(TAG, "Received on topic '%.*s': %.*s",
                     event->topic_len, event->topic,
                     event->data_len, event->data);
            handle_command(event->data, event->data_len);
        }
        break;
    case MQTT_EVENT_ERROR:
        ESP_LOGE(TAG, "MQTT error");
        break;
    default:
        break;
    }
}

/* ─── Инициализация MQTT ─────────────────────────────────────── */
void mqtt_app_start(void)
{
    static char lwt_topic[64];
    snprintf(lwt_topic, sizeof(lwt_topic), "devices/%d/status", DEVICE_ID);

    static char client_id[16];
    snprintf(client_id, sizeof(client_id), "%d", DEVICE_ID);

    esp_mqtt_client_config_t mqtt_cfg = {
        .broker.address.uri = MQTT_BROKER_URI,
        .credentials.client_id = client_id,
        .session.keepalive = 30,
        .network.disable_auto_reconnect = false,
        .session.last_will = {
            .topic = lwt_topic,
            .msg = "offline",
            .msg_len = 7,
            .qos = 1,
            .retain = 1},
    };

    mqtt_client = esp_mqtt_client_init(&mqtt_cfg);
    esp_mqtt_client_register_event(mqtt_client, ESP_EVENT_ANY_ID,
                                   mqtt_event_handler, NULL);
    esp_mqtt_client_start(mqtt_client);
}

esp_mqtt_client_handle_t mqtt_app_get_client(void)
{
    return mqtt_client;
}

/* ─── Задача отправки данных из очереди ──────────────────────── */
void mqtt_send_task(void *pvParameters)
{
    adxl345_buffer_t *buf = NULL;

    while (1)
    {
        if (xQueueReceive(send_queue, &buf, portMAX_DELAY) != pdTRUE)
            continue;

        esp_mqtt_client_handle_t client = mqtt_app_get_client();
        if (client == NULL || !mqtt_connected)
        {
            xQueueSend(free_queue, &buf, 0);
            continue;
        }

        if (g_cycle_active!=last_cycle_state) {
            last_cycle_state=g_cycle_active;
            publish_cycle_status(client, g_cycle_active);
        }

        uint32_t frame_id = g_frame_id++;

        switch (buf->TYPE)
        {
        case 0x01:
        case 0x04:
            publish_rms_test(client, buf, frame_id);
            break;
        case 0x02:
            publish_rms_test(client, buf, frame_id);
            publish_test_spectrum(client, buf, frame_id, "spectrum1");
            break;
        case 0x03:
            publish_rms_test(client, buf, frame_id);
            publish_test_spectrum(client, buf, frame_id, "spectrum2");
            break;
        case 0x05:
            // Полный поток: СКЗ + спектр + осциллограмма
            publish_rms(client, buf, frame_id);
            publish_spectrum(client, buf, frame_id);
            publish_waveform(client, buf, frame_id);
            break;
        case 0x06:
            // Только СКЗ: виброскорость, виброускорение, пик-фактор
            publish_rms(client, buf, frame_id);
            break;
        case 0x00:
            // STOP: обычная публикация не требуется
            break;
        default:
            break;
        }

        /* Аварийная публикация — В ЛЮБОМ РЕЖИМЕ,
           если ALARM установлен и включён ALARM_STREAM */
        if (g_alarm_stream_enabled && buf->ALARM)
        {
            publish_alarm_rms(client, buf, frame_id);
            publish_alarm_spectrum(client, buf, frame_id);
            publish_alarm_waveform(client, buf, frame_id);
        }

        xQueueSend(free_queue, &buf, 0);
    }
}