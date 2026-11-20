#include "test_timers.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "param.h"

static const char *TAG = "TEST_CTRL";

volatile uint8_t g_packet_type[2] = {0x00, 0x00};
volatile bool g_test_active = false;

static esp_timer_handle_t timer_3s_handle = NULL;
static esp_timer_handle_t timer_5s_handle = NULL;
static esp_timer_handle_t test_stop_handle = NULL;

static void timer_3s_cb(void *arg)
{
    g_packet_type[0] = 0x02;
    g_packet_type[1] = 0x02;
    if (timer_3s_handle)
    {
        esp_timer_stop(timer_3s_handle);
        esp_timer_delete(timer_3s_handle);
        timer_3s_handle = NULL;
    }
}

static void timer_5s_cb(void *arg)
{
    g_packet_type[0] = 0x03;
    g_packet_type[1] = 0x03;
    if (timer_5s_handle)
    {
        esp_timer_stop(timer_5s_handle);
        esp_timer_delete(timer_5s_handle);
        timer_5s_handle = NULL;
    }
}

static void test_stop_cb(void *arg)
{
    g_packet_type[0] = 0x04;
    g_packet_type[1] = 0x04;
    g_test_active = false;
    if (test_stop_handle)
    {
        esp_timer_stop(test_stop_handle);
        esp_timer_delete(test_stop_handle);
        test_stop_handle = NULL;
    }
}

void start_test_timers(void)
{
    if (g_test_active)
    {
        ESP_LOGW(TAG, "Test already active, ignoring new command");
        return;
    }

    g_test_active = true;

    esp_timer_create_args_t args = {.callback = timer_3s_cb};
    esp_timer_create(&args, &timer_3s_handle);
    esp_timer_start_once(timer_3s_handle, FIRST_FRAME_SEC*1000000);

    args.callback = timer_5s_cb;
    esp_timer_create(&args, &timer_5s_handle);
    esp_timer_start_once(timer_5s_handle, SECOND_FRAME_SEC*1000000);

    args.callback = test_stop_cb;
    esp_timer_create(&args, &test_stop_handle);
    esp_timer_start_once(test_stop_handle, DURATION*1000000);

    ESP_LOGI(TAG, "Test timers started");
}