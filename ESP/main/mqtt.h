#pragma once
#include "esp_err.h"
#include "mqtt_client.h"

void mqtt_app_start(void);
esp_mqtt_client_handle_t mqtt_app_get_client(void);
void mqtt_send_task(void *pvParameters);

