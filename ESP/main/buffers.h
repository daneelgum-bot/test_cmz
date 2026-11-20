#pragma once

#include "param.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include <stdint.h>
#include "adxl345.h"

typedef struct
{
    uint8_t sensor_id;
    uint8_t axis;
    uint8_t TYPE;
    uint8_t ALARM;
    float rms_speed;
    float rms_accel;
    float crest_factor;
    float spectrum[BUF_SIZE / 2];
    float accel[BUF_SIZE];
} adxl345_buffer_t;

typedef struct
{
    uint8_t sensor_id;
    uint8_t axis;
    float accel[BUF_SIZE / 2];
} half_buffer_t;

typedef struct
{
    adxl345_t *dev;
    uint8_t sensor_id;
    uint8_t axis;
} read_task_param_t;

extern QueueHandle_t free_queue;
extern QueueHandle_t fft_queue;
extern QueueHandle_t send_queue;
extern QueueHandle_t half_queue;

void buffers_init(void);
void adxl345_read_axes(void *pvParameters);
void websocket_send_task(void *pvParameters);