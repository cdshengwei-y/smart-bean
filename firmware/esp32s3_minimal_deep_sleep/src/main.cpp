#include <Arduino.h>
#include <esp_sleep.h>

// Electrical isolation diagnostic: enter deep sleep immediately after reset.
// No Wi-Fi, BLE, Serial, SD, microphone, or GPIO setup is performed here.
void setup() {
  // Keep USB available long enough for the native-USB serial connection
  // to enumerate when the board is powered through a hub.
  delay(30000);
  esp_deep_sleep_start();
}

void loop() {}
