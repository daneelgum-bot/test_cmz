#include "buffers.h"
#include "esp_log.h"
#include "driver/spi_master.h"
#include <math.h>
#include <string.h>
#include <stdatomic.h>

static const char *TAG = "BUFFERS";

QueueHandle_t free_queue = NULL;
QueueHandle_t fft_queue = NULL;
QueueHandle_t send_queue = NULL;
QueueHandle_t half_queue = NULL;
// static adxl345_buffer_t buffers[NUM_BUFFERS];

void buffers_init(void)
{
    free_queue = xQueueCreate(NUM_BUFFERS, sizeof(adxl345_buffer_t *));
    fft_queue = xQueueCreate(NUM_BUFFERS, sizeof(adxl345_buffer_t *));
    send_queue = xQueueCreate(NUM_BUFFERS, sizeof(adxl345_buffer_t *));
    half_queue = xQueueCreate(NUM_BUFFERS, sizeof(half_buffer_t *));
    assert(free_queue && fft_queue && send_queue && half_queue);

    for (int i = 0; i < NUM_BUFFERS; i++)
    {
        adxl345_buffer_t *buf = heap_caps_malloc(sizeof(adxl345_buffer_t), MALLOC_CAP_8BIT);
        half_buffer_t *half_buf = heap_caps_malloc(sizeof(half_buffer_t), MALLOC_CAP_8BIT);
        assert(buf && half_buf);
        xQueueSend(free_queue, &buf, 0);
        xQueueSend(half_queue, &half_buf, 0);
    }
    ESP_LOGI(TAG, "Buffers initialized (%d total)", NUM_BUFFERS);
}
