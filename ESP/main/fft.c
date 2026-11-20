#include "esp_dsp.h"
#include "param.h"
#include "buffers.h"
#include <math.h>
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "test_timers.h"
#include "esp_timer.h"

static const char *TAG = "FFT";

#define HP_CUTOFF 10.0f              // частота среза ФВЧ (Гц)
#define HP_Q1 0.5412f                // добротность первого каскада
#define HP_Q2 1.3066f                // добротность второго каскада
#define SAMPLING_RATE (1 / 0.000313) // частота дискретизации
//#define SAMPLING_RATE 10000
static float window[BUF_SIZE];
static float hp_coeffs1[5];
static float hp_coeffs2[5];
static float hp_state[2][4] = {{0}}; // состояния для каждого датчика (0 1 - первый фильтр 2 3 - второй )
static float *fft_input = NULL;

static half_buffer_t *prev_buf[2] = {NULL, NULL};
static bool first_frame[2] = {true, true};

void fft_task(void *pvParameters)
{
    // Инициализируем библиотеку
    esp_err_t ret = dsps_fft2r_init_fc32(NULL, BUF_SIZE);
    if (ret != ESP_OK)
    {
        ESP_LOGE(TAG, "FFT init failed: %d", ret);
        return;
    }

    dsps_wind_hann_f32(window, BUF_SIZE);

    fft_input = heap_caps_malloc(BUF_SIZE * 2*sizeof(float), MALLOC_CAP_8BIT);
    assert(fft_input);

    adxl345_buffer_t *buf = NULL;
    half_buffer_t *half_buf = NULL;
    float norm_freq = HP_CUTOFF / (SAMPLING_RATE / 2.0f);

    dsps_biquad_gen_hpf_f32(hp_coeffs1, norm_freq, HP_Q1);
    dsps_biquad_gen_hpf_f32(hp_coeffs2, norm_freq, HP_Q2);

    while (1)
    {
        if (xQueueReceive(fft_queue, &half_buf, portMAX_DELAY) != pdTRUE)
        {
            ESP_LOGE(TAG, "FIRST!!!!!");
            continue;
        }
       //int64_t t0 = esp_timer_get_time();

        uint8_t sensor_id = half_buf->sensor_id - 1;

        dsps_biquad_f32(half_buf->accel, half_buf->accel, BUF_SIZE / 2,
                        hp_coeffs1, &hp_state[sensor_id][0]);
        dsps_biquad_f32(half_buf->accel, half_buf->accel, BUF_SIZE / 2,
                        hp_coeffs2, &hp_state[sensor_id][2]);

        if (first_frame[sensor_id])
        {
            prev_buf[sensor_id] = half_buf; // предыдущий для перекрытия 50%
            first_frame[sensor_id] = false;
            continue;
        }
/*
        if (prev_buf[sensor_id] == NULL)
        {
            ESP_LOGW(TAG, "prev_buf is NULL, resetting first_frame");
            first_frame[sensor_id] = true;
            prev_buf[sensor_id] = half_buf;
            continue;
        }

        ESP_LOGI(TAG, "CHECK: prev=%p axis=%d, half=%p axis=%d",
                 prev_buf[sensor_id],
                 prev_buf[sensor_id] ? prev_buf[sensor_id]->axis : -1,
                 half_buf,
                 half_buf->axis);*/

        if (prev_buf[sensor_id]->axis != half_buf->axis)
        {
            ESP_LOGW(TAG, "Axis changed for sensor %d, resetting overlap", half_buf->sensor_id);
            // Возвращаем старый буфер в свободные, текущий становится первым
            xQueueSend(half_queue, &prev_buf[sensor_id], portMAX_DELAY);
            prev_buf[sensor_id] = half_buf;
            first_frame[sensor_id] = false;
            hp_state[sensor_id][0] = 0.0f;
            hp_state[sensor_id][1] = 0.0f;
            hp_state[sensor_id][2] = 0.0f;
            hp_state[sensor_id][3] = 0.0f; // начинаем с текущего как первого
            continue;
        }

        if (xQueueReceive(free_queue, &buf, portMAX_DELAY) != pdTRUE)
        {
            ESP_LOGE(TAG, "ERROR 228");
            xQueueSend(half_queue, &prev_buf[sensor_id], 0);
            xQueueSend(half_queue, &half_buf, portMAX_DELAY);
            // сбрасываем состояние, чтобы не было рассинхронизации
            first_frame[sensor_id] = true;
            prev_buf[sensor_id] = NULL;

            continue;
        }
        buf->axis = half_buf->axis;
        buf->sensor_id = half_buf->sensor_id;
        buf->TYPE = g_packet_type[buf->sensor_id - 1];
        for (int i = 0; i < BUF_SIZE / 2; i++)
        {
            buf->accel[i] = prev_buf[sensor_id]->accel[i];
            fft_input[i * 2] = buf->accel[i] * window[i];
            fft_input[i * 2 + 1] = 0.0f; // МНИМАЯ

            buf->accel[i + BUF_SIZE / 2] = half_buf->accel[i];
            fft_input[(BUF_SIZE / 2 + i) * 2] = half_buf->accel[i] * window[BUF_SIZE / 2 + i];
            fft_input[(BUF_SIZE / 2 + i) * 2 + 1] = 0.0f;
        }
        dsps_fft2r_fc32(fft_input, BUF_SIZE);
        dsps_bit_rev_fc32(fft_input, BUF_SIZE);

        float rms_speed_sq = 0.0f;
        for (int i = 1; i < BUF_SIZE / 2; i++)
        {
            float freq = i * SAMPLING_RATE / BUF_SIZE;
            float real = fft_input[i * 2];
            float imag = fft_input[i * 2 + 1];
            float amp = (2.0f / BUF_SIZE) * sqrtf(real * real + imag * imag) * 2; // АМПЛИТУДА УСКОРЕНИЯ В М/С^2 И НОРМИРОВКА
            buf->spectrum[i] = amp / (2.0f * M_PI * freq) * 1000;                 // АМПЛ ВИБРОСКОРОСТИ В ММ/C

            rms_speed_sq += buf->spectrum[i] * buf->spectrum[i];
        }
        buf->rms_speed = sqrtf(rms_speed_sq * 0.5f); // СКЗ ВИБРОСКОРОСТИ
        float sum_sq = 0.0f;
        float peak = 0.0f;
        for (int i = 0; i < BUF_SIZE; i++)
        {
            if (fabs(buf->accel[i]) > peak)
                peak = fabs(buf->accel[i]);
            sum_sq += buf->accel[i] * buf->accel[i];
        }
        buf->rms_accel = sqrtf(sum_sq / BUF_SIZE);
        buf->crest_factor = peak / buf->rms_accel;

        if ((g_packet_type[buf->sensor_id - 1] == 0x02 || g_packet_type[buf->sensor_id - 1] == 0x03) && (buf->TYPE==0x02 || buf->TYPE==0x03))
        {
            g_packet_type[buf->sensor_id - 1] = 0x01;
        }
        else if ((g_packet_type[buf->sensor_id - 1] == 0x04)&& buf->TYPE==0x04)
            g_packet_type[buf->sensor_id - 1] = 0x00;

        if ((buf->rms_speed > RMS_SPEED_ALARM || buf->crest_factor > CREST_FACTOR_ALARM)&&buf->rms_accel > MIN_RMS)
        {
            buf->ALARM = 1;
            /*ESP_LOGI(TAG, "ALARM! Sensor %d: rms_speed=%.3f mm/s, rms_accel=%.3f m/s², crest_factor=%.3f, PEAK=%.3f m/s^2, AXIS=%d",
                     buf->sensor_id, buf->rms_speed, buf->rms_accel, buf->crest_factor, peak, buf->axis);*/
        }
        else
            buf->ALARM = 0;

        /*int64_t dt = esp_timer_get_time() - t0;
        if (dt > 160000)
        { // 160 000 мкс = 160 мс
            ESP_LOGW(TAG, "SLOW FRAME: %lld us, AXIS: %d", dt, buf->axis);
        }
        else
            ESP_LOGI(TAG, "fast FRAME: %lld us, ASIX: %d", dt, buf->axis);
*/
        if (xQueueSend(send_queue, &buf, pdMS_TO_TICKS(160)) != pdTRUE)
        {
            ESP_LOGE(TAG, "ERROR SEND QUEUE FULL");
            // Возвращаем ВСЕ занятые буферы, включая текущую половину
            xQueueSend(free_queue, &buf, 0);                 // выходной целый буфер
            xQueueSend(half_queue, &prev_buf[sensor_id], 0); // предыдущая половина
            xQueueSend(half_queue, &half_buf, 0);            // текущая половина (добавить!)
            first_frame[sensor_id] = true;
            prev_buf[sensor_id] = NULL;
            continue;
        }
        xQueueSend(half_queue, &prev_buf[sensor_id], portMAX_DELAY);
        prev_buf[sensor_id] = half_buf;
        // ... вся обработка (склейка, БПФ, расчёт RMS, отправка) ...

        /* if (xQueueSend(free_queue, &buf, 0) != pdTRUE)
         {
             ESP_LOGE(TAG, "ERROR SEND QUEUE FULL");
             first_frame[sensor_id] = true;
             prev_buf[sensor_id] = NULL;
             xQueueSend(free_queue, &buf, 0);
             continue;
         }*/

        // ESP_LOGI(TAG, "OTRABOTAL URA!!! Sensor %d: rms_speed=%.3f mm/s, rms_accel=%.3f m/s², crest_factor=%.3f, PEAK=%.3f m/s^2",
        // buf->sensor_id, buf->rms_speed, buf->rms_accel, buf->crest_factor,peak);
    }
}