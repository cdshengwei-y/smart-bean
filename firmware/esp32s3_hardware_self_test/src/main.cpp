#include <Arduino.h>
#include <SD.h>
#include <SPI.h>
#include <driver/gpio.h>
#include <driver/i2s.h>
#include <esp_sleep.h>
#include <math.h>
#include <soc/gpio_reg.h>
#include <soc/soc.h>

namespace {

constexpr int kSdPowerEnPin = 40;
constexpr int kSdCsPin = 3;
constexpr int kSdSckPin = 7;
constexpr int kSdMisoPin = 8;
constexpr int kSdMosiPin = 9;
constexpr int kMicClockPin = 42;
constexpr int kMicDataPin = 41;
constexpr int kUserLedPin = 21;

constexpr uint32_t kSdPowerUpDelayMs = 10;
constexpr uint32_t kSdPowerOffDelayMs = 20;
constexpr uint32_t kSdSpiFrequencyHz = 20000000;
constexpr size_t kSdTestFileBytes = 64U * 1024U;
constexpr char kSdTestFilePath[] = "/hardware_self_test.bin";
constexpr uint32_t kMicSampleRate = 16000;
constexpr uint32_t kMicTestDurationMs = 3000;
constexpr uint32_t kMicBusProbeDurationMs = 200;
constexpr uint8_t kMicDiagnosticLedcChannel = 7;

SPIClass sdSpi(FSPI);
bool sdMounted = false;

struct TestSummary {
  uint8_t passed = 0;
  uint8_t failed = 0;
  uint8_t manual = 0;
};

void printResult(const char *name, bool passed, const char *detail = nullptr) {
  Serial.printf("[%s] %s", passed ? "PASS" : "FAIL", name);
  if (detail != nullptr && detail[0] != '\0') {
    Serial.printf(" - %s", detail);
  }
  Serial.println();
}

void printManual(const char *name, const char *detail) {
  Serial.printf("[MANUAL] %s - %s\n", name, detail);
}

void setSdBusHighImpedance() {
  pinMode(kSdCsPin, INPUT);
  pinMode(kSdSckPin, INPUT);
  pinMode(kSdMisoPin, INPUT);
  pinMode(kSdMosiPin, INPUT);
}

void unmountAndPowerOffSd() {
  // SD.begin() may initialize part of the driver even when mounting fails.
  SD.end();
  sdSpi.end();
  sdMounted = false;
  setSdBusHighImpedance();
  pinMode(kSdPowerEnPin, OUTPUT);
  digitalWrite(kSdPowerEnPin, LOW);
  delay(kSdPowerOffDelayMs);
}

void powerOnSdWithoutMounting() {
  unmountAndPowerOffSd();
  gpio_hold_dis(static_cast<gpio_num_t>(kSdPowerEnPin));
  pinMode(kSdPowerEnPin, OUTPUT);
  digitalWrite(kSdPowerEnPin, HIGH);
  delay(kSdPowerUpDelayMs);
}

bool mountSdOnce() {
  powerOnSdWithoutMounting();
  sdSpi.begin(kSdSckPin, kSdMisoPin, kSdMosiPin, kSdCsPin);
  sdMounted = SD.begin(kSdCsPin, sdSpi, kSdSpiFrequencyHz) && SD.cardType() != CARD_NONE;
  return sdMounted;
}

bool mountSdWithRetries(uint8_t attempts = 3) {
  for (uint8_t attempt = 1; attempt <= attempts; ++attempt) {
    if (mountSdOnce()) {
      Serial.printf("SD mount succeeded on attempt %u.\n", static_cast<unsigned>(attempt));
      return true;
    }
    Serial.printf("SD mount attempt %u failed.\n", static_cast<unsigned>(attempt));
    unmountAndPowerOffSd();
  }
  return false;
}

void fillTestPattern(uint8_t *buffer, size_t length, size_t offset) {
  for (size_t i = 0; i < length; ++i) {
    buffer[i] = static_cast<uint8_t>(((offset + i) * 31U + 7U) & 0xFFU);
  }
}

bool testSdReadWrite() {
  Serial.println("\n--- SD read/write test ---");
  if (!mountSdWithRetries()) {
    printResult("SD mount", false, "card not detected");
    return false;
  }

  Serial.printf("Card size: %.2f GB, used: %.2f MB\n",
                static_cast<double>(SD.cardSize()) / 1000000000.0,
                static_cast<double>(SD.usedBytes()) / 1000000.0);

  SD.remove(kSdTestFilePath);
  File testFile = SD.open(kSdTestFilePath, FILE_WRITE);
  if (!testFile) {
    printResult("SD test file open", false);
    unmountAndPowerOffSd();
    return false;
  }

  uint8_t expected[512];
  const uint32_t writeStartedAt = millis();
  size_t writtenTotal = 0;
  while (writtenTotal < kSdTestFileBytes) {
    const size_t chunk = min(sizeof(expected), kSdTestFileBytes - writtenTotal);
    fillTestPattern(expected, chunk, writtenTotal);
    const size_t written = testFile.write(expected, chunk);
    if (written != chunk) {
      testFile.close();
      SD.remove(kSdTestFilePath);
      printResult("SD write", false, "short write");
      unmountAndPowerOffSd();
      return false;
    }
    writtenTotal += written;
  }
  testFile.flush();
  testFile.close();
  const uint32_t writeElapsedMs = millis() - writeStartedAt;

  testFile = SD.open(kSdTestFilePath, FILE_READ);
  if (!testFile || testFile.size() != kSdTestFileBytes) {
    if (testFile) {
      testFile.close();
    }
    SD.remove(kSdTestFilePath);
    printResult("SD read", false, "file size mismatch");
    unmountAndPowerOffSd();
    return false;
  }

  uint8_t actual[512];
  const uint32_t readStartedAt = millis();
  size_t readTotal = 0;
  bool dataMatches = true;
  while (readTotal < kSdTestFileBytes && dataMatches) {
    const size_t chunk = min(sizeof(actual), kSdTestFileBytes - readTotal);
    const size_t read = testFile.read(actual, chunk);
    if (read != chunk) {
      dataMatches = false;
      break;
    }
    fillTestPattern(expected, chunk, readTotal);
    for (size_t i = 0; i < chunk; ++i) {
      if (actual[i] != expected[i]) {
        Serial.printf("Mismatch at byte %u: got %u, expected %u\n",
                      static_cast<unsigned>(readTotal + i),
                      static_cast<unsigned>(actual[i]),
                      static_cast<unsigned>(expected[i]));
        dataMatches = false;
        break;
      }
    }
    readTotal += read;
  }
  testFile.close();
  const uint32_t readElapsedMs = millis() - readStartedAt;
  const bool removed = SD.remove(kSdTestFilePath);

  Serial.printf("64 KiB write: %lu ms, read: %lu ms, cleanup: %s\n",
                static_cast<unsigned long>(writeElapsedMs),
                static_cast<unsigned long>(readElapsedMs),
                removed ? "ok" : "failed");
  printResult("SD data verification", dataMatches && removed);
  unmountAndPowerOffSd();
  return dataMatches && removed;
}

bool testSdPowerCycles(uint16_t cycles) {
  Serial.printf("\n--- SD power-cycle test: %u cycles ---\n", static_cast<unsigned>(cycles));
  uint16_t passed = 0;
  for (uint16_t cycle = 1; cycle <= cycles; ++cycle) {
    const bool mounted = mountSdOnce();
    const bool capacityValid = mounted && SD.cardSize() > 0;
    if (capacityValid) {
      ++passed;
    }
    Serial.printf("Cycle %u/%u: %s\n",
                  static_cast<unsigned>(cycle),
                  static_cast<unsigned>(cycles),
                  capacityValid ? "PASS" : "FAIL");
    unmountAndPowerOffSd();
  }
  char detail[48];
  snprintf(detail, sizeof(detail), "%u/%u mounts succeeded",
           static_cast<unsigned>(passed), static_cast<unsigned>(cycles));
  printResult("SD repeated power cycling", passed == cycles, detail);
  return passed == cycles;
}

bool installMicrophone() {
  gpio_hold_dis(static_cast<gpio_num_t>(kMicClockPin));
  gpio_reset_pin(static_cast<gpio_num_t>(kMicClockPin));
  const i2s_config_t config = {
      .mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_RX | I2S_MODE_PDM),
      .sample_rate = kMicSampleRate,
      .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
      // MIC SELECT is tied to GND on this board, so the T3902 drives the
      // right PDM time slot.
      .channel_format = I2S_CHANNEL_FMT_ONLY_RIGHT,
      .communication_format = I2S_COMM_FORMAT_STAND_I2S,
      .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
      .dma_buf_count = 8,
      .dma_buf_len = 256,
      .use_apll = false,
      .tx_desc_auto_clear = false,
      .fixed_mclk = 0,
  };
  const i2s_pin_config_t pins = {
      .bck_io_num = I2S_PIN_NO_CHANGE,
      .ws_io_num = kMicClockPin,
      .data_out_num = I2S_PIN_NO_CHANGE,
      .data_in_num = kMicDataPin,
  };

