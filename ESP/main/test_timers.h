#pragma once
#include <stdint.h>
#include <stdbool.h>

extern volatile uint8_t g_packet_type[2];
extern volatile bool g_test_active;

void start_test_timers(void);

