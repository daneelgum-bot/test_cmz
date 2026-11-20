#include <stdio.h>
#include "param.h"
#include "buffers.h"
#include "esp_log.h"
#include "websocket.h"
#include "nvs_flash.h"
#include "ethernet.h"
#include "fft.h"
#include "driver/gpio.h"
// #include "generate.h"
#include "mqtt.h"

static const char *TAG = " MAIN";

static read_task_param_t param1;
static read_task_param_t param2;

#define CYCLE_PIN GPIO_NUM_34
volatile bool g_cycle_active = false;

int sensor_count = 0;

static void IRAM_ATTR cycle_isr_handler(void *arg)
{
    // При использовании подтяжки к GND (pull-down)
    g_cycle_active = (gpio_get_level(CYCLE_PIN) == 1);

}

void app_main(void)
{

    gpio_config_t io_conf = {
        .pin_bit_mask = (1ULL << CYCLE_PIN),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE, // Внутренние подтяжки не работают
        .intr_type = GPIO_INTR_ANYEDGE};
    gpio_config(&io_conf);
    gpio_install_isr_service(0);
    gpio_isr_handler_add(CYCLE_PIN, cycle_isr_handler, NULL);

    if (gpio_get_level(CYCLE_PIN) == 1)
    {
        ESP_LOGI(TAG, "Pin %d is already high, triggering event manually.", CYCLE_PIN);
        g_cycle_active = true;

    }

    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND)
    {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    buffers_init();

    ESP_ERROR_CHECK(adxl345_bus_init(SPI2_HOST, SPI2_MISO, SPI2_MOSI, SPI2_SCLK));
    ESP_ERROR_CHECK(adxl345_bus_init(SPI3_HOST, SPI3_MISO, SPI3_MOSI, SPI3_SCLK));

    static adxl345_t dev1, dev2;
    ESP_ERROR_CHECK(adxl345_init(&dev1, SPI2_HOST, SPI2_CS1));
    ESP_ERROR_CHECK(adxl345_init(&dev2, SPI3_HOST, SPI3_CS2));

    ethernet_init();
    vTaskDelay(pdMS_TO_TICKS(1000));
    // websocket_app_start();
    mqtt_app_start();
    if (adxl345_check_presence(&dev1))
    {
        sensor_count++;
        adxl345_force_4wire_spi(&dev1);
        adxl345_configure(&dev1);
        ESP_LOGI(TAG, "ADXL345 1 found!");
        param1.dev = &dev1;
        param1.sensor_id = 1;
        param1.axis = 1;
        xTaskCreatePinnedToCore(adxl345_read_axes, "read1", 4096, &param1, 5, NULL, 1);
    }
    if (adxl345_check_presence(&dev2))
    {
        sensor_count++;
        adxl345_force_4wire_spi(&dev2);
        adxl345_configure(&dev2);
        ESP_LOGI(TAG, "ADXL345 2 found!");
        param2.dev = &dev2;
        param2.sensor_id = 2;
        param2.axis = 1;
        xTaskCreatePinnedToCore(adxl345_read_axes, "read2", 4096, &param2, 5, NULL, 1);
    }
    if (sensor_count == 0)
    {
        ESP_LOGI(TAG, "both ADXL345 not found!");
        return;
    }
    xTaskCreatePinnedToCore(fft_task, "fft", 8192, NULL, 4, NULL, 0);
    xTaskCreatePinnedToCore(mqtt_send_task, "mqtt_send", 8192, NULL, 3, NULL, 0);
}
// ESP_LOGI(TAG, "POEHALO");
// xTaskCreatePinnedToCore(generate_task, "read1", 4096, &param1, 5, NULL, 1);