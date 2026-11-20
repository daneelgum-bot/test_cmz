#include "read_axes_adxl.h"
#include "adxl345.h"
#include <math.h>
#include <string.h>
#include "driver/spi_master.h"
#include "buffers.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "websocket.h"
#include "test_timers.h"

static const char *TAG = "READ_TASK";

extern volatile bool g_cycle_active;
volatile uint8_t axis[2] = {1, 1};
portMUX_TYPE axis_spinlock = portMUX_INITIALIZER_UNLOCKED;

static void IRAM_ATTR timer_callback(void *arg)
{
    TaskHandle_t task = (TaskHandle_t)arg;
    BaseType_t xHigherPriorityTaskWoken = pdFALSE;
    vTaskNotifyGiveFromISR(task, &xHigherPriorityTaskWoken);
    portYIELD_FROM_ISR(xHigherPriorityTaskWoken);
}

void adxl345_read_axes(void *pvParameters)
{
    ESP_LOGI(TAG, "PENIS");
    read_task_param_t *param = (read_task_param_t *)pvParameters;
    adxl345_t *dev = param->dev;

    esp_timer_handle_t timer;
    esp_timer_create_args_t timer_args = {
        .callback = &timer_callback,
        .arg = xTaskGetCurrentTaskHandle(),
        .name = (param->sensor_id == 1) ? "adxl_timer1" : "adxl_timer2"};
    esp_timer_create(&timer_args, &timer);
    esp_timer_start_periodic(timer, 313);

    uint8_t *rx_buffer = heap_caps_malloc(7, MALLOC_CAP_DMA);
    uint8_t *tx_buffer = heap_caps_malloc(7, MALLOC_CAP_DMA);
    assert(rx_buffer && tx_buffer);

    tx_buffer[0] = 0xC0 | 0x32;
    for (int i = 1; i < 7; i++)
        tx_buffer[i] = 0xFF;

    spi_transaction_t t = {
        .length = 8 * 7,
        .tx_buffer = tx_buffer,
        .rx_buffer = rx_buffer};

    half_buffer_t *half_buf = NULL;
    if (xQueueReceive(half_queue, &half_buf, portMAX_DELAY) != pdTRUE)
    {
        ESP_LOGE(TAG, "NO FREE HALF BUFFER");
        vTaskDelete(NULL);
    }

    int idx = 0;
    half_buf->sensor_id = param->sensor_id;
    portENTER_CRITICAL(&axis_spinlock); // синхронизация между ядрами
    half_buf->axis = axis[half_buf->sensor_id - 1];
    portEXIT_CRITICAL(&axis_spinlock);
    while (1)
    {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        if (!g_cycle_active && idx==0)
        {   
            continue; // Пропускаем измерение, если станок не в цикле
        }
        spi_device_transmit(dev->handle, &t);
        switch ((half_buf->axis))
        {
        case 1: // x
            half_buf->accel[idx++] = (int16_t)(rx_buffer[1] | (rx_buffer[2] << 8)) * 0.004f * G_TO_MS2;
            break;
        case 2: // y
            half_buf->accel[idx++] = (int16_t)(rx_buffer[3] | (rx_buffer[4] << 8)) * 0.004f * G_TO_MS2;
            break;
        case 3: // z
            half_buf->accel[idx++] = (int16_t)(rx_buffer[5] | (rx_buffer[6] << 8)) * 0.004f * G_TO_MS2;
            break;
        default:
            half_buf->accel[idx++] = sqrt(((int16_t)(rx_buffer[1] | (rx_buffer[2] << 8)) * 0.004f * G_TO_MS2) * ((int16_t)(rx_buffer[1] | (rx_buffer[2] << 8)) * 0.004f * G_TO_MS2) + ((int16_t)(rx_buffer[3] | (rx_buffer[4] << 8)) * 0.004f * G_TO_MS2) * ((int16_t)(rx_buffer[3] | (rx_buffer[4] << 8)) * 0.004f * G_TO_MS2) + ((int16_t)(rx_buffer[5] | (rx_buffer[6] << 8)) * 0.004f * G_TO_MS2) * ((int16_t)(rx_buffer[5] | (rx_buffer[6] << 8)) * 0.004f * G_TO_MS2));
        }

        if (idx >= BUF_SIZE / 2) // ОТПАРВЛЯТЬ ПО BUF_SIZE/2
        {
            if (xQueueSend(fft_queue, &half_buf, 0) != pdTRUE)
            {
                ESP_LOGE(TAG, "ERROR!!!!!! FFT_QUEUE FULL");
                xQueueSend(half_queue, &half_buf, 0); // возвращаем в свободные
            }
            if (xQueueReceive(half_queue, &half_buf, portMAX_DELAY) != pdTRUE)
            {
                ESP_LOGE(TAG, "FULL!!!!!!!!!231");
            }

            idx = 0;
            half_buf->sensor_id = param->sensor_id;
            half_buf->axis = axis[half_buf->sensor_id - 1];
            // ESP_LOGI(TAG, "Sensor %d new half axis: %d", half_buf->sensor_id, half_buf->axis);
        };
    }
}