// mic_toggle v4 -- GPIO15 (MIC_PWR_EN) BLINKS 1s ON / 1s OFF so the operator
// can locate the net on the top layer with a multimeter: any pad that toggles
// 0V <-> 3.3V once a second belongs to MIC_PWR_EN.  PDM stats keep printing.

#include <Arduino.h>
#include <driver/i2s.h>

namespace {

constexpr int kMicPwrEn = 15;
constexpr int kClockPin = 42;
constexpr int kDataPin = 41;
constexpr int kLedPin = 21;
constexpr int kSampleRate = 16000;
constexpr int kChunkSamples = 1600;
constexpr uint32_t kOnMs = 1000;
constexpr uint32_t kOffMs = 1000;

void installI2S() {
  i2s_config_t config = {};
  config.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_RX | I2S_MODE_PDM);
  config.sample_rate = kSampleRate;
  config.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  config.channel_format = I2S_CHANNEL_FMT_ONLY_RIGHT;
  config.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  config.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  config.dma_buf_count = 8;
  config.dma_buf_len = 256;
  config.use_apll = false;
  config.tx_desc_auto_clear = false;
  config.fixed_mclk = 0;
  if (i2s_driver_install(I2S_NUM_0, &config, 0, nullptr) != ESP_OK) {
    Serial.println("i2s_driver_install FAILED");
    return;
  }
  i2s_pin_config_t pins = {};
  pins.bck_io_num = I2S_PIN_NO_CHANGE;
  pins.ws_io_num = kClockPin;
  pins.data_out_num = I2S_PIN_NO_CHANGE;
  pins.data_in_num = kDataPin;
  if (i2s_set_pin(I2S_NUM_0, &pins) != ESP_OK) {
    Serial.println("i2s_set_pin FAILED");
    i2s_driver_uninstall(I2S_NUM_0);
    return;
  }
  i2s_set_pdm_rx_down_sample(I2S_NUM_0, I2S_PDM_DSR_8S);
  i2s_start(I2S_NUM_0);
}

// Rough activity probe on a raw GPIO (works even while I2S owns the pad mux:
// the input path always reflects the real pad voltage).
void rawToggleProbe(int gpio, int ms) {
  const uint32_t t0 = millis();
  int last = digitalRead(gpio);
  int transitions = 0;
  long highs = 0, n = 0;
  while (millis() - t0 < (uint32_t)ms) {
    const int v = digitalRead(gpio);
    if (v != last) { transitions++; last = v; }
    highs += v;
    n++;
  }
  Serial.printf("    raw GPIO%d: %ld reads, %d transitions, %4.1f%% high\n",
                gpio, n, transitions, n ? 100.0 * highs / n : 0.0);
}

}  // namespace

void sampleStats();
void reportStats();

void setup() {
  Serial.begin(115200);
  pinMode(kLedPin, OUTPUT);
  pinMode(kMicPwrEn, OUTPUT);
  digitalWrite(kMicPwrEn, HIGH);
  delay(300);
  installI2S();
  delay(500);
  Serial.println();
  Serial.println("mic_toggle v4: GPIO15 (MIC_PWR_EN) BLINKS 1s ON / 1s OFF");
  Serial.println("probe top-layer pads: pad toggling 0V<->3.3V at 1 Hz = MIC_PWR_EN net");
}

void loop() {
  static uint32_t round = 0;
  ++round;

  // Phase 1: GPIO15 HIGH for kOnMs, sample PDM.
  digitalWrite(kMicPwrEn, HIGH);
  digitalWrite(kLedPin, HIGH);
  const uint32_t onStart = millis();
  while (millis() - onStart < kOnMs) {
    sampleStats();
  }
  const int readback = digitalRead(kMicPwrEn);
  reportStats();
  Serial.printf("[%lu] ON phase done, GPIO15 readback=%d\n", (unsigned long)round, readback);

  // Phase 2: GPIO15 LOW for kOffMs (net falls to R11 pulldown level).
  digitalWrite(kMicPwrEn, LOW);
  digitalWrite(kLedPin, LOW);
  delay(kOffMs);
}

// Accumulates one chunk of PDM samples into the rolling stats.
static int16_t chunk[kChunkSamples];
static int mn = INT16_MAX, mx = INT16_MIN;
static int64_t sum = 0, sum_sq = 0;
static int changes = 0, n = 0;
static int buckets[16] = {0};

void sampleStats() {
  size_t got = 0;
  if (i2s_read(I2S_NUM_0, chunk, sizeof(chunk), &got, pdMS_TO_TICKS(50)) != ESP_OK ||
      got == 0) {
    return;
  }
  const int c = got / 2;
  static int16_t prev = 0;
  static bool first = true;
  for (int i = 0; i < c; ++i) {
    const int16_t v = chunk[i];
    if (first) { prev = v; first = false; }
    else if (v != prev) { changes++; }
    prev = v;
    if (v < mn) mn = v;
    if (v > mx) mx = v;
    sum += v;
    sum_sq += (int64_t)v * v;
    n++;
    const int b = (v + 32768) / 4096;
    if (b >= 0 && b < 16) buckets[b]++;
  }
}

void reportStats() {
  float rms = 0.f;
  if (n > 0) {
    const float mean = (float)(sum / n);
    rms = sqrtf((float)((double)sum_sq / n - (double)mean * mean));
  }
  int used_buckets = 0;
  for (int i = 0; i < 16; ++i) if (buckets[i] > 0) used_buckets++;

  Serial.printf("    PCM n=%d min=%d max=%d rms=%.1f changes=%d buckets=%d",
                n, mn, mx, rms, changes, used_buckets);
  if (n == 0) {
    Serial.println("  -> I2S read failed");
  } else if (used_buckets >= 8 && rms > 30.0f && changes > 500) {
    Serial.println("  -> *** ALIVE ***");
  } else if (used_buckets <= 2) {
    Serial.println("  -> constant (mic unpowered or data line dead)");
  } else {
    Serial.println("  -> weak/ambiguous");
  }
  // reset rolling stats for next round
  mn = INT16_MAX; mx = INT16_MIN;
  sum = 0; sum_sq = 0; changes = 0; n = 0;
  memset(buckets, 0, sizeof(buckets));
}
