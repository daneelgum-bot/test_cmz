#pragma once
#include <stdint.h>

#define MIN_RMS 0.215f

#define BUF_SIZE 2048
#define WS_URI "ws://192.168.0.131:8765"

#define MQTT_BROKER_URI "mqtt://192.168.0.131:1883"

#define DEVICE_ID 1
#define NUM_BUFFERS 6

#define SPI2_MISO   12
#define SPI2_MOSI   13
#define SPI2_SCLK   14
#define SPI2_CS1    32

#define SPI3_MISO   5
#define SPI3_MOSI   16
#define SPI3_SCLK   4
#define SPI3_CS2    15

#define G_TO_MS2 9.8193f 



volatile extern float RMS_SPEED_ALARM;
volatile extern float CREST_FACTOR_ALARM;
volatile extern uint16_t FIRST_FRAME_SEC;
volatile extern uint16_t SECOND_FRAME_SEC;
volatile extern uint16_t DURATION;