  if (i2s_driver_install(I2S_NUM_0, &config, 0, nullptr) != ESP_OK) {
    return false;
  }
  if (i2s_set_pin(I2S_NUM_0, &pins) != ESP_OK ||
      i2s_set_pdm_rx_down_sample(I2S_NUM_0, I2S_PDM_DSR_8S) != ESP_OK) {
    i2s_driver_uninstall(I2S_NUM_0);
    return false;
  }
  return true;
}

uint32_t samplePinTransitions(int pin, uint32_t reads = 1000000) {
  gpio_set_direction(static_cast<gpio_num_t>(pin), GPIO_MODE_INPUT_OUTPUT);
  const uint32_t mask = 1U << (pin - 32);
  uint32_t previous = REG_READ(GPIO_IN1_REG) & mask;
  uint32_t transitions = 0;
  for (uint32_t i = 0; i < reads; ++i) {
    const uint32_t level = REG_READ(GPIO_IN1_REG) & mask;
    transitions += level != previous;
    previous = level;
  }
  return transitions;
}

struct DiagnosticClockResult {
  uint32_t requestedHz = 0;
  uint32_t configuredHz = 0;
  uint32_t clockTransitions = 0;
  uint32_t dataTransitions = 0;
};

DiagnosticClockResult probeDataWithDiagnosticClock(uint32_t frequencyHz,
                                                   uint8_t dataPinMode = INPUT) {
  DiagnosticClockResult result;
  result.requestedHz = frequencyHz;
  pinMode(kMicDataPin, dataPinMode);
  gpio_hold_dis(static_cast<gpio_num_t>(kMicClockPin));
  gpio_reset_pin(static_cast<gpio_num_t>(kMicClockPin));
  result.configuredHz = ledcSetup(kMicDiagnosticLedcChannel, frequencyHz, 1);
  ledcAttachPin(kMicClockPin, kMicDiagnosticLedcChannel);
  gpio_set_direction(static_cast<gpio_num_t>(kMicClockPin), GPIO_MODE_INPUT_OUTPUT);
  ledcWrite(kMicDiagnosticLedcChannel, 1);
  delay(50);
  result.clockTransitions = samplePinTransitions(kMicClockPin);
  result.dataTransitions = samplePinTransitions(kMicDataPin);
  ledcWrite(kMicDiagnosticLedcChannel, 0);
  ledcDetachPin(kMicClockPin);
  pinMode(kMicClockPin, INPUT);
  pinMode(kMicDataPin, INPUT);
  delay(5);
  return result;
}

