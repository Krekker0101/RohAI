#pragma once
#include <stdint.h>
// Bench-only 3.3 V logic. Output bit order N R/Y/G, S R/Y/G, E R/Y/G,
// W R/Y/G, pedestrian R/G. Independent electrical sense uses same order.
constexpr char DEVICE_ID[] = "rohai-maket-1";
constexpr char FIRMWARE_VERSION[] = "1.0.1";
constexpr int OUTPUT_DATA=23, OUTPUT_CLOCK=18, OUTPUT_LATCH=19, OUTPUT_ENABLE=25;
constexpr int SENSE_DATA=34, SENSE_CLOCK=22, SENSE_LOAD=21;
constexpr int ESTOP_PIN=27, LOCAL_REARM_PIN=26;
constexpr uint32_t MIN_GREEN_MS=8000, MAX_GREEN_MS=45000;
constexpr uint32_t YELLOW_MS=3000, ALL_RED_MS=2000;
constexpr uint32_t PED_GREEN_MS=10000, PED_CLEARANCE_MS=5000;
constexpr uint16_t ALL_RED_MASK=(1<<0)|(1<<3)|(1<<6)|(1<<9)|(1<<12);
