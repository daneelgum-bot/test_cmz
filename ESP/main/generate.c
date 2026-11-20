#include <math.h>

#include "generate.h"
#include <math.h>
#include <string.h>
#include "buffers.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "websocket.h"
#include "test_timers.h"

static const char *TAG = "generate_TASK";

extern volatile bool g_cycle_active;
volatile uint8_t axis[2] = {1, 1};
portMUX_TYPE axis_spinlock = portMUX_INITIALIZER_UNLOCKED;
float SAMPLE_FREQ_GEN = 10000;
static void IRAM_ATTR timer_callback(void *arg)
{
    TaskHandle_t task = (TaskHandle_t)arg;
    BaseType_t xHigherPriorityTaskWoken = pdFALSE;
    vTaskNotifyGiveFromISR(task, &xHigherPriorityTaskWoken);
    portYIELD_FROM_ISR(xHigherPriorityTaskWoken);
}

void generate_task(void *pvParameters)
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
    esp_timer_start_periodic(timer, 1 / SAMPLE_FREQ_GEN * 1000000);

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
        if (!g_cycle_active)
        {
            continue; // Пропускаем измерение, если станок не в цикле
        }
        half_buf->accel[idx] = 1 * sinf(2 * M_PI * 50 / SAMPLE_FREQ_GEN*idx) + 4 * sinf(2 * M_PI * 3750 / SAMPLE_FREQ_GEN*idx)*sinf(2*M_PI*500/SAMPLE_FREQ_GEN*idx) + 1.5 * sinf(2 * M_PI * 1000 / SAMPLE_FREQ_GEN*idx);
        idx++;
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