uint32_t testGpioLoopback() {
  gpio_hold_dis(static_cast<gpio_num_t>(kMicClockPin));
  gpio_reset_pin(static_cast<gpio_num_t>(kMicClockPin));
  const uint32_t configuredHz = ledcSetup(kMicDiagnosticLedcChannel, 1000, 1);
  if (configuredHz == 0) {
    return 0;
  }
  ledcAttachPin(kMicClockPin, kMicDiagnosticLedcChannel);
  gpio_set_direction(static_cast<gpio_num_t>(kMicClockPin), GPIO_MODE_INPUT_OUTPUT);
  ledcWrite(kMicDiagnosticLedcChannel, 1);
  delay(10);
  const uint32_t transitions = samplePinTransitions(kMicClockPin);
  ledcWrite(kMicDiagnosticLedcChannel, 0);
  ledcDetachPin(kMicClockPin);
  pinMode(kMicClockPin, INPUT);
  return transitions;
}

bool testMicrophoneDigitalBus() {
  Serial.println("\n--- Microphone digital bus diagnostic ---");
  Serial.println("Probing PDM CLK and DATA at the XIAO GPIO pins.");
  if (!installMicrophone()) {
    printResult("Microphone initialization", false);
    Serial.println("Microphone diagnostic complete.");
    return false;
  }

  // The T3902 requires no more than 20 ms to wake after its clock starts.
  gpio_set_direction(static_cast<gpio_num_t>(kMicClockPin), GPIO_MODE_INPUT_OUTPUT);
  delay(50);
  int16_t warmupSamples[64];
  size_t warmupBytesRead = 0;
  i2s_read(I2S_NUM_0, warmupSamples, sizeof(warmupSamples), &warmupBytesRead,
           pdMS_TO_TICKS(100));
  const uint32_t i2sClockTransitions = samplePinTransitions(kMicClockPin);
  const uint32_t i2sDataTransitions = samplePinTransitions(kMicDataPin);

  i2s_stop(I2S_NUM_0);
  i2s_driver_uninstall(I2S_NUM_0);
  pinMode(kMicClockPin, INPUT);

  // SELECT is tied to GND, so the T3902 DATA output is high impedance with
  // the clock stopped. A healthy, non-shorted line should follow both pulls.
  delay(5);
  pinMode(kMicDataPin, INPUT_PULLUP);
  delay(5);
  const bool dataWithPullUp = digitalRead(kMicDataPin) == HIGH;
  pinMode(kMicDataPin, INPUT_PULLDOWN);
  delay(5);
  const bool dataWithPullDown = digitalRead(kMicDataPin) == HIGH;
  pinMode(kMicDataPin, INPUT);

  // Sweep every T3902 operating clock range. This distinguishes an I2S clock
  // configuration problem from a microphone/PCB problem.
  constexpr uint32_t diagnosticFrequenciesHz[] = {
      512000,   // Low-power mode
      1024000,  // Standard mode, 64x 16 kHz
      2048000,  // Standard mode
      4096000,  // High-performance mode lower edge
  };
  DiagnosticClockResult clockResults[
      sizeof(diagnosticFrequenciesHz) / sizeof(diagnosticFrequenciesHz[0])];
  bool diagnosticClockConfigured = true;
  bool diagnosticClockActive = true;
  bool diagnosticDataActive = false;
  for (size_t i = 0; i < sizeof(diagnosticFrequenciesHz) / sizeof(diagnosticFrequenciesHz[0]); ++i) {
    clockResults[i] = probeDataWithDiagnosticClock(diagnosticFrequenciesHz[i]);
    diagnosticClockConfigured &= clockResults[i].configuredHz > 0;
    diagnosticClockActive &= clockResults[i].clockTransitions > 10;
    diagnosticDataActive |= clockResults[i].dataTransitions > 10;
  }

  // If the microphone is not driving, DATA will simply follow these weak
  // internal pulls even while a valid clock is present.
  const DiagnosticClockResult dataPullUp =
      probeDataWithDiagnosticClock(2048000, INPUT_PULLUP);
  const DiagnosticClockResult dataPullDown =
      probeDataWithDiagnosticClock(2048000, INPUT_PULLDOWN);
  const uint32_t gpioLoopbackTransitions = testGpioLoopback();

  Serial.printf("I2S PDM GPIO42/GPIO41 transitions per 1M fast reads=%lu/%lu\n",
                static_cast<unsigned long>(i2sClockTransitions),
                static_cast<unsigned long>(i2sDataTransitions));
  for (const DiagnosticClockResult &result : clockResults) {
    Serial.printf("Diagnostic clock: requested=%lu Hz, configured=%lu Hz, GPIO42/GPIO41 transitions per 1M reads=%lu/%lu\n",
                  static_cast<unsigned long>(result.requestedHz),
                  static_cast<unsigned long>(result.configuredHz),
                  static_cast<unsigned long>(result.clockTransitions),
                  static_cast<unsigned long>(result.dataTransitions));
  }
  Serial.printf("GPIO41 transitions per 1M reads at 2.048 MHz + pull-up=%lu, pull-down=%lu\n",
                static_cast<unsigned long>(dataPullUp.dataTransitions),
                static_cast<unsigned long>(dataPullDown.dataTransitions));
  Serial.printf("GPIO fast-read loopback: GPIO42 1 kHz transitions per 1M reads=%lu\n",
                static_cast<unsigned long>(gpioLoopbackTransitions));
  Serial.printf("DATA idle pull test: pull-up=%s, pull-down=%s\n",
                dataWithPullUp ? "HIGH" : "LOW",
                dataWithPullDown ? "HIGH" : "LOW");

  const bool clockActive = i2sClockTransitions > 10;
  const bool dataActive = i2sDataTransitions > 10;
  const bool dataFollowsPulls = dataWithPullUp && !dataWithPullDown;
  const bool activeDataFollowsPulls =
      dataPullUp.dataTransitions == 0 && dataPullDown.dataTransitions == 0;

  Serial.printf("[INFO] I2S PDM clock pad readback - %s\n",
                clockActive ? "GPIO42 toggling observed"
                            : "no transitions observed by direct GPIO register sampling");
  printResult("I2S PDM DATA transitions", dataActive,
              dataActive ? "GPIO41 is toggling" : "GPIO41 did not show valid PDM pulses");
  printResult("Independent diagnostic clock generator", diagnosticClockConfigured,
              diagnosticClockConfigured ? "GPIO42 configured at all four test frequencies"
                                        : "LEDC clock setup failed");
  printResult("Independent diagnostic clock pad activity", diagnosticClockActive,
              diagnosticClockActive ? "GPIO register sampling observed GPIO42 transitions"
                                    : "GPIO42 register sampling saw no transitions");
  printResult("GPIO sampling loopback calibration", gpioLoopbackTransitions > 2,
              gpioLoopbackTransitions > 2 ? "GPIO register sampling sees 1 kHz GPIO42 output"
                                           : "GPIO register sampling path is not validated");
  printResult("Microphone DATA response to diagnostic clock", diagnosticDataActive,
              diagnosticDataActive ? "GPIO41 returned PDM pulses"
                                   : "GPIO41 remained inactive across all clock ranges");
  printResult("Microphone DATA idle short check", dataFollowsPulls,
              dataFollowsPulls ? "line follows internal pulls; no hard short detected"
                               : "possible DATA short, solder bridge, or incorrect orientation");
  printResult("Microphone DATA drive check", !activeDataFollowsPulls,
              activeDataFollowsPulls
                  ? "DATA still follows weak pulls while clocked; microphone output is high impedance"
                  : "DATA does not simply follow weak pulls while clocked");

  if (diagnosticClockConfigured && !diagnosticClockActive) {
    Serial.println("Diagnosis: GPIO42 clock pad did not toggle; inspect GPIO routing and hold state.");
  } else if (diagnosticClockConfigured && diagnosticClockActive && !diagnosticDataActive &&
      dataFollowsPulls && activeDataFollowsPulls) {
    Serial.println("Diagnosis: GPIO41 is not hard-shorted, but MIC1 does not return PDM data");
    Serial.println("at any T3902 clock range, and DATA remains high impedance while clocked.");
    Serial.println("Check MIC1 Pin5 supply, CN1 Pad23-to-MIC1 Pin4 continuity,");
    Serial.println("CN1 Pad21-to-MIC1 Pin1 continuity, MIC1 orientation/soldering, then MIC1 itself.");
  } else if (!dataFollowsPulls) {
    Serial.println("Diagnosis: inspect the GPIO41 DATA net for a short or solder bridge before powering again.");
  } else if (diagnosticDataActive && !dataActive) {
    Serial.println("Diagnosis: MIC1 and PCB respond to a direct clock; repair the I2S clock configuration.");
  }
  Serial.println("USB remains connected to the XIAO main board; the expansion board has no USB connector.");
  Serial.println("Microphone diagnostic complete.");
  return diagnosticClockConfigured && diagnosticClockActive && diagnosticDataActive && dataFollowsPulls;
}

