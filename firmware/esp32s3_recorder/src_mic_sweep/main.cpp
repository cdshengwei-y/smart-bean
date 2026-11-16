// MIC_PWR_EN safe probe v2.
//
// LESSON from v1: CN1 pin numbers do NOT map 1:1 to XIAO GPIO numbers -- the
// B2B connectors mate by physical position.  Driving a candidate GPIO HIGH
// blindly can short a VIN/GND pad and brown out the board (observed).
//
// v2 never blind-drives.  For each candidate pin:
//   1. INPUT (no pulls) read  -> HIGH means the pad is tied to a rail or a
//      driven output (VIN/3V3/GND-side driven).  SKIP, report "rail".
//   2. INPUT_PULLUP read      -> LOW means the pad lands on GND.  SKIP.
//   3. Otherwise the pad is a floating/high-Z input: the MIC_PWR_EN net has
//      R10 100k pulldown on the expansion side, so a floating candidate reads
//      LOW with no pulls and HIGH with pull-up.  Only then drive it HIGH
//      push-pull and sample the microphone.
//
// Mic sampling uses the exact I2S config of the recorder firmware.

#include <Arduino.h>
#include <driver/i2s.h>

#include <cmath>

namespace {

constexpr int kClockPin = 42;
constexpr int kDataPin = 41;
constexpr int kSampleRate = 16000;
constexpr int kCandidates[] = {18, 17, 16, 15, 14, 13, 12, 11, 10};
constexpr int kCandidateCount = sizeof(kCandidates) / sizeof(kCandidates[0]);
constexpr size_t kWarmupChunks = 4;
constexpr size_t kChunkSamples = 800;
constexpr int kMeasureChunks = 20;

struct MicStats {
  int min_value = INT16_MAX;
  int max_value = INT16_MIN;
  int64_t sum = 0;
  int64_t sum_sq = 0;
  int changes = 0;
  int buckets[64] = {0};
  int samples = 0;
  int16_t previous = 0;
};

bool sampleOnce(i2s_channel_fmt_t fmt, MicStats &stats) {
  i2s_config_t config = {};
  config.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_RX | I2S_MODE_PDM);
  config.sample_rate = kSampleRate;
  config.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  config.channel_format = fmt;
  config.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  config.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  config.dma_buf_count = 8;
  config.dma_buf_len = 256;
  config.use_apll = false;
  config.tx_desc_auto_clear = false;
  config.fixed_mclk = 0;
  if (i2s_driver_install(I2S_NUM_0, &config, 0, nullptr) != ESP_OK) {
    Serial.println("  i2s_driver_install FAILED");
    return false;
  }
  i2s_pin_config_t pins = {};
  pins.bck_io_num = I2S_PIN_NO_CHANGE;
  pins.ws_io_num = kClockPin;
  pins.data_out_num = I2S_PIN_NO_CHANGE;
  pins.data_in_num = kDataPin;
  if (i2s_set_pin(I2S_NUM_0, &pins) != ESP_OK) {
    Serial.println("  i2s_set_pin FAILED");
    i2s_driver_uninstall(I2S_NUM_0);
    return false;
  }
  i2s_set_pdm_rx_down_sample(I2S_NUM_0, I2S_PDM_DSR_8S);
  i2s_start(I2S_NUM_0);

  static int16_t chunk[kChunkSamples];
  size_t got = 0;
  bool first = true;
  for (int c = 0; c < kWarmupChunks + kMeasureChunks; ++c) {
    if (i2s_read(I2S_NUM_0, chunk, sizeof(chunk), &got, pdMS_TO_TICKS(1000)) != ESP_OK ||
        got == 0) {
      continue;
    }
    if (c < (int)kWarmupChunks) continue;
    const int n = got / 2;
    for (int i = 0; i < n; ++i) {
      const int16_t v = chunk[i];
      if (first) {
        stats.previous = v;
        first = false;
      } else if (v != stats.previous) {
        stats.changes++;
      }
      stats.previous = v;
      stats.samples++;
      stats.sum += v;
      stats.sum_sq += (int64_t)v * v;
      if (v < stats.min_value) stats.min_value = v;
      if (v > stats.max_value) stats.max_value = v;
      const int b = (v / 512) + 32;
      if (b >= 0 && b < 64) stats.buckets[b]++;
    }
  }
  i2s_stop(I2S_NUM_0);
  i2s_driver_uninstall(I2S_NUM_0);
  return stats.samples > 0;
}

void report(const char *label, const MicStats &s) {
  const float mean = s.samples ? (float)s.sum / s.samples : 0.f;
  const float rms = s.samples
      ? sqrtf((float)((double)s.sum_sq / s.samples - (double)mean * mean))
      : 0.f;
  int used_buckets = 0;
  for (int i = 0; i < 64; ++i) {
    if (s.buckets[i] > 0) used_buckets++;
  }
  Serial.printf("    %-11s min=%6d max=%6d rms=%8.1f changes=%6d buckets=%2d\n",
                label, s.min_value, s.max_value, rms, s.changes, used_buckets);
  if (s.samples == 0) {
    Serial.println("    -> no data (I2S read failed)");
  } else if (used_buckets >= 8 && rms > 30.0f && s.changes > 500) {
    Serial.println("    -> *** ALIVE *** microphone is producing real signal");
  } else if (used_buckets <= 2) {
    Serial.println("    -> constant (mic still unpowered)");
  } else {
    Serial.println("    -> weak/ambiguous");
  }
}

void probePin(int gpio) {
  // Step 1: passive read, no pulls.  HIGH => rail/driven pad, never touch.
  pinMode(gpio, INPUT);
  delay(5);
  const int passive = digitalRead(gpio);
  if (passive == HIGH) {
    Serial.printf("=== GPIO%d ===  rail/driven (reads HIGH, no pulls) -> SKIP\n", gpio);
    return;
  }
  // Step 2: pull-up read.  LOW => pad lands on GND, never drive it.
  pinMode(gpio, INPUT_PULLUP);
  delay(5);
  const int pulled = digitalRead(gpio);
  pinMode(gpio, INPUT);
  if (pulled == LOW) {
    Serial.printf("=== GPIO%d ===  GND pad (pull-up reads LOW) -> SKIP\n", gpio);
    return;
  }
  // Step 3: floating/high-Z input -> safe to drive.  MIC_PWR_EN has R10 100k
  // pulldown, so it must read LOW passively and HIGH with pull-up.
  Serial.printf("=== GPIO%d ===  floating/high-Z -> drive HIGH and sample\n", gpio);
  pinMode(gpio, OUTPUT);
  digitalWrite(gpio, HIGH);
  delay(400);

  MicStats left;
  if (sampleOnce(I2S_CHANNEL_FMT_ONLY_LEFT, left)) {
    report("LEFT slot", left);
  }
  MicStats right;
  if (sampleOnce(I2S_CHANNEL_FMT_ONLY_RIGHT, right)) {
    report("RIGHT slot", right);
  }

  digitalWrite(gpio, LOW);
  pinMode(gpio, INPUT);
  delay(200);
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(1200);
  Serial.println();
  Serial.println("MIC_PWR_EN safe probe v2");
  Serial.println("Passive classify first (rail / GND / floating), drive only floating pins.");
}

void loop() {
  static int round = 0;
  ++round;
  Serial.printf("\n########## ROUND %d ##########\n", round);
  for (int i = 0; i < kCandidateCount; ++i) {
    probePin(kCandidates[i]);
  }
  Serial.println("ROUND DONE, next in 3 s");
  delay(3000);
}