bool testMicrophone() {
  Serial.println("\n--- Microphone activity test ---");
  Serial.println("Speak or tap near the microphone for the next 3 seconds.");
  if (!installMicrophone()) {
    printResult("Microphone initialization", false);
    return false;
  }

  int16_t samples[256];
  uint64_t sumSquares = 0;
  uint32_t sampleCount = 0;
  uint32_t changedSamples = 0;
  uint32_t saturatedSamples = 0;
  int16_t minimum = INT16_MAX;
  int16_t maximum = INT16_MIN;
  int16_t previous = 0;
  bool havePrevious = false;
  bool readFailed = false;
  const uint32_t startedAt = millis();

  while (millis() - startedAt < kMicTestDurationMs) {
    size_t bytesRead = 0;
    if (i2s_read(I2S_NUM_0, samples, sizeof(samples), &bytesRead, pdMS_TO_TICKS(250)) != ESP_OK) {
      readFailed = true;
      break;
    }
    const size_t count = bytesRead / sizeof(samples[0]);
    for (size_t i = 0; i < count; ++i) {
      const int32_t sample = samples[i];
      minimum = min(minimum, samples[i]);
      maximum = max(maximum, samples[i]);
      sumSquares += static_cast<uint64_t>(sample * sample);
      if (havePrevious && samples[i] != previous) {
        ++changedSamples;
      }
      if (samples[i] == INT16_MIN || samples[i] == INT16_MAX) {
        ++saturatedSamples;
      }
      previous = samples[i];
      havePrevious = true;
      ++sampleCount;
    }
  }

  i2s_stop(I2S_NUM_0);
  i2s_driver_uninstall(I2S_NUM_0);
  pinMode(kMicClockPin, INPUT);
  pinMode(kMicDataPin, INPUT);

  const double rms = sampleCount == 0 ? 0.0 : sqrt(static_cast<double>(sumSquares) / sampleCount);
  const int32_t peakToPeak = sampleCount == 0 ? 0 :
      static_cast<int32_t>(maximum) - static_cast<int32_t>(minimum);
  const bool hasActivity = !readFailed && sampleCount >= kMicSampleRate * 2 &&
                           peakToPeak >= 64 &&
                           changedSamples > sampleCount / 100 &&
                           saturatedSamples < sampleCount / 5;
  Serial.printf("Samples: %lu, min: %d, max: %d, p-p: %ld, RMS: %.1f, changed: %lu, saturated: %lu\n",
                static_cast<unsigned long>(sampleCount), minimum, maximum,
                static_cast<long>(peakToPeak), rms,
                static_cast<unsigned long>(changedSamples),
                static_cast<unsigned long>(saturatedSamples));
  printResult("Microphone electrical activity", hasActivity,
              "final audio quality still requires listening to a recording");
  return hasActivity;
}

void testUserLed() {
  Serial.println("\n--- XIAO USER LED test ---");
  pinMode(kUserLedPin, OUTPUT);
  for (uint8_t i = 0; i < 3; ++i) {
    digitalWrite(kUserLedPin, LOW);
    delay(350);
    digitalWrite(kUserLedPin, HIGH);
    delay(350);
  }
  printManual("XIAO USER LED", "confirm that it blinked three times");
}

void printPinMap() {
  Serial.println("\nCustom expansion board pin map:");
  Serial.println("  CN1 Pad1  -> NC (XIAO GPIO21 remains connected to onboard USER LED)");
  Serial.println("  CN1 Pad19 -> GPIO40 / SD_PWR_EN");
  Serial.println("  CN1 Pad21 -> GPIO41 / MIC_DATA");
  Serial.println("  CN1 Pad23 -> GPIO42 / MIC_CLK");
  Serial.println("  CN1 Pad22 -> GPIO9  / SD_MOSI");
  Serial.println("  CN1 Pad24 -> GPIO8  / SD_MISO");
  Serial.println("  CN1 Pad26 -> GPIO7  / SD_SCK");
  Serial.println("  CN1 Pad28 -> GPIO3  / SD_CS");
}

void runAllTests() {
  Serial.println("\n========================================");
  Serial.println("AI Recording Bean hardware self-test");
  Serial.println("========================================");
  printPinMap();

  TestSummary summary;
  testUserLed();
  ++summary.manual;
  testSdReadWrite() ? ++summary.passed : ++summary.failed;
  testSdPowerCycles(10) ? ++summary.passed : ++summary.failed;
  testMicrophone() ? ++summary.passed : ++summary.failed;
  unmountAndPowerOffSd();

  Serial.println("\n--- Automatic test summary ---");
  Serial.printf("PASS: %u, FAIL: %u, MANUAL: %u\n",
                static_cast<unsigned>(summary.passed),
                static_cast<unsigned>(summary.failed),
                static_cast<unsigned>(summary.manual));
  Serial.println(summary.failed == 0 ? "AUTOMATIC TESTS PASSED" : "ONE OR MORE TESTS FAILED");
  Serial.println("TF power is OFF after the test.");
}

void printMenu() {
  Serial.println("\nCommands:");
  Serial.println("  A - run all tests (includes 10 SD power cycles)");
  Serial.println("  S - SD mount + 64 KiB write/read verification");
  Serial.println("  C - extended 50-cycle SD power test");
  Serial.println("  M - microphone activity test");
  Serial.println("  D - diagnose microphone CLK/DATA electrical activity");
  Serial.println("  L - blink XIAO onboard USER LED three times");
  Serial.println("  0 - turn TF power OFF and keep bus pins high impedance");
  Serial.println("  1 - turn TF power ON without mounting (for voltage measurement)");
  Serial.println("  P - print the verified connector pin map");
  Serial.println("  ? - print this menu");
}

void handleCommand(char command) {
  switch (command) {
    case 'A':
      runAllTests();
      break;
    case 'S':
      testSdReadWrite();
      break;
    case 'C':
      testSdPowerCycles(50);
      break;
    case 'M':
      testMicrophone();
      break;
    case 'D':
      testMicrophoneDigitalBus();
      break;
    case 'L':
      testUserLed();
      break;
    case '0':
      unmountAndPowerOffSd();
      Serial.println("TF power OFF. Measure VCC_SD_3V3 to GND; expected near 0 V.");
      break;
    case '1':
      powerOnSdWithoutMounting();
      Serial.println("TF power ON, SD bus high impedance. Measure VCC_SD_3V3 to GND; expected near 3.3 V.");
      break;
    case 'P':
      printPinMap();
      break;
    case '?':
      printMenu();
      break;
    default:
      break;
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);
  const uint32_t serialWaitStartedAt = millis();
  while (!Serial && millis() - serialWaitStartedAt < 3000) {
    delay(10);
  }

  gpio_deep_sleep_hold_dis();
  gpio_hold_dis(static_cast<gpio_num_t>(kSdPowerEnPin));
  gpio_hold_dis(static_cast<gpio_num_t>(kMicClockPin));
  unmountAndPowerOffSd();
  pinMode(kUserLedPin, OUTPUT);
  digitalWrite(kUserLedPin, HIGH);

  Serial.println();
  Serial.println("Self-test firmware booted. Insert a FAT32 TF card before SD tests.");
  printMenu();
  runAllTests();
  printMenu();
}

void loop() {
  while (Serial.available() > 0) {
    char command = static_cast<char>(Serial.read());
    if (command >= 'a' && command <= 'z') {
      command = static_cast<char>(command - 'a' + 'A');
    }
    handleCommand(command);
  }
  delay(10);
}
