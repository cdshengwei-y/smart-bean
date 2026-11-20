#include <Arduino.h>
#include <ArduinoJson.h>
#include <PubSubClient.h>
#include <BLE2902.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <ESPmDNS.h>
#include <HTTPClient.h>
#include <SD.h>
#include <SPI.h>
#include <HTTPUpdate.h>
#include <Preferences.h>
#include <WebServer.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <driver/i2s.h>
#include <driver/gpio.h>
#include <esp_sleep.h>
#include <esp_system.h>
#include <mbedtls/md.h>
#include <mbedtls/base64.h>
#include <mbedtls/sha256.h>
#include <memory>
#include <new>
#include <sys/time.h>
#include <time.h>

#if __has_include("device_config.h")
#include "device_config.h"
#endif
#include "device_identity.h"

#ifndef RECORDER_WIFI_SSID
#define RECORDER_WIFI_SSID ""
#endif
#ifndef RECORDER_WIFI_PASSWORD
#define RECORDER_WIFI_PASSWORD ""
#endif
#ifndef RECORDER_MQTT_ENABLED
#define RECORDER_MQTT_ENABLED 0
#endif
#ifndef RECORDER_MQTT_HOST
#define RECORDER_MQTT_HOST ""
#endif
#ifndef RECORDER_MQTT_PORT
#define RECORDER_MQTT_PORT 1883
#endif
#ifndef RECORDER_MQTT_USERNAME
#define RECORDER_MQTT_USERNAME ""
#endif
#ifndef RECORDER_MQTT_PASSWORD
#define RECORDER_MQTT_PASSWORD ""
#endif
#ifndef RECORDER_DEVICE_ID
#define RECORDER_DEVICE_ID ""
#endif
#ifndef RECORDER_FORCE_EFUSE_DEVICE_ID
#define RECORDER_FORCE_EFUSE_DEVICE_ID 0
#endif
#ifndef RECORDER_MINIO_ENDPOINT
#define RECORDER_MINIO_ENDPOINT ""
#endif
#ifndef RECORDER_MINIO_BUCKET
#define RECORDER_MINIO_BUCKET "recordingbean"
#endif
#ifndef RECORDER_MINIO_ACCESS_KEY
#define RECORDER_MINIO_ACCESS_KEY ""
#endif
#ifndef RECORDER_MINIO_SECRET_KEY
#define RECORDER_MINIO_SECRET_KEY ""
#endif
#ifndef RECORDER_MINIO_REGION
#define RECORDER_MINIO_REGION "us-east-1"
#endif
#ifndef RECORDER_MINIO_USE_TLS
#define RECORDER_MINIO_USE_TLS 0
#endif
#ifndef RECORDER_MINIO_TLS_CA_CERT
#define RECORDER_MINIO_TLS_CA_CERT ""
#endif
#ifndef RECORDER_OTA_MANIFEST_URL
#define RECORDER_OTA_MANIFEST_URL ""
#endif
#ifndef RECORDER_FIRMWARE_VERSION
#define RECORDER_FIRMWARE_VERSION "0.3.0"
#endif
#ifndef RECORDER_TEST_RECORDING_LIMIT_SECONDS
#define RECORDER_TEST_RECORDING_LIMIT_SECONDS 0
#endif
#ifndef RECORDER_WIFI_DIAGNOSTICS
#define RECORDER_WIFI_DIAGNOSTICS 0
#endif
#ifndef RECORDER_DISABLE_CLOUD_UPLOAD
#define RECORDER_DISABLE_CLOUD_UPLOAD 0
#endif
#ifndef RECORDER_SLEEP_IDLE_MS
#define RECORDER_SLEEP_IDLE_MS (2UL * 60UL * 1000UL)
#endif
#ifndef RECORDER_FORCE_SLEEP_TEST
#define RECORDER_FORCE_SLEEP_TEST 0
#endif
#ifndef RECORDER_DIRECT_DEEP_SLEEP_TEST
#define RECORDER_DIRECT_DEEP_SLEEP_TEST 0
#endif
#ifndef RECORDER_DEEP_SLEEP_DIAGNOSTIC_WAKE_US
#define RECORDER_DEEP_SLEEP_DIAGNOSTIC_WAKE_US 0ULL
#endif
#ifndef RECORDER_MIC_RIGHT_CHANNEL
#define RECORDER_MIC_RIGHT_CHANNEL 0
#endif
#ifndef RECORDER_CUSTOM_EXPANSION_BOARD
#define RECORDER_CUSTOM_EXPANSION_BOARD 0
#endif
#ifndef RECORDER_SD_POWER_SWITCH
#define RECORDER_SD_POWER_SWITCH 0
#endif
#ifndef RECORDER_SD_CS_PIN
#define RECORDER_SD_CS_PIN 3
#endif
#ifndef RECORDER_SD_LEGACY_INIT
#define RECORDER_SD_LEGACY_INIT 0
#endif
#ifndef RECORDER_SD_STANDALONE_INIT
#define RECORDER_SD_STANDALONE_INIT 0
#endif
#ifndef RECORDER_SD_BLE_BEFORE_INIT
#define RECORDER_SD_BLE_BEFORE_INIT 0
#endif

namespace {

constexpr char kDeviceName[] = "AI Recording Bean V0.2";
constexpr char kFirmwareVersion[] = RECORDER_FIRMWARE_VERSION;
constexpr char kMdnsService[] = "ai-recorder";
constexpr char kBleDeviceNamePrefix[] = "AI Recording Bean";
constexpr char kBleServiceUuid[] = "d3b10000-8a1f-4e23-9d46-5e6d0f434f01";
constexpr char kBleControlUuid[] = "d3b10001-8a1f-4e23-9d46-5e6d0f434f01";
constexpr char kBleStatusUuid[] = "d3b10002-8a1f-4e23-9d46-5e6d0f434f01";
constexpr char kBleFilesUuid[] = "d3b10003-8a1f-4e23-9d46-5e6d0f434f01";
constexpr char kApSsid[] = "AI-Recording-Bean";
constexpr char kApPassword[] = "recordingbean";

constexpr int kMicClockPin = 42;
constexpr int kMicDataPin = 41;
constexpr i2s_channel_fmt_t kMicChannelFormat =
    RECORDER_MIC_RIGHT_CHANNEL ? I2S_CHANNEL_FMT_ONLY_RIGHT : I2S_CHANNEL_FMT_ONLY_LEFT;
// Custom expansion board: GPIO40 drives the active-high TPS22919 TF power switch.
constexpr int kSdPowerEnPin = 40;
// Optional mic power switch: an active-high load switch feeding MIC_VDD
// (board rev. 20260929+, U2 / TPS22919, EN net MIC_PWR_EN on GPIO15 with a
// 100k pull-down so the mic is unpowered until firmware turns it on).
// Build without RECORDER_MIC_PWR_EN_GPIO for boards where MIC_VDD is hard-wired.
#ifdef RECORDER_MIC_PWR_EN_GPIO
constexpr int kMicPowerEnPin = RECORDER_MIC_PWR_EN_GPIO;
constexpr bool kMicPowerSwitched = true;
#else
constexpr bool kMicPowerSwitched = false;
#endif
constexpr int kSdCsPin = RECORDER_SD_CS_PIN;
constexpr int kSdSckPin = 7;
constexpr int kSdMisoPin = 8;
constexpr int kSdMosiPin = 9;
constexpr uint32_t kSdPowerUpDelayMs = 10;
constexpr uint32_t kSdPowerCycleOffMs = 20;
constexpr uint32_t kSdPowerDownDelayMs = 2;
constexpr uint32_t kSampleRate = 16000;
constexpr uint32_t kBytesPerSecond = kSampleRate * 2;
constexpr size_t kAudioBufferBytes = 1024;
constexpr char kRecordingsDir[] = "/recordings";
constexpr int kHttpOk = 200;
constexpr int kHttpNoContent = 204;
constexpr int kHttpBadRequest = 400;
constexpr int kHttpNotFound = 404;
constexpr int kHttpMethodNotAllowed = 405;
constexpr int kHttpConflict = 409;
constexpr int kHttpInternalServerError = 500;
constexpr int kHttpServiceUnavailable = 503;
constexpr uint32_t kWifiConnectTimeoutMs = 15000;
constexpr uint32_t kWifiRetryIntervalMs = 30000;
// Wi-Fi normally powers down as soon as there is no pending upload.  A manual
// phone download keeps it up briefly, without changing the low-power idle
// behavior during ordinary recording use.
constexpr uint32_t kWifiAccessLeaseMs = 15UL * 60UL * 1000UL;
constexpr uint32_t kUploadRetryIntervalMs = 10000;
constexpr uint32_t kSdRetryIntervalMs = 5000;
constexpr uint32_t kRecordingFlushIntervalMs = 2000;
constexpr uint32_t kBleIdleSleepDelayMs = RECORDER_SLEEP_IDLE_MS;
constexpr size_t kMultipartPartBytes = 5UL * 1024UL * 1024UL;
constexpr uint16_t kMultipartRequestTimeoutMs = 60000;
constexpr size_t kHashBufferBytes = 2048;
constexpr size_t kStorageTransferMaxBase64Bytes = 12000;
constexpr time_t kBeijingUtcOffsetSeconds = 8 * 60 * 60;
constexpr char kUploadedMarkerSuffix[] = ".uploaded";
constexpr char kUploadFailedMarkerSuffix[] = ".upload_failed";
constexpr char kRecordingTempSuffix[] = ".part";

#ifndef RECORDER_SEGMENT_SECONDS
#define RECORDER_SEGMENT_SECONDS 60
#endif
// 直播预览分段长度：录音时同步写一份分段副本，停止后优先上传，
// backend 逐段转写并把文字流进 Obsidian（完整录音仍走原有链路）。
constexpr uint32_t kSegmentSeconds = RECORDER_SEGMENT_SECONDS;
constexpr char kSegmentMarker[] = "__s";

#ifndef RECORDER_LIVE_UPLOAD
#define RECORDER_LIVE_UPLOAD 1
#endif
// 边录边传总开关：录音中持续上传已完结的分段副本。
// 若实测 WiFi 射频干扰麦克风音质，编译时置 0 可整体回退。
constexpr bool kLiveUploadEnabled = RECORDER_LIVE_UPLOAD != 0;

#ifndef RECORDER_LIVE_STREAM_URL
#define RECORDER_LIVE_STREAM_URL ""
#endif
// 直播 PCM 流推送（sherpa 秒级出字）：录音中每 2s 把 PCM 尾块推给 Mac
// 流式引擎。URL 为空则整体关闭；关闭时仅靠 60s 分段兜底出字。
constexpr char kLiveStreamUrl[] = RECORDER_LIVE_STREAM_URL;
constexpr bool kLiveStreamEnabled = sizeof(kLiveStreamUrl) > 1;
constexpr uint32_t kLivePcmPushIntervalMs = 2000;
constexpr size_t kLiveRingBytes = 96 * 1024;  // ~3 秒 16k s16le PCM

// 直播 PCM 环形缓冲：audioTask 生产（写卡同时写入），uploadTask 消费
// （网络推送）。满时丢最老数据——直播优先新鲜度。
// 内存放在 PSRAM：160KB 静态缓冲会挤爆内部 heap，导致蓝牙 GATT 连接
// 因内存不足失败（APP 能搜到设备但永远连不上——2026-11-18 实测教训）。
#include "esp_heap_caps.h"

static uint8_t *liveRing = nullptr;      // PSRAM
static uint8_t *livePushBuffer = nullptr; // PSRAM
static volatile size_t liveRingHead = 0;  // 写位置
static volatile size_t liveRingTail = 0;  // 读位置
static volatile bool liveRingOverflow = false;
static bool liveStreamRuntimeReady = false;
SemaphoreHandle_t liveRingMutex;
constexpr size_t kLivePushBytes = 64 * 1024;
// 内部 RAM 暂存：写卡锁内只做内部 RAM 拷贝，攒批后（锁外）再进 PSRAM——
// 高频 PSRAM 访问与 SD 写卡同临界区时，实测 SD SPI 写入半途失败
// （RECORD_WRITE_FAILED expected=1024 actual=468）。
static uint8_t liveStagingBuffer[8192];
static size_t liveStagingUsed = 0;

static inline bool liveStreamReady() {
  return liveStreamRuntimeReady;
}

static inline void liveRingLock() {
  xSemaphoreTakeRecursive(liveRingMutex, portMAX_DELAY);
}
static inline void liveRingUnlock() {
  xSemaphoreGiveRecursive(liveRingMutex);
}

static size_t liveRingAvailable() {
  return (liveRingHead + kLiveRingBytes - liveRingTail) % kLiveRingBytes;
}

static void liveRingPush(const uint8_t *data, size_t length) {
  if (!liveRingMutex || !liveRing || !liveStreamRuntimeReady) return;
  liveRingLock();
  for (size_t index = 0; index < length; ++index) {
    liveRing[liveRingHead] = data[index];
    liveRingHead = (liveRingHead + 1) % kLiveRingBytes;
    if (liveRingHead == liveRingTail) {
      liveRingTail = (liveRingTail + 1) % kLiveRingBytes;
      liveRingOverflow = true;
    }
  }
  liveRingUnlock();
}

static size_t liveRingTake(uint8_t *out, size_t capacity) {
  if (!liveRingMutex || !liveRing || !liveStreamRuntimeReady) return 0;
  liveRingLock();
  size_t available = liveRingAvailable();
  size_t count = available < capacity ? available : capacity;
  for (size_t index = 0; index < count; ++index) {
    out[index] = liveRing[liveRingTail];
    liveRingTail = (liveRingTail + 1) % kLiveRingBytes;
  }
  liveRingOverflow = false;
  liveRingUnlock();
  return count;
}
constexpr char kUploadStateSuffix[] = ".upload";
constexpr char kUploadStateTempSuffix[] = ".upload.tmp";
constexpr char kSleepMarkerPath[] = "/recordings/.last_sleep";
constexpr uint8_t kMqttCommandHistorySize = 8;
constexpr unsigned int kMqttMaxCommandPayloadBytes = 1024;
constexpr uint32_t kMqttReconnectInitialDelayMs = 1000;
constexpr uint32_t kMqttReconnectMaxDelayMs = 60000;

WebServer server(80);
WiFiClient mqttNetworkClient;
PubSubClient mqttClient(mqttNetworkClient);
SPIClass sdSpi(FSPI);
SemaphoreHandle_t fileMutex;

// 递归锁：SD 卡互斥。上传路径需要在网络传输期间放锁、按小块重新取锁，
// 而部分调用方（audioTask/stopRecording）持锁调用内部再取锁，故用递归互斥。
static inline void sdLock() {
  xSemaphoreTakeRecursive(fileMutex, portMAX_DELAY);
}
static inline void sdUnlock() {
  xSemaphoreGiveRecursive(fileMutex);
}
TaskHandle_t audioTaskHandle;
TaskHandle_t otaTaskHandle = nullptr;
BLECharacteristic *bleControlCharacteristic = nullptr;
BLECharacteristic *bleStatusCharacteristic = nullptr;
BLECharacteristic *bleFilesCharacteristic = nullptr;
QueueHandle_t bleCommandQueue = nullptr;

File recordingFile;
String currentFileName;
String currentRecordingPath;
String uploadingFileName;
volatile bool recording = false;
volatile bool uploadInProgress = false;
volatile uint32_t uploadBytesSent = 0;
volatile uint32_t uploadBytesTotal = 0;
// Scan the TF card only after boot, a completed recording, or an SD remount.
volatile bool uploadScanRequested = true;
int8_t pcmByteSwapMode = -1;
volatile uint32_t recordingStartedAt = 0;
volatile uint32_t lastRecordingFlushAt = 0;
volatile uint16_t fileSequence = 0;
bool sdReady = false;
bool microphoneReady = false;
bool recoveryApActive = false;
bool mdnsStarted = false;
uint32_t nextWifiRetryAt = 0;
uint32_t nextSdRetryAt = 0;
uint32_t nextMqttRetryAt = 0;
uint32_t nextMqttStateAt = 0;
uint32_t mqttReconnectDelayMs = kMqttReconnectInitialDelayMs;
struct MqttCommandHistoryEntry {
  String commandId;
  String capability;
  String status;
  String errorCode;
  String resultJson;
};

MqttCommandHistoryEntry mqttCommandHistory[kMqttCommandHistorySize];
struct PendingMqttAck {
  String commandId;
  String payload;
};
PendingMqttAck pendingMqttAcks[kMqttCommandHistorySize];
String lastUploadError;
String lastPartFailure;
String lastRecordingErrorCode;
String lastCompletedFileName;
String lastBleStatus;
volatile bool bleClientConnected = false;
volatile bool bleStatusUpdatePending = false;
volatile bool bleAdvertisingRestartPending = false;
volatile bool otaUpdateRequested = false;
volatile bool otaInstallRequested = false;
volatile bool otaUpdateInProgress = false;
volatile bool wifiAccessRequested = false;
volatile uint32_t wifiAccessUntil = 0;
volatile bool cloudStorageChangedPending = false;
uint32_t nextBleStatusAt = 0;
uint32_t bleInactiveSince = 0;
String otaStatus = "idle";
String cloudConfigStatus = "default";
String lastSleepBlockerReason;
uint32_t lastSleepBlockerLogAt = 0;

struct RuntimeCloudStorageConfig {
  String provider;
  String endpoint;
  String region;
  String bucket;
  String recordingsPrefix;
  String notesPrefix;
  String accessKey;
  String secretKey;
  String tlsCaCert;
  uint32_t revision = 0;
};

RuntimeCloudStorageConfig runtimeCloudStorage;
String storageTransferBase64;
size_t storageTransferExpected = 0;
uint16_t storageTransferSequence = 0;
bool storageTransferActive = false;

bool wifiAccessLeaseActive();
bool mqttConfigured();
bool maintainWifi(bool forceRetry = false);
void maintainMqtt();
void publishMqttState();
void publishMqttEvent(const char *eventType, const String &commandId = String());
void loadMqttCommandHistory();
String deviceId();
template <typename JsonContainer>
void addStatus(JsonContainer &document);
bool startRecording();
bool stopRecording();
String deepSleepBlockerReason();
void logSleepBlocker(const String &reason);

enum class BleCommandType : uint8_t {
  Start,
  Stop,
  SyncTime,
  ListFiles,
  DeleteFile,
  CheckUpdate,
  EnableWifi,
};

struct BleCommand {
  BleCommandType type;
  uint16_t cursor;
  uint32_t epoch;
  bool installUpdate;
  char fileName[97];
};

constexpr uint16_t kBleFilesPageSize = 10;

struct __attribute__((packed)) WavHeader {
  char riff[4] = {'R', 'I', 'F', 'F'};
  uint32_t riffSize = 36;
  char wave[4] = {'W', 'A', 'V', 'E'};
  char fmt[4] = {'f', 'm', 't', ' '};
  uint32_t fmtSize = 16;
  uint16_t audioFormat = 1;
  uint16_t channels = 1;
  uint32_t sampleRate = kSampleRate;
  uint32_t byteRate = kBytesPerSecond;
  uint16_t blockAlign = 2;
  uint16_t bitsPerSample = 16;
  char data[4] = {'d', 'a', 't', 'a'};
  uint32_t dataSize = 0;
};

void writeWavHeader(File &file, uint32_t dataSize);

void sendJson(int status, JsonDocument &document) {
  String response;
  serializeJson(document, response);
  server.send(status, "application/json; charset=utf-8", response);
}

void sendError(int status, const char *message) {
  StaticJsonDocument<192> document;
  document["error"] = message;
  sendJson(status, document);
}

bool isValidRecordingName(const String &name) {
  return name.length() > 4 && name.length() <= 96 && name.endsWith(".wav") &&
         name.indexOf('/') < 0 && name.indexOf('\\') < 0 && name.indexOf("..") < 0;
}

String urlDecode(const String &value) {
  String result;
  result.reserve(value.length());
  for (size_t index = 0; index < value.length(); ++index) {
    if (value[index] == '%' && index + 2 < value.length()) {
      char hex[3] = {value[index + 1], value[index + 2], '\0'};
      result += static_cast<char>(strtol(hex, nullptr, 16));
      index += 2;
    } else if (value[index] == '+') {
      result += ' ';
    } else {
      result += value[index];
    }
  }
  return result;
}

String contentDispositionHeader(const String &name) {
  static const char hex[] = "0123456789ABCDEF";
  String encoded;
  encoded.reserve(name.length() * 3);
  for (size_t index = 0; index < name.length(); ++index) {
    const uint8_t byte = static_cast<uint8_t>(name[index]);
    const bool unreserved = (byte >= 'A' && byte <= 'Z') || (byte >= 'a' && byte <= 'z') ||
                            (byte >= '0' && byte <= '9') || byte == '-' || byte == '.' ||
                            byte == '_' || byte == '~';
    if (unreserved) {
      encoded += static_cast<char>(byte);
    } else {
      encoded += '%';
      encoded += hex[byte >> 4];
      encoded += hex[byte & 0x0F];
    }
  }
  return "attachment; filename*=UTF-8''" + encoded;
}

String recordingPath(const String &name) {
  return String(kRecordingsDir) + "/" + name;
}

String partialRecordingPath(const String &name) {
  return recordingPath(name) + kRecordingTempSuffix;
}

bool isSegmentFileName(const String &name) {
  const int marker = name.indexOf(kSegmentMarker);
  return marker > 0 && name.endsWith(".wav");
}

String segmentStem(const String &segmentName) {
  const int marker = segmentName.indexOf(kSegmentMarker);
  return marker > 0 ? segmentName.substring(0, marker) : segmentName;
}

String segmentFileName(const String &baseName, uint16_t index) {
  const int dot = baseName.lastIndexOf('.');
  const String stem = dot >= 0 ? baseName.substring(0, dot) : baseName;
  char suffix[10];
  snprintf(suffix, sizeof(suffix), "%s%02u.wav", kSegmentMarker,
           static_cast<unsigned>(index));
  return stem + suffix;
}

String uploadStatePath(const String &name) {
  return recordingPath(name) + kUploadStateSuffix;
}

String uploadStateTempPath(const String &name) {
  return recordingPath(name) + kUploadStateTempSuffix;
}

String isoTimestamp(time_t value) {
  if (value < 1700000000) {
    return "unsynced";
  }
  tm utcTime;
  gmtime_r(&value, &utcTime);
  char text[25];
  strftime(text, sizeof(text), "%Y-%m-%dT%H:%M:%SZ", &utcTime);
  return String(text);
}

String isoTimestamp() {
  return isoTimestamp(time(nullptr));
}

int batteryPercent() {
  // The XIAO Sense board does not expose a fixed battery ADC pin in this design.
  // Return -1 until a battery divider is added and calibrated on the final board.
  return -1;
}

bool hasValidClock() {
  return time(nullptr) >= 1700000000;
}

bool isConfiguredValue(const char *value) {
  return value != nullptr && value[0] != '\0' && !String(value).startsWith("YOUR_");
}

bool isConfiguredValue(const String &value) {
  return !value.isEmpty() && !value.startsWith("YOUR_");
}

String jsonText(JsonDocument &document, const char *key, const String &fallback) {
  if (!document.containsKey(key)) {
    return fallback;
  }
  const char *value = document[key].as<const char *>();
  return value == nullptr ? String() : String(value);
}

bool validStorageEndpoint(String endpoint) {
  endpoint.trim();
  while (endpoint.endsWith("/")) {
    endpoint.remove(endpoint.length() - 1);
  }
  const int schemeEnd = endpoint.indexOf("://");
  if (schemeEnd <= 0 || endpoint.indexOf('?', schemeEnd + 3) >= 0 ||
      endpoint.indexOf('#', schemeEnd + 3) >= 0) {
    return false;
  }
  const String scheme = endpoint.substring(0, schemeEnd);
  if (scheme != "http" && scheme != "https") {
    return false;
  }
  const String authority = endpoint.substring(schemeEnd + 3);
  if (authority.isEmpty() || authority.indexOf('/') >= 0 ||
      authority.indexOf(' ') >= 0 || authority.indexOf('\r') >= 0 ||
      authority.indexOf('\n') >= 0) {
    return false;
  }
  return true;
}

String normalizedStoragePrefix(String prefix, const String &fallback) {
  prefix = prefix.isEmpty() ? fallback : prefix;
  prefix.trim();
  while (prefix.startsWith("/")) {
    prefix.remove(0, 1);
  }
  while (prefix.endsWith("/")) {
    prefix.remove(prefix.length() - 1);
  }
  return prefix + "/";
}

bool validStoragePrefix(const String &prefix) {
  return !prefix.isEmpty() && prefix.length() <= 512 && !prefix.startsWith("/") &&
         prefix.indexOf("..") < 0 && prefix.indexOf('\\') < 0 &&
         prefix.indexOf('\r') < 0 && prefix.indexOf('\n') < 0;
}

bool applyStorageJson(JsonDocument &document, RuntimeCloudStorageConfig &candidate) {
  const String type = jsonText(document, "type", "s3");
  if (type != "s3") {
    return false;
  }
  candidate.provider = jsonText(document, "provider", candidate.provider);
  candidate.endpoint = jsonText(document, "endpoint", candidate.endpoint);
  candidate.region = jsonText(document, "region", candidate.region);
  candidate.bucket = jsonText(document, "bucket", candidate.bucket);
  candidate.recordingsPrefix = normalizedStoragePrefix(
      jsonText(document, "recordings_prefix", candidate.recordingsPrefix),
      "recordings/");
  candidate.accessKey = jsonText(document, "access_key", candidate.accessKey);
  candidate.secretKey = jsonText(document, "secret_key", candidate.secretKey);
  candidate.tlsCaCert = jsonText(document, "tls_ca_cert", candidate.tlsCaCert);

  candidate.endpoint.trim();
  while (candidate.endpoint.endsWith("/")) {
    candidate.endpoint.remove(candidate.endpoint.length() - 1);
  }
  const bool tls = candidate.endpoint.startsWith("https://");
  return validStorageEndpoint(candidate.endpoint) && !candidate.region.isEmpty() &&
         candidate.region.length() <= 128 && !candidate.bucket.isEmpty() &&
         candidate.bucket.length() <= 127 && candidate.bucket.indexOf('/') < 0 &&
         candidate.bucket.indexOf('\\') < 0 && isConfiguredValue(candidate.accessKey) &&
         isConfiguredValue(candidate.secretKey) && validStoragePrefix(candidate.recordingsPrefix) &&
         (!tls || !candidate.tlsCaCert.isEmpty());
}

void loadRuntimeCloudStorage() {
#if RECORDER_DISABLE_CLOUD_UPLOAD
  // 拉取式架构（2026-11-20）：录音豆只管录音，Mac 服务从 TF 卡拉取录音
  // 与分段并写云端存储。设备不再自行上传（uploadTask 空转），恢复热点、
  // OTA 与 WiFi 拉取连接不受影响。忽略 preferences 里已保存的云配置。
  runtimeCloudStorage.provider = "none";
  runtimeCloudStorage.endpoint = "";
  runtimeCloudStorage.region = "";
  runtimeCloudStorage.bucket = "";
  runtimeCloudStorage.accessKey = "";
  runtimeCloudStorage.secretKey = "";
  runtimeCloudStorage.tlsCaCert = "";
  runtimeCloudStorage.recordingsPrefix = "recordings/";
  runtimeCloudStorage.revision = 0;
  cloudConfigStatus = "fetcher_mode";
  Serial.println("Cloud upload disabled: fetcher mode (service pulls from device)");
  return;
#endif
  runtimeCloudStorage.provider = "minio";
  runtimeCloudStorage.endpoint = String(RECORDER_MINIO_ENDPOINT);
  runtimeCloudStorage.region = String(RECORDER_MINIO_REGION);
  runtimeCloudStorage.bucket = String(RECORDER_MINIO_BUCKET);
  runtimeCloudStorage.recordingsPrefix = "recordings/";
  runtimeCloudStorage.accessKey = String(RECORDER_MINIO_ACCESS_KEY);
  runtimeCloudStorage.secretKey = String(RECORDER_MINIO_SECRET_KEY);
  runtimeCloudStorage.tlsCaCert = String(RECORDER_MINIO_TLS_CA_CERT);
  runtimeCloudStorage.revision = 0;
  cloudConfigStatus = "default";

  Preferences preferences;
  if (!preferences.begin("cloud", true)) {
    return;
  }
  const String saved = preferences.getString("config", "");
  const uint32_t savedRevision = preferences.getULong("revision", 0);
  preferences.end();
  if (saved.isEmpty()) {
    return;
  }

  DynamicJsonDocument document(8192);
  if (deserializeJson(document, saved) != DeserializationError::Ok) {
    cloudConfigStatus = "invalid";
    return;
  }
  RuntimeCloudStorageConfig candidate = runtimeCloudStorage;
  if (!applyStorageJson(document, candidate)) {
    cloudConfigStatus = "invalid";
    return;
  }
  runtimeCloudStorage = candidate;
  runtimeCloudStorage.revision = savedRevision;
  cloudConfigStatus = "saved";
}

bool persistRuntimeCloudStorage(RuntimeCloudStorageConfig candidate) {
  DynamicJsonDocument document(8192);
  document["type"] = "s3";
  document["provider"] = candidate.provider;
  document["endpoint"] = candidate.endpoint;
  document["region"] = candidate.region;
  document["bucket"] = candidate.bucket;
  document["recordings_prefix"] = candidate.recordingsPrefix;
  document["access_key"] = candidate.accessKey;
  document["secret_key"] = candidate.secretKey;
  document["tls_ca_cert"] = candidate.tlsCaCert;
  String serialized;
  if (serializeJson(document, serialized) == 0) {
    return false;
  }
  Preferences preferences;
  if (!preferences.begin("cloud", false)) {
    return false;
  }
  const size_t written = preferences.putString("config", serialized);
  const bool revisionWritten =
      preferences.putULong("revision", candidate.revision) > 0;
  preferences.end();
  return written > 0 && revisionWritten;
}

bool decodeStorageTransfer(RuntimeCloudStorageConfig &candidate) {
  if (storageTransferBase64.isEmpty() ||
      storageTransferBase64.length() != storageTransferExpected) {
    return false;
  }
  std::unique_ptr<uint8_t[]> decoded(
      new (std::nothrow) uint8_t[storageTransferBase64.length() + 1]);
  if (!decoded) {
    return false;
  }
  size_t decodedLength = 0;
  const int result = mbedtls_base64_decode(
      decoded.get(),
      storageTransferBase64.length() + 1,
      &decodedLength,
      reinterpret_cast<const unsigned char *>(storageTransferBase64.c_str()),
      storageTransferBase64.length());
  if (result != 0 || decodedLength == 0) {
    return false;
  }
  decoded[decodedLength] = '\0';
  DynamicJsonDocument document(8192);
  if (deserializeJson(document, reinterpret_cast<char *>(decoded.get()), decodedLength) !=
      DeserializationError::Ok) {
    return false;
  }
  candidate = runtimeCloudStorage;
  return applyStorageJson(document, candidate);
}

bool handleStorageBleCommand(const String &command) {
  if (command.startsWith("STORAGE:BEGIN:")) {
    const String lengthText = command.substring(strlen("STORAGE:BEGIN:"));
    const unsigned long expected = strtoul(lengthText.c_str(), nullptr, 10);
    if (expected == 0 || expected > kStorageTransferMaxBase64Bytes) {
      storageTransferActive = false;
      cloudConfigStatus = "invalid";
    } else {
      storageTransferBase64 = "";
      storageTransferBase64.reserve(expected);
      storageTransferExpected = expected;
      storageTransferSequence = 0;
      storageTransferActive = true;
      cloudConfigStatus = "receiving";
    }
    bleStatusUpdatePending = true;
    return true;
  }
  if (command.startsWith("STORAGE:CHUNK:")) {
    if (!storageTransferActive) {
      cloudConfigStatus = "invalid";
      bleStatusUpdatePending = true;
      return true;
    }
    const String remainder = command.substring(strlen("STORAGE:CHUNK:"));
    const int separator = remainder.indexOf(':');
    const unsigned long sequence =
        separator >= 0 ? strtoul(remainder.substring(0, separator).c_str(), nullptr, 10)
                       : 65535;
    const String payload = separator >= 0 ? remainder.substring(separator + 1) : "";
    if (sequence != storageTransferSequence ||
        storageTransferBase64.length() + payload.length() > storageTransferExpected) {
      storageTransferActive = false;
      cloudConfigStatus = "invalid";
    } else {
      storageTransferBase64 += payload;
      ++storageTransferSequence;
    }
    bleStatusUpdatePending = true;
    return true;
  }
  if (command == "STORAGE:COMMIT") {
    RuntimeCloudStorageConfig candidate;
    bool accepted = storageTransferActive && !recording && !uploadInProgress &&
                    decodeStorageTransfer(candidate);
    storageTransferActive = false;
    storageTransferBase64 = "";
    if (!accepted) {
      cloudConfigStatus = recording || uploadInProgress ? "busy" : "invalid";
      bleStatusUpdatePending = true;
      return true;
    }
    candidate.revision = runtimeCloudStorage.revision + 1;
    if (candidate.revision == 0) {
      candidate.revision = 1;
    }
    if (!persistRuntimeCloudStorage(candidate)) {
      cloudConfigStatus = "save_failed";
      bleStatusUpdatePending = true;
      return true;
    }
    runtimeCloudStorage = candidate;
    cloudConfigStatus = "saved";
    lastUploadError = "";
    uploadScanRequested = true;
    cloudStorageChangedPending = true;
    bleStatusUpdatePending = true;
    return true;
  }
  return false;
}

bool cloudConfigured() {
  const String endpoint = runtimeCloudStorage.endpoint;
  const bool validScheme = endpoint.startsWith("http://") || endpoint.startsWith("https://");
  const bool endpointUsesTls = endpoint.startsWith("https://");
  return isConfiguredValue(RECORDER_WIFI_SSID) && isConfiguredValue(runtimeCloudStorage.bucket) &&
         isConfiguredValue(runtimeCloudStorage.accessKey) &&
         isConfiguredValue(runtimeCloudStorage.secretKey) && validScheme &&
         endpointUsesTls == !runtimeCloudStorage.tlsCaCert.isEmpty() &&
         validStoragePrefix(runtimeCloudStorage.recordingsPrefix);
}

bool usesVirtualHostStorage() {
  String provider = runtimeCloudStorage.provider;
  provider.toLowerCase();
  return provider == "huawei-obs";
}

String rawStorageEndpoint() {
  String endpoint = runtimeCloudStorage.endpoint;
  endpoint.trim();
  while (endpoint.endsWith("/")) {
    endpoint.remove(endpoint.length() - 1);
  }
  return endpoint;
}

String minioEndpoint() {
  String endpoint = rawStorageEndpoint();
  if (!usesVirtualHostStorage()) {
    return endpoint;
  }

  const int schemeEnd = endpoint.indexOf("://");
  if (schemeEnd < 0) {
    return endpoint;
  }
  const String scheme = endpoint.substring(0, schemeEnd);
  String authority = endpoint.substring(schemeEnd + 3);
  const int pathStart = authority.indexOf('/');
  if (pathStart >= 0) {
    authority = authority.substring(0, pathStart);
  }
  if (authority.isEmpty() || runtimeCloudStorage.bucket.isEmpty()) {
    return endpoint;
  }
  return scheme + "://" + runtimeCloudStorage.bucket + "." + authority;
}

String minioHost() {
  String host = minioEndpoint();
  const int schemeEnd = host.indexOf("://");
  if (schemeEnd >= 0) {
    host = host.substring(schemeEnd + 3);
  }
  const int pathStart = host.indexOf('/');
  return pathStart >= 0 ? host.substring(0, pathStart) : host;
}

bool isValidDeviceId(const String &value) {
  if (value.isEmpty() || value.length() > 50) {
    return false;
  }
  for (size_t index = 0; index < value.length(); ++index) {
    const char character = value[index];
    const bool allowed = (character >= 'A' && character <= 'Z') ||
                         (character >= 'a' && character <= 'z') ||
                         (character >= '0' && character <= '9') ||
                         character == '-' || character == '_';
    if (!allowed) {
      return false;
    }
  }
  return true;
}

String deviceId() {
  static String value;
  if (!value.isEmpty()) {
    return value;
  }
#ifdef RECORDER_TEST_DEVICE_ID
  // Explicit identity for an operator-requested binding test. Shared production
  // builds never define this flag and continue to use the hardware identity.
  const String testDeviceId = String(RECORDER_TEST_DEVICE_ID);
  if (isValidDeviceId(testDeviceId)) {
    value = testDeviceId;
    return value;
  }
#endif
  const uint64_t mac = ESP.getEfuseMac();
  // Hardware identity must take precedence over a build flag. The ESP API can
  // expose the same 48 MAC bits in the reverse order used by printed labels.
  if (recorder_identity::matchesMac(mac, recorder_identity::kBeanYe1RecordedMac)) {
    value = "bean_ye_1";
    return value;
  }
  if (recorder_identity::matchesMac(mac, recorder_identity::kBeanYe2RecordedMac)) {
    value = "bean_ye_2";
    return value;
  }
#if !RECORDER_FORCE_EFUSE_DEVICE_ID
  const String configured = String(RECORDER_DEVICE_ID);
  if (isValidDeviceId(configured)) {
    value = configured;
    return value;
  }
#endif
  char text[18] = {};
  snprintf(
      text,
      sizeof(text),
      "bean-%04lX%08lX",
      static_cast<unsigned long>((mac >> 32) & 0xFFFF),
      static_cast<unsigned long>(mac & 0xFFFFFFFF));
  value = text;
  return value;
}

bool mqttConfigured() {
#if RECORDER_MQTT_ENABLED
  return isConfiguredValue(RECORDER_MQTT_HOST) && RECORDER_MQTT_PORT > 0;
#else
  return false;
#endif
}

String mqttTopic(const char *suffix) {
  return String("agentos/devices/") + deviceId() + "/" + suffix;
}

void publishMqttJson(const String &topic, JsonDocument &document, bool retain = false) {
  if (!mqttClient.connected()) {
    return;
  }
  String payload;
  serializeJson(document, payload);
  mqttClient.publish(topic.c_str(), payload.c_str(), retain);
}

void publishMqttAvailability(const char *status) {
  StaticJsonDocument<96> document;
  document["device_id"] = deviceId();
  document["status"] = status;
  publishMqttJson(mqttTopic("availability"), document, true);
}

void publishMqttManifest() {
  StaticJsonDocument<384> document;
  document["device_id"] = deviceId();
  document["device_type"] = "audio-recorder";
  document["protocol_version"] = "1.0";
  document["firmware_version"] = kFirmwareVersion;
  JsonArray capabilities = document["capabilities"].to<JsonArray>();
  capabilities.add("record.start");
  capabilities.add("record.stop");
  capabilities.add("status.read");
  publishMqttJson(mqttTopic("manifest"), document, true);
}

void publishMqttState() {
  if (!mqttClient.connected()) {
    return;
  }
  StaticJsonDocument<1536> document;
  addStatus(document);
  document["mqtt_connected"] = mqttClient.connected();
  publishMqttJson(mqttTopic("state"), document, true);
}

void publishMqttEvent(const char *eventType, const String &commandId) {
  StaticJsonDocument<384> document;
  document["event_type"] = eventType;
  document["timestamp"] = isoTimestamp();
  if (!commandId.isEmpty()) {
    document["command_id"] = commandId;
  }
  if (String(eventType) == "recording.stopped") {
    document["file"] = lastCompletedFileName;
  }
  publishMqttJson(mqttTopic("events"), document);
}

int findMqttCommandHistory(const String &commandId) {
  for (uint8_t index = 0; index < kMqttCommandHistorySize; ++index) {
    if (mqttCommandHistory[index].commandId == commandId) {
      return index;
    }
  }
  return -1;
}

void saveMqttCommandHistory() {
  DynamicJsonDocument document(2048);
  JsonArray entries = document.createNestedArray("entries");
  for (const MqttCommandHistoryEntry &entry : mqttCommandHistory) {
    if (entry.commandId.isEmpty()) {
      continue;
    }
    JsonObject item = entries.createNestedObject();
    item["command_id"] = entry.commandId;
    item["capability"] = entry.capability;
    item["status"] = entry.status;
    if (!entry.errorCode.isEmpty()) {
      item["error_code"] = entry.errorCode;
    }
    if (!entry.resultJson.isEmpty()) {
      item["result"] = entry.resultJson;
    }
  }
  String serialized;
  serializeJson(document, serialized);
  Preferences preferences;
  if (!preferences.begin("mqttcmd", false)) {
    return;
  }
  preferences.putString("history", serialized);
  preferences.end();
}

void loadMqttCommandHistory() {
  Preferences preferences;
  if (!preferences.begin("mqttcmd", true)) {
    return;
  }
  const String serialized = preferences.getString("history", "");
  preferences.end();
  if (serialized.isEmpty()) {
    return;
  }

  DynamicJsonDocument document(2048);
  if (deserializeJson(document, serialized) != DeserializationError::Ok) {
    return;
  }
  JsonArray entries = document["entries"].as<JsonArray>();
  if (entries.isNull()) {
    return;
  }
  uint8_t index = 0;
  for (JsonObject item : entries) {
    if (index >= kMqttCommandHistorySize) {
      break;
    }
    mqttCommandHistory[index].commandId = item["command_id"] | "";
    mqttCommandHistory[index].capability = item["capability"] | "";
    mqttCommandHistory[index].status = item["status"] | "";
    if (mqttCommandHistory[index].status.isEmpty()) {
      // Migrate the pre-direct-MQTT NVS layout once without losing idempotency.
      mqttCommandHistory[index].status = item["state"] | "";
    }
    mqttCommandHistory[index].errorCode = item["error_code"] | "";
    mqttCommandHistory[index].resultJson = item["result"] | "";
    if (!mqttCommandHistory[index].commandId.isEmpty()) {
      ++index;
    }
  }
}

void rememberMqttCommand(const String &commandId, const String &capability,
                         const char *status, const char *errorCode,
                         const String &resultJson) {
  int index = findMqttCommandHistory(commandId);
  if (index < 0) {
    for (int cursor = kMqttCommandHistorySize - 1; cursor > 0; --cursor) {
      mqttCommandHistory[cursor] = mqttCommandHistory[cursor - 1];
    }
    index = 0;
  }
  mqttCommandHistory[index].commandId = commandId;
  mqttCommandHistory[index].capability = capability;
  mqttCommandHistory[index].status = status;
  mqttCommandHistory[index].errorCode = errorCode == nullptr ? "" : errorCode;
  mqttCommandHistory[index].resultJson = resultJson;
  saveMqttCommandHistory();
}

void queueMqttAck(const String &commandId, const String &payload) {
  for (PendingMqttAck &pending : pendingMqttAcks) {
    if (pending.commandId == commandId || pending.commandId.isEmpty()) {
      pending.commandId = commandId;
      pending.payload = payload;
      return;
    }
  }
  // Keep the newest bounded set if the device receives more ACK failures than
  // the command history can retain.
  pendingMqttAcks[kMqttCommandHistorySize - 1].commandId = commandId;
  pendingMqttAcks[kMqttCommandHistorySize - 1].payload = payload;
}

void retryPendingMqttAcks() {
  if (!mqttClient.connected()) {
    return;
  }
  const String topic = mqttTopic("acks");
  for (PendingMqttAck &pending : pendingMqttAcks) {
    if (pending.commandId.isEmpty() || pending.payload.isEmpty()) {
      continue;
    }
    if (mqttClient.publish(topic.c_str(), pending.payload.c_str(), false)) {
      pending.commandId = "";
      pending.payload = "";
    }
  }
}

void publishMqttCommandAck(const String &commandId, const char *status,
                           const char *errorCode = nullptr,
                           const String &preservedResultJson = String(),
                           const String &capability = String(),
                           bool remember = true) {
  StaticJsonDocument<2048> document;
  document["protocol_version"] = "1.0";
  document["command_id"] = commandId;
  document["device_id"] = deviceId();
  document["status"] = status;
  if (errorCode != nullptr && errorCode[0] != '\0') {
    document["error_code"] = errorCode;
    document["message"] = errorCode;
  }
  if (!preservedResultJson.isEmpty()) {
    DynamicJsonDocument preserved(1536);
    if (deserializeJson(preserved, preservedResultJson) == DeserializationError::Ok &&
        preserved.is<JsonObject>()) {
      document["result"] = preserved.as<JsonObject>();
    }
  }
  if (!document["result"].is<JsonObject>()) {
    JsonObject result = document.createNestedObject("result");
    addStatus(result);
  }
  String resultJson;
  serializeJson(document["result"], resultJson);
  String payload;
  serializeJson(document, payload);
  if (!mqttClient.connected() ||
      !mqttClient.publish(mqttTopic("acks").c_str(), payload.c_str(), false)) {
    queueMqttAck(commandId, payload);
  }
  if (remember) {
    rememberMqttCommand(commandId, capability, status, errorCode, resultJson);
  }
}

bool hasOnlyMqttCommandFields(JsonObject object) {
  for (JsonPair pair : object) {
    const String key = pair.key().c_str();
    if (key != "protocol_version" && key != "command_id" &&
        key != "device_id" && key != "capability" && key != "params") {
      return false;
    }
  }
  return true;
}

bool isValidMqttCommandId(const String &value) {
  if (value.isEmpty() || value.length() > 64) {
    return false;
  }
  for (size_t index = 0; index < value.length(); ++index) {
    const char character = value[index];
    const bool allowed = (character >= 'A' && character <= 'Z') ||
                         (character >= 'a' && character <= 'z') ||
                         (character >= '0' && character <= '9') ||
                         character == '-' || character == '_';
    if (!allowed) {
      return false;
    }
  }
  return true;
}

void handleMqttCommand(char *topic, uint8_t *payload, unsigned int length) {
  if (String(topic) != mqttTopic("commands")) {
    return;
  }
  if (length == 0 || length > kMqttMaxCommandPayloadBytes) {
    Serial.printf("MQTT command rejected: payload too large (%u bytes)\n", length);
    return;
  }
  StaticJsonDocument<768> document;
  if (deserializeJson(document, payload, length)) {
    return;
  }
  JsonObject command = document.as<JsonObject>();
  if (command.isNull()) {
    return;
  }
  const String commandId = document["command_id"] | "";
  if (!isValidMqttCommandId(commandId)) {
    return;
  }
  if (!hasOnlyMqttCommandFields(command)) {
    publishMqttCommandAck(commandId, "failed", "INVALID_COMMAND");
    return;
  }
  const String targetDeviceId = document["device_id"] | "";
  if (targetDeviceId.isEmpty() || targetDeviceId != deviceId()) {
    publishMqttCommandAck(commandId, "failed", "DEVICE_ID_MISMATCH");
    return;
  }
  const String protocolVersion = document["protocol_version"] | "";
  if (protocolVersion != "1.0") {
    publishMqttCommandAck(commandId, "failed", "UNSUPPORTED_PROTOCOL_VERSION");
    return;
  }
  const String capability = document["capability"] | "";
  const int historyIndex = findMqttCommandHistory(commandId);
  if (historyIndex >= 0 && !mqttCommandHistory[historyIndex].status.isEmpty()) {
    if (!mqttCommandHistory[historyIndex].capability.isEmpty() &&
        mqttCommandHistory[historyIndex].capability != capability) {
      publishMqttCommandAck(commandId, "failed", "DUPLICATE_COMMAND", String(), capability, false);
      return;
    }
    const String &errorCode = mqttCommandHistory[historyIndex].errorCode;
    publishMqttCommandAck(
        commandId,
        mqttCommandHistory[historyIndex].status.c_str(),
        errorCode.isEmpty() ? nullptr : errorCode.c_str(),
        mqttCommandHistory[historyIndex].resultJson,
        mqttCommandHistory[historyIndex].capability);
    return;
  }

  JsonVariant params = document["params"];
  if (params.isNull() || !params.is<JsonObject>() || params.as<JsonObject>().size() != 0) {
    publishMqttCommandAck(commandId, "failed", "INVALID_PARAMS");
    return;
  }

  if (capability == "status.read") {
    publishMqttState();
    publishMqttCommandAck(commandId, "succeeded", nullptr, String(), capability);
    return;
  }
  if (capability == "record.start") {
    if (!startRecording()) {
      publishMqttCommandAck(commandId, "failed", lastRecordingErrorCode.c_str(), String(), capability);
      publishMqttState();
      return;
    }
    publishMqttEvent("recording.started", commandId);
    publishMqttCommandAck(commandId, "succeeded", nullptr, String(), capability);
    publishMqttState();
    return;
  }
  if (capability == "record.stop") {
    if (!stopRecording()) {
      publishMqttCommandAck(commandId, "failed", lastRecordingErrorCode.c_str(), String(), capability);
      publishMqttState();
      return;
    }
    publishMqttEvent("recording.stopped", commandId);
    publishMqttCommandAck(commandId, "succeeded", nullptr, String(), capability);
    publishMqttState();
    return;
  }
  publishMqttCommandAck(commandId, "failed", "CAPABILITY_NOT_SUPPORTED", String(), capability);
}

void onMqttMessage(char *topic, uint8_t *payload, unsigned int length) {
  handleMqttCommand(topic, payload, length);
}

void maintainMqtt() {
#if RECORDER_MQTT_ENABLED
  if (!mqttConfigured()) {
    return;
  }
  if (WiFi.status() != WL_CONNECTED) {
    maintainWifi();
    return;
  }
  if (!mqttClient.connected()) {
    if (millis() < nextMqttRetryAt) {
      return;
    }
    const uint32_t now = millis();
    const String clientId = deviceId() + "-hub";
    const String availabilityTopic = mqttTopic("availability");
    const char offlinePayload[] = "{\"status\":\"offline\"}";
    bool connected = false;
    if (isConfiguredValue(RECORDER_MQTT_USERNAME)) {
      connected = mqttClient.connect(
          clientId.c_str(), RECORDER_MQTT_USERNAME, RECORDER_MQTT_PASSWORD,
          availabilityTopic.c_str(), 1, true, offlinePayload);
    } else {
      connected = mqttClient.connect(
          clientId.c_str(), availabilityTopic.c_str(), 1, true, offlinePayload);
    }
    if (!connected) {
      Serial.printf("MQTT connect failed, state=%d\n", mqttClient.state());
      nextMqttRetryAt = now + mqttReconnectDelayMs;
      mqttReconnectDelayMs = min(
      static_cast<uint32_t>(mqttReconnectDelayMs * 2UL), kMqttReconnectMaxDelayMs);
      return;
    }
    mqttReconnectDelayMs = kMqttReconnectInitialDelayMs;
    nextMqttRetryAt = 0;
    mqttClient.subscribe(mqttTopic("commands").c_str(), 1);
    retryPendingMqttAcks();
    publishMqttAvailability("online");
    publishMqttManifest();
    publishMqttState();
    nextMqttStateAt = millis() + 30000;
    Serial.printf("MQTT connected: %s\n", RECORDER_MQTT_HOST);
  }
  mqttClient.loop();
  if (mqttClient.connected() && millis() >= nextMqttStateAt) {
    publishMqttState();
    nextMqttStateAt = millis() + 30000;
  }
#endif
}

String mdnsHostname() {
  String hostname = deviceId();
  hostname.replace("_", "-");
  return "ai-recorder-" + hostname;
}

String bleDeviceName() {
  return String(kBleDeviceNamePrefix) + " " + deviceId();
}

String minioObjectKey(const String &name) {
  if (isSegmentFileName(name)) {
    // 直播预览分段走独立子路径，backend 的常规发现/任务队列不碰它们。
    return runtimeCloudStorage.recordingsPrefix + deviceId() + "/live/" +
        segmentStem(name) + "/" + name;
  }
  return runtimeCloudStorage.recordingsPrefix + deviceId() + "/" + name;
}

String uploadedMarkerPath(const String &name) {
  return recordingPath(name) + kUploadedMarkerSuffix;
}

String uploadFailedMarkerPath(const String &name) {
  return recordingPath(name) + kUploadFailedMarkerSuffix;
}

bool isUploaded(const String &name) {
  return SD.exists(uploadedMarkerPath(name));
}

bool isUploadFailed(const String &name) {
  return SD.exists(uploadFailedMarkerPath(name));
}

void clearUploadMarkers() {
  if (!sdReady) {
    return;
  }
  sdLock();
  File directory = SD.open(kRecordingsDir);
  if (directory) {
    File entry;
    while ((entry = directory.openNextFile())) {
      const String fullPath = entry.name();
      const int slash = fullPath.lastIndexOf('/');
      const String name = slash >= 0 ? fullPath.substring(slash + 1) : fullPath;
      if (!entry.isDirectory() && name.endsWith(".wav")) {
        SD.remove(uploadedMarkerPath(name));
        SD.remove(uploadFailedMarkerPath(name));
        SD.remove(uploadStatePath(name));
        SD.remove(uploadStateTempPath(name));
      }
      entry.close();
    }
    directory.close();
  }
  sdUnlock();
}

struct UploadResumeState {
  uint32_t fileSize = 0;
  uint16_t completedPartCount = 0;
  String uploadId;
  String completedParts;
};

bool saveUploadResumeState(const String &name, const UploadResumeState &state) {
  sdLock();
  const String temporaryPath = uploadStateTempPath(name);
  const String finalPath = uploadStatePath(name);
  SD.remove(temporaryPath);
  File file = SD.open(temporaryPath, FILE_WRITE);
  if (!file) {
    sdUnlock();
    return false;
  }
  file.println("version=1");
  file.println("size=" + String(state.fileSize));
  file.println("upload_id=" + state.uploadId);
  file.println("parts=" + String(state.completedPartCount));

  size_t cursor = 0;
  while (cursor < state.completedParts.length()) {
    const int next = state.completedParts.indexOf("</Part>", cursor);
    if (next < 0) {
      file.close();
      SD.remove(temporaryPath);
      sdUnlock();
      return false;
    }
    const int etagStart = state.completedParts.indexOf("<ETag>", cursor);
    const int etagEnd = etagStart < 0 ? -1 : state.completedParts.indexOf("</ETag>", etagStart);
    if (etagEnd < 0) {
      file.close();
      SD.remove(temporaryPath);
      sdUnlock();
      return false;
    }
    file.println("etag=" + state.completedParts.substring(etagStart + 6, etagEnd));
    cursor = static_cast<size_t>(next) + strlen("</Part>");
  }
  file.flush();
  file.close();

  // Replacing the small checkpoint only after it is fully flushed keeps a
  // power loss from corrupting the actual WAV. Losing this checkpoint merely
  // causes a safe re-upload instead of data loss.
  SD.remove(finalPath);
  const bool renamed = SD.rename(temporaryPath, finalPath);
  sdUnlock();
  return renamed;
}

bool loadUploadResumeState(const String &name, uint32_t expectedFileSize, UploadResumeState &state) {
  sdLock();
  File file = SD.open(uploadStatePath(name), FILE_READ);
  if (!file) {
    sdUnlock();
    return false;
  }
  int declaredParts = -1;
  while (file.available()) {
    String line = file.readStringUntil('\n');
    line.trim();
    if (line.startsWith("size=")) {
      state.fileSize = static_cast<uint32_t>(line.substring(5).toInt());
    } else if (line.startsWith("upload_id=")) {
      state.uploadId = line.substring(10);
    } else if (line.startsWith("parts=")) {
      declaredParts = line.substring(6).toInt();
    } else if (line.startsWith("etag=")) {
      const String etag = line.substring(5);
      if (etag.isEmpty()) {
        file.close();
        sdUnlock();
        return false;
      }
      ++state.completedPartCount;
      state.completedParts += "<Part><PartNumber>" + String(state.completedPartCount) +
          "</PartNumber><ETag>" + etag + "</ETag></Part>";
    }
  }
  file.close();
  sdUnlock();
  const uint32_t expectedParts = (expectedFileSize + kMultipartPartBytes - 1) / kMultipartPartBytes;
  return state.fileSize == expectedFileSize && !state.uploadId.isEmpty() && declaredParts >= 0 &&
      state.completedPartCount == static_cast<uint16_t>(declaredParts) && state.completedPartCount <= expectedParts;
}

void clearUploadResumeState(const String &name) {
  sdLock();
  SD.remove(uploadStateTempPath(name));
  SD.remove(uploadStatePath(name));
  sdUnlock();
}

bool hasWavHeader(const WavHeader &header) {
  return memcmp(header.riff, "RIFF", 4) == 0 && memcmp(header.wave, "WAVE", 4) == 0 &&
         memcmp(header.fmt, "fmt ", 4) == 0 && memcmp(header.data, "data", 4) == 0 &&
         header.audioFormat == 1 && header.channels == 1 && header.sampleRate == kSampleRate &&
         header.bitsPerSample == 16;
}

bool repairWavHeader(const String &path) {
  File file = SD.open(path, FILE_WRITE);
  if (!file) {
    return false;
  }
  const size_t totalSize = file.size();
  if (totalSize <= sizeof(WavHeader)) {
    file.close();
    return false;
  }
  WavHeader existing;
  file.seek(0);
  if (file.read(reinterpret_cast<uint8_t *>(&existing), sizeof(existing)) != sizeof(existing) || !hasWavHeader(existing)) {
    file.close();
    return false;
  }
  writeWavHeader(file, static_cast<uint32_t>(totalSize - sizeof(WavHeader)));
  file.flush();
  file.close();
  return true;
}

void recoverInterruptedRecordings() {
  File directory = SD.open(kRecordingsDir);
  if (!directory) {
    return;
  }

  File entry;
  while ((entry = directory.openNextFile())) {
    const String fullPath = entry.name();
    const int slash = fullPath.lastIndexOf('/');
    const String name = slash >= 0 ? fullPath.substring(slash + 1) : fullPath;
    const size_t size = entry.size();
    entry.close();

    if (name.endsWith(kRecordingTempSuffix)) {
      const String finishedName = name.substring(0, name.length() - strlen(kRecordingTempSuffix));
      if (!isValidRecordingName(finishedName) || SD.exists(recordingPath(finishedName))) {
        Serial.printf("RECOVERY_SKIP %s\n", name.c_str());
        continue;
      }
      if (repairWavHeader(recordingPath(name)) && SD.rename(recordingPath(name), recordingPath(finishedName))) {
        Serial.printf("RECOVERY_RECORDED %s\n", finishedName.c_str());
      } else {
        Serial.printf("RECOVERY_FAILED %s\n", name.c_str());
      }
      continue;
    }

    // Versions before 0.2.1 wrote directly to .wav and left dataSize at zero
    // after an unexpected reset. Repair those files once, without touching
    // ordinary finalized recordings.
    if (name.endsWith(".wav") && size > sizeof(WavHeader)) {
      File wav = SD.open(recordingPath(name), FILE_READ);
      WavHeader header;
      const bool hasHeader = wav && wav.read(reinterpret_cast<uint8_t *>(&header), sizeof(header)) == sizeof(header);
      wav.close();
      if (hasHeader && hasWavHeader(header) && header.dataSize == 0 && repairWavHeader(recordingPath(name))) {
        Serial.printf("RECOVERY_LEGACY %s\n", name.c_str());
      }
    }
  }
  directory.close();
}

String findNextPendingRecording(bool segmentsOnly = false) {
  File directory = SD.open(kRecordingsDir);
  if (!directory) {
    return "";
  }
  // 分段预览优先：小文件上传快，backend 逐段转写能让文字尽早流出。
  String segmentCandidate;
  String candidate;
  File entry;
  while ((entry = directory.openNextFile())) {
    const String fullPath = entry.name();
    const int slash = fullPath.lastIndexOf('/');
    const String name = slash >= 0 ? fullPath.substring(slash + 1) : fullPath;
    const bool pending = !entry.isDirectory() && name.endsWith(".wav") &&
                         name != currentFileName && !isUploaded(name) &&
                         !isUploadFailed(name) &&
                         (!segmentsOnly || isSegmentFileName(name));
    if (pending) {
      if (isSegmentFileName(name)) {
        if (segmentCandidate.isEmpty()) {
          segmentCandidate = name;
        }
      } else if (candidate.isEmpty()) {
        candidate = name;
      }
    }
    entry.close();
    if (!segmentCandidate.isEmpty() && !candidate.isEmpty()) {
      break;
    }
  }
  directory.close();
  return segmentCandidate.isEmpty() ? candidate : segmentCandidate;
}

bool markUploaded(const String &name) {
  sdLock();
  File marker = SD.open(uploadedMarkerPath(name), FILE_WRITE);
  if (!marker) {
    sdUnlock();
    return false;
  }
  marker.print(isoTimestamp());
  marker.println();
  marker.flush();
  marker.close();
  sdUnlock();
  return true;
}

bool markUploadFailed(const String &name, const String &reason) {
  sdLock();
  File marker = SD.open(uploadFailedMarkerPath(name), FILE_WRITE);
  if (!marker) {
    sdUnlock();
    return false;
  }
  marker.print(isoTimestamp());
  marker.print(" ");
  marker.println(reason);
  marker.flush();
  marker.close();
  sdUnlock();
  return true;
}

bool sha256Bytes(const uint8_t *input, size_t length, uint8_t output[32]) {
  mbedtls_sha256_context context;
  mbedtls_sha256_init(&context);
  const int startResult = mbedtls_sha256_starts_ret(&context, 0);
  const int updateResult = startResult == 0 ? mbedtls_sha256_update_ret(&context, input, length) : startResult;
  const int finishResult = updateResult == 0 ? mbedtls_sha256_finish_ret(&context, output) : updateResult;
  mbedtls_sha256_free(&context);
  return finishResult == 0;
}

bool sha256File(File &file, uint8_t output[32]) {
  uint8_t buffer[kHashBufferBytes];
  mbedtls_sha256_context context;
  mbedtls_sha256_init(&context);
  int result = mbedtls_sha256_starts_ret(&context, 0);
  file.seek(0);
  while (result == 0 && file.available()) {
    const size_t read = file.read(buffer, sizeof(buffer));
    if (read == 0) {
      break;
    }
    result = mbedtls_sha256_update_ret(&context, buffer, read);
  }
  if (result == 0) {
    result = mbedtls_sha256_finish_ret(&context, output);
  }
  mbedtls_sha256_free(&context);
  file.seek(0);
  return result == 0;
}

String hexString(const uint8_t *bytes, size_t length) {
  static const char kHex[] = "0123456789abcdef";
  String result;
  result.reserve(length * 2);
  for (size_t index = 0; index < length; ++index) {
    result += kHex[bytes[index] >> 4];
    result += kHex[bytes[index] & 0x0f];
  }
  return result;
}

String sha256Hex(const String &input) {
  uint8_t hash[32];
  if (!sha256Bytes(reinterpret_cast<const uint8_t *>(input.c_str()), input.length(), hash)) {
    return "";
  }
  return hexString(hash, sizeof(hash));
}

bool hmacSha256(const uint8_t *key, size_t keyLength, const uint8_t *message, size_t messageLength,
                uint8_t output[32]) {
  const mbedtls_md_info_t *info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  if (info == nullptr) {
    return false;
  }
  return mbedtls_md_hmac(info, key, keyLength, message, messageLength, output) == 0;
}

bool hmacSha256(const uint8_t *key, size_t keyLength, const String &message, uint8_t output[32]) {
  return hmacSha256(key, keyLength, reinterpret_cast<const uint8_t *>(message.c_str()), message.length(), output);
}

bool signingKey(const char *date, uint8_t output[32]) {
  const String initialKey = "AWS4" + runtimeCloudStorage.secretKey;
  uint8_t dateKey[32];
  uint8_t regionKey[32];
  uint8_t serviceKey[32];
  return hmacSha256(reinterpret_cast<const uint8_t *>(initialKey.c_str()), initialKey.length(), String(date), dateKey) &&
         hmacSha256(dateKey, sizeof(dateKey), runtimeCloudStorage.region, regionKey) &&
         hmacSha256(regionKey, sizeof(regionKey), "s3", serviceKey) &&
         hmacSha256(serviceKey, sizeof(serviceKey), "aws4_request", output);
}

bool awsTimestamp(char date[9], char timestamp[17]) {
  const time_t now = time(nullptr);
  if (now < 1700000000) {
    return false;
  }
  tm utcTime;
  gmtime_r(&now, &utcTime);
  return strftime(date, 9, "%Y%m%d", &utcTime) == 8 &&
         strftime(timestamp, 17, "%Y%m%dT%H%M%SZ", &utcTime) == 16;
}

bool ensureClock() {
  if (hasValidClock()) {
    return true;
  }
  configTime(0, 0, "pool.ntp.org", "time.google.com");
  const uint32_t deadline = millis() + 10000;
  while (!hasValidClock() && millis() < deadline) {
    delay(250);
  }
  return hasValidClock();
}

void startRecoveryAccessPoint() {
  if (recoveryApActive) {
    return;
  }
  WiFi.mode(WIFI_AP_STA);
  recoveryApActive = WiFi.softAP(kApSsid, kApPassword);
  Serial.printf("Recovery hotspot: %s\n", recoveryApActive ? WiFi.softAPIP().toString().c_str() : "ERROR start failed");
}

void startMdns() {
  if (mdnsStarted || WiFi.status() != WL_CONNECTED) {
    return;
  }
  const String hostname = mdnsHostname();
  if (!MDNS.begin(hostname.c_str())) {
    Serial.println("mDNS start failed.");
    return;
  }
  MDNS.setInstanceName(kDeviceName);
  MDNS.addService(kMdnsService, "tcp", 80);
  MDNS.addServiceTxt(kMdnsService, "tcp", "device_name", kDeviceName);
  MDNS.addServiceTxt(kMdnsService, "tcp", "device_id", deviceId().c_str());
  MDNS.addServiceTxt(kMdnsService, "tcp", "firmware", kFirmwareVersion);
  mdnsStarted = true;
  Serial.printf("mDNS ready: %s.local (_%s._tcp)\n", hostname.c_str(), kMdnsService);
}

void stopWifiWhenIdle() {
  // OTA owns the station interface until it has either failed or rebooted into
  // the new image. The upload task otherwise stops Wi-Fi every idle cycle.
  if (otaUpdateInProgress || wifiAccessLeaseActive() || mqttConfigured()) {
    return;
  }
  if (mdnsStarted) {
    MDNS.end();
    mdnsStarted = false;
  }
  if (recoveryApActive) {
    WiFi.softAPdisconnect(true);
    recoveryApActive = false;
  }
  WiFi.disconnect(true, true);
  WiFi.mode(WIFI_OFF);
  nextWifiRetryAt = 0;
}

bool maintainWifi(bool forceRetry) {
  if (WiFi.status() == WL_CONNECTED) {
    if (recoveryApActive) {
      WiFi.softAPdisconnect(true);
      WiFi.mode(WIFI_STA);
      recoveryApActive = false;
    }
    startMdns();
    return true;
  }
  if (!isConfiguredValue(RECORDER_WIFI_SSID) || (!forceRetry && millis() < nextWifiRetryAt)) {
    return false;
  }

  if (forceRetry && recoveryApActive) {
    // A manual OTA retry must start from a clean station state. Leaving the
    // recovery AP active can prevent ESP32 from re-enabling its STA interface.
    WiFi.softAPdisconnect(true);
    recoveryApActive = false;
    WiFi.mode(WIFI_STA);
    WiFi.disconnect(false, false);
    delay(100);
  } else {
    WiFi.mode(recoveryApActive ? WIFI_AP_STA : WIFI_STA);
  }
#if RECORDER_WIFI_DIAGNOSTICS
  Serial.printf("Wi-Fi scan before connecting to %s:\n", RECORDER_WIFI_SSID);
  const int networkCount = WiFi.scanNetworks(false, true);
  Serial.printf("  scan result count=%d\n", networkCount);
  bool configuredNetworkSeen = false;
  if (networkCount >= 0) {
    for (int index = 0; index < networkCount; ++index) {
      const String ssid = WiFi.SSID(index);
      if (ssid == RECORDER_WIFI_SSID) {
        configuredNetworkSeen = true;
        Serial.printf("  found configured SSID, RSSI=%d dBm, encryption=%d\n", WiFi.RSSI(index), WiFi.encryptionType(index));
      }
    }
    if (!configuredNetworkSeen) Serial.println("  configured SSID was not seen in this scan");
  } else {
    Serial.println("  Wi-Fi scan failed before association");
  }
  WiFi.scanDelete();
#endif
  WiFi.begin(RECORDER_WIFI_SSID, RECORDER_WIFI_PASSWORD);
  // 注意：BLE 连接存在时禁止 WiFi.setSleep(false)——ESP-IDF 共存机制
  // 强制要求 modem sleep，关闭会触发 abort() 崩溃（2026-11-18 实测）。
  // BLE+WiFi 共存的正确做法是让出射频窗口：onConnect 里请求更大的
  // 蓝牙连接间隔 + 录音中降低状态通知频率。
  const uint32_t deadline = millis() + kWifiConnectTimeoutMs;
  while (WiFi.status() != WL_CONNECTED && millis() < deadline) {
    delay(250);
  }
  nextWifiRetryAt = millis() + kWifiRetryIntervalMs;
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("Wi-Fi connected: %s\n", WiFi.localIP().toString().c_str());
    return maintainWifi();
  }
  Serial.println("Wi-Fi connection failed; recovery hotspot remains available.");
  startRecoveryAccessPoint();
  return false;
}

void writeWavHeader(File &file, uint32_t dataSize) {
  WavHeader header;
  header.dataSize = dataSize;
  header.riffSize = 36 + dataSize;
  file.seek(0);
  file.write(reinterpret_cast<const uint8_t *>(&header), sizeof(header));
}

File segmentFile;
String segmentPartPath;
uint16_t segmentIndex = 0;
uint32_t segmentStartedAt = 0;

bool openNextSegmentFile() {
  segmentPartPath = partialRecordingPath(segmentFileName(currentFileName, segmentIndex));
  segmentFile = SD.open(segmentPartPath, FILE_WRITE);
  if (!segmentFile) {
    Serial.println("SEGMENT_OPEN_FAILED");
    segmentPartPath = "";
    return false;
  }
  writeWavHeader(segmentFile, 0);
  segmentFile.flush();
  segmentStartedAt = millis();
  return true;
}

bool finalizeSegmentFile() {
  if (!segmentFile) {
    return false;
  }
  segmentFile.flush();
  const size_t totalSize = segmentFile.size();
  const uint32_t dataSize = totalSize > sizeof(WavHeader) ? totalSize - sizeof(WavHeader) : 0;
  writeWavHeader(segmentFile, dataSize);
  segmentFile.flush();
  segmentFile.close();
  segmentPartPath = "";
  const String finalName = segmentFileName(currentFileName, segmentIndex);
  const String finalPath = recordingPath(finalName);
  SD.remove(finalPath);
  if (!SD.rename(partialRecordingPath(finalName), finalPath)) {
    Serial.printf("SEGMENT_RENAME_FAILED %s\n", finalName.c_str());
    return false;
  }
  if (dataSize < kSampleRate * 2) {
    // 不足 1 秒的尾巴没有转写价值，直接丢弃。
    SD.remove(finalPath);
    Serial.printf("SEGMENT_DROPPED %s bytes=%lu\n", finalName.c_str(),
                  static_cast<unsigned long>(dataSize));
    return true;
  }
  Serial.printf("SEGMENT_READY %s bytes=%lu\n", finalName.c_str(),
                static_cast<unsigned long>(dataSize));
  uploadScanRequested = true;
  return true;
}

// 上传用文件读取适配器：每次 seek/read 都短暂持有 SD 锁，
// 让网络传输期间录音任务可以继续写卡（Stage B 边录边传的核心）。
class UploadFileReader {
 public:
  UploadFileReader() = default;

  bool open(const String &path) {
    sdLock();
    file_ = SD.open(path, FILE_READ);
    const bool ok = static_cast<bool>(file_);
    sdUnlock();
    return ok;
  }
  operator bool() const { return static_cast<bool>(file_); }
  size_t size() {
    sdLock();
    const size_t value = file_.size();
    sdUnlock();
    return value;
  }
  void seek(size_t offset) {
    sdLock();
    file_.seek(offset);
    sdUnlock();
  }
  size_t read(uint8_t *buffer, size_t length) {
    sdLock();
    const size_t count = file_.read(buffer, length);
    sdUnlock();
    return count;
  }
  void close() {
    sdLock();
    file_.close();
    sdUnlock();
  }

 private:
  File file_;
};

bool finalizeRecordingFile() {
  if (!recordingFile) {
    return false;
  }
  // SD writes are buffered. Flush before measuring so the WAV data length includes
  // the final audio block written by the recording task.
  recordingFile.flush();
  const size_t totalSize = recordingFile.size();
  const uint32_t dataSize = totalSize > sizeof(WavHeader) ? totalSize - sizeof(WavHeader) : 0;
  writeWavHeader(recordingFile, dataSize);
  recordingFile.flush();
  recordingFile.close();
  const bool renamed = !currentRecordingPath.isEmpty() &&
      SD.rename(currentRecordingPath, recordingPath(currentFileName));
  if (!renamed) {
    Serial.printf("RECORD_FINALIZE_RENAME_FAILED %s\n", currentFileName.c_str());
    return false;
  }
  currentRecordingPath = "";
  const uint32_t elapsedMs = millis() - recordingStartedAt;
  const float effectiveSampleRate = elapsedMs == 0 ? 0.0f :
      static_cast<float>(dataSize) * 1000.0f / (2.0f * static_cast<float>(elapsedMs));
  Serial.printf("Recording saved: %lu bytes, %lu ms, %.1f Hz\n",
                static_cast<unsigned long>(dataSize),
                static_cast<unsigned long>(elapsedMs),
                effectiveSampleRate);
  return true;
}

String nextFileName() {
  const time_t now = time(nullptr);
  char timestamp[20] = {};
  if (now >= 1700000000) {
    // Keep object-signing timestamps in UTC, but make human-facing recording
    // filenames use fixed China Standard Time (UTC+08:00).
    const time_t beijingTime = now + kBeijingUtcOffsetSeconds;
    tm beijingCalendar;
    gmtime_r(&beijingTime, &beijingCalendar);
    strftime(timestamp, sizeof(timestamp), "%Y%m%d_%H%M%S", &beijingCalendar);
  }
  for (uint16_t attempt = 0; attempt < 1000; ++attempt) {
    const String stem = timestamp[0] == '\0' ? String(millis()) : String(timestamp);
    const String candidate = "REC_" + stem + "_" + String(fileSequence++) + ".wav";
    if (!SD.exists(recordingPath(candidate)) && !SD.exists(partialRecordingPath(candidate))) {
      return candidate;
    }
  }
  return "REC_" + String(millis()) + "_fallback.wav";
}

bool openNextRecordingFile() {
  currentFileName = nextFileName();
  currentRecordingPath = partialRecordingPath(currentFileName);
  recordingFile = SD.open(currentRecordingPath, FILE_WRITE);
  if (!recordingFile) {
    currentRecordingPath = "";
    return false;
  }
  writeWavHeader(recordingFile, 0);
  recordingFile.flush();
  recordingStartedAt = millis();
  lastRecordingFlushAt = recordingStartedAt;
  return true;
}

bool startRecording() {
  lastRecordingErrorCode = "";
  if (!sdReady || !microphoneReady) {
    lastRecordingErrorCode = !sdReady ? "TF_CARD_UNAVAILABLE" : "MICROPHONE_UNAVAILABLE";
    return false;
  }
  if (recording) {
    return true;
  }
  sdLock();
  const bool opened = openNextRecordingFile();
  recording = opened;
  if (opened) {
    pcmByteSwapMode = -1;
    segmentIndex = 1;
    if (!openNextSegmentFile()) {
      // 分段预览是可选增强：失败不影响正式录音。
      Serial.println("SEGMENT_PREVIEW_DISABLED");
    }
    Serial.printf("RECORD_BEGIN %s\n", currentFileName.c_str());
  } else {
    lastRecordingErrorCode = "TF_CARD_WRITE_FAILED";
    Serial.println("RECORD_BEGIN_FAILED");
  }
  sdUnlock();
  return opened;
}

bool stopRecording() {
  lastRecordingErrorCode = "";
  if (!recording) {
    return true;
  }
  recording = false;
  sdLock();
  const String finishedFileName = currentFileName;
  const bool finished = finalizeRecordingFile();
  finalizeSegmentFile();
  segmentFile = File();
  lastCompletedFileName = finished ? finishedFileName : "";
  currentFileName = "";
  if (!finished) {
    lastRecordingErrorCode = "RECORDING_FINALIZE_FAILED";
  }
  Serial.printf("RECORD_END %s %s\n", finishedFileName.c_str(), finished ? "OK" : "FAILED");
  sdUnlock();
  if (finished) {
    uploadScanRequested = true;
  }
  return finished;
}

bool otaConfigured() {
  return isConfiguredValue(RECORDER_OTA_MANIFEST_URL);
}

bool wifiAccessLeaseActive() {
  return wifiAccessRequested && static_cast<int32_t>(wifiAccessUntil - millis()) > 0;
}

String deepSleepBlockerReason() {
  if (bleClientConnected) {
    return "ble_client_connected";
  }
  if (recording) {
    return "recording";
  }
  if (uploadInProgress) {
    return "upload_in_progress";
  }
  if (otaUpdateRequested) {
    return "ota_update_requested";
  }
  if (otaUpdateInProgress) {
    return "ota_update_in_progress";
  }
  if (otaTaskHandle != nullptr) {
    return "ota_task_running";
  }
  if (mqttConfigured()) {
    return "mqtt_enabled";
  }
  if (wifiAccessLeaseActive()) {
    return "wifi_access_lease_active";
  }
  if (sdReady && cloudConfigured() && uploadScanRequested) {
    return "pending_upload_scan";
  }
  if (bleInactiveSince == 0) {
    return "ble_inactive_unknown";
  }
  if (millis() - bleInactiveSince < kBleIdleSleepDelayMs) {
    return "ble_idle_timer";
  }
  return "";
}

void logSleepBlocker(const String &reason) {
  if (reason.isEmpty()) {
    return;
  }
  const uint32_t now = millis();
  if (reason == lastSleepBlockerReason && now - lastSleepBlockerLogAt < 5000) {
    return;
  }
  lastSleepBlockerReason = reason;
  lastSleepBlockerLogAt = now;
  Serial.printf(
      "SLEEP_BLOCKER %s ble=%d rec=%d upload=%d ota_req=%d ota_run=%d ota_task=%d wifi_lease=%d upload_scan=%d ble_idle_ms=%lu cloud=%d sd=%d\n",
      reason.c_str(), bleClientConnected ? 1 : 0, recording ? 1 : 0, uploadInProgress ? 1 : 0,
      otaUpdateRequested ? 1 : 0, otaUpdateInProgress ? 1 : 0, otaTaskHandle != nullptr ? 1 : 0,
      wifiAccessLeaseActive() ? 1 : 0, uploadScanRequested ? 1 : 0,
      bleInactiveSince == 0 ? 0UL : static_cast<unsigned long>(now - bleInactiveSince),
      cloudConfigured() ? 1 : 0, sdReady ? 1 : 0);
}

bool isVersionNewer(const String &candidate, const String &current) {
  int candidateParts[3] = {};
  int currentParts[3] = {};
  if (sscanf(candidate.c_str(), "%d.%d.%d", &candidateParts[0], &candidateParts[1], &candidateParts[2]) < 2 ||
      sscanf(current.c_str(), "%d.%d.%d", &currentParts[0], &currentParts[1], &currentParts[2]) < 2) {
    return false;
  }
  for (size_t index = 0; index < 3; ++index) {
    if (candidateParts[index] != currentParts[index]) return candidateParts[index] > currentParts[index];
  }
  return false;
}

void runOtaUpdate() {
  otaUpdateRequested = false;
  if (!otaConfigured()) { otaStatus = "not_configured"; return; }
  if (recording || uploadInProgress) { otaStatus = "busy"; return; }
  otaUpdateInProgress = true;
  otaStatus = "connecting";
  // A user explicitly requested this update. Do not make them wait for the
  // background upload retry backoff after a prior association failure.
  if (!maintainWifi(true)) { otaStatus = "network_failed"; otaUpdateInProgress = false; return; }
  // The update check runs on its own task, so a BLE START can still be handled
  // while Wi-Fi is associating. Recording always wins over a manual OTA check.
  if (recording || uploadInProgress) { otaStatus = "busy"; otaUpdateInProgress = false; return; }

  HTTPClient manifestHttp;
  if (!manifestHttp.begin(RECORDER_OTA_MANIFEST_URL)) { otaStatus = "manifest_failed"; otaUpdateInProgress = false; return; }
  const int manifestCode = manifestHttp.GET();
  const String manifestBody = manifestCode == HTTP_CODE_OK ? manifestHttp.getString() : "";
  manifestHttp.end();
  StaticJsonDocument<768> manifest;
  if (manifestCode != HTTP_CODE_OK || deserializeJson(manifest, manifestBody)) {
    otaStatus = "manifest_failed"; otaUpdateInProgress = false; return;
  }
  const String version = String(manifest["version"] | "");
  const String firmwareUrl = String(manifest["url"] | "");
  if (!isVersionNewer(version, kFirmwareVersion)) { otaStatus = "up_to_date"; otaUpdateInProgress = false; return; }
  if (firmwareUrl.isEmpty()) { otaStatus = "manifest_invalid"; otaUpdateInProgress = false; return; }
  if (!otaInstallRequested) {
    otaStatus = "update_available_" + version;
    otaUpdateInProgress = false;
    return;
  }

  otaStatus = "downloading_" + version;
  HTTPUpdate updater;
  WiFiClient updateClient;
  const t_httpUpdate_return result = updater.update(updateClient, firmwareUrl, kFirmwareVersion);
  if (result == HTTP_UPDATE_OK) return;  // Successful update reboots before returning.
  otaStatus = "update_failed_" + String(updater.getLastError());
  otaUpdateInProgress = false;
  otaInstallRequested = false;
}

void otaTask(void *parameter) {
  runOtaUpdate();
  otaTaskHandle = nullptr;
  vTaskDelete(nullptr);
}

void startOtaTask() {
  if (otaTaskHandle != nullptr) {
    return;
  }
  if (xTaskCreatePinnedToCore(otaTask, "ota_update", 8192, nullptr, 1, &otaTaskHandle, 0) != pdPASS) {
    otaUpdateRequested = false;
    otaUpdateInProgress = false;
    otaStatus = "update_failed_task";
  }
}

void publishBleStatus(bool force = false) {
  if (bleStatusCharacteristic == nullptr) {
    return;
  }
  const String payload = "recording=" + String(recording ? 1 : 0) +
                         ";device_id=" + deviceId() +
                         ";firmware=" + String(kFirmwareVersion) +
                         ";ota=" + otaStatus +
                         ";seconds=" + String(recording ? (millis() - recordingStartedAt) / 1000 : 0) +
                         ";uploading=" + String(uploadInProgress ? 1 : 0) +
                         ";upload_sent=" + String(uploadBytesSent) +
                         ";upload_total=" + String(uploadBytesTotal) +
                         ";wifi=" + (WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString() :
                                      (wifiAccessLeaseActive() ? "connecting" : "off")) +
                         ";file=" + (recording ? currentFileName :
                                      (uploadInProgress ? uploadingFileName : "")) +
                         ";sd_free_mb=" +
                         String(sdReady ? (SD.totalBytes() - SD.usedBytes()) / 1024 / 1024 : 0) +
                         ";sd_total_mb=" + String(sdReady ? SD.totalBytes() / 1024 / 1024 : 0) +
                         ";cloud=" + String(cloudConfigured() ? "ready" : "missing") +
                         ";cloud_status=" + cloudConfigStatus +
                         ";sleep_blocker=" + deepSleepBlockerReason() +
                         ";cloud_config_revision=" + String(runtimeCloudStorage.revision);
  if (!force && payload == lastBleStatus) {
    return;
  }
  lastBleStatus = payload;
  bleStatusCharacteristic->setValue(payload.c_str());
  if (bleClientConnected) {
    bleStatusCharacteristic->notify();
  }
}

void publishBleFileEvent(JsonDocument &document) {
  if (bleFilesCharacteristic == nullptr || !bleClientConnected) {
    return;
  }
  String payload;
  serializeJson(document, payload);
  bleFilesCharacteristic->setValue(payload.c_str());
  bleFilesCharacteristic->notify();
  delay(8);
}

void publishBleFileError(const char *operation, const String &name, const char *message) {
  StaticJsonDocument<256> document;
  document["t"] = "error";
  document["op"] = operation;
  document["n"] = name;
  document["m"] = message;
  publishBleFileEvent(document);
}

void publishBleFileList(uint16_t cursor) {
  if (!sdReady) {
    publishBleFileError("list", "", "TF card is unavailable");
    return;
  }
  // 扫描 SD 时持锁，发送 BLE 通知时放锁（notify 内含 delay，不能锁着卡等）。
  struct ListedFile {
    String name;
    size_t size;
    time_t lastWrite;
  };
  ListedFile page[kBleFilesPageSize + 1];
  uint16_t pageLength = 0;
  bool more = false;
  sdLock();
  File directory = SD.open(kRecordingsDir);
  if (!directory) {
    sdUnlock();
    publishBleFileError("list", "", "Unable to read recordings directory");
    return;
  }

  uint16_t index = 0;
  File entry;
  while ((entry = directory.openNextFile())) {
    const String fullPath = entry.name();
    const int slash = fullPath.lastIndexOf('/');
    const String name = slash >= 0 ? fullPath.substring(slash + 1) : fullPath;
    const bool sendable = !entry.isDirectory() && name.endsWith(".wav") &&
                          !isSegmentFileName(name) &&
                          (!recording || name != currentFileName);
    if (sendable && index >= cursor) {
      if (pageLength >= kBleFilesPageSize) {
        more = true;
        entry.close();
        break;
      }
      page[pageLength].name = name;
      page[pageLength].size = entry.size();
      page[pageLength].lastWrite = entry.getLastWrite();
      ++pageLength;
    }
    if (sendable) {
      ++index;
    }
    entry.close();
  }
  directory.close();
  sdUnlock();

  for (uint16_t position = 0; position < pageLength; ++position) {
    StaticJsonDocument<256> document;
    document["t"] = "file";
    document["n"] = page[position].name;
    const size_t size = page[position].size;
    document["s"] = size;
    document["d"] = size > sizeof(WavHeader) ? (size - sizeof(WavHeader)) / kBytesPerSecond : 0;
    document["c"] = isoTimestamp(page[position].lastWrite);
    publishBleFileEvent(document);
  }

  StaticJsonDocument<128> end;
  end["t"] = "end";
  end["next"] = cursor + pageLength;
  end["more"] = more;
  publishBleFileEvent(end);
}

void deleteBleFile(const String &name) {
  if (!sdReady || !isValidRecordingName(name)) {
    publishBleFileError("delete", name, "Invalid or unavailable recording");
    return;
  }
  if (recording && name == currentFileName) {
    publishBleFileError("delete", name, "Recording is still active");
    return;
  }
  sdLock();
  const String path = recordingPath(name);
  const bool deleted = SD.exists(path) && SD.remove(path);
  if (deleted) {
    SD.remove(uploadedMarkerPath(name));
    SD.remove(uploadFailedMarkerPath(name));
    if (!isSegmentFileName(name)) {
      // 一并清理该录音的分段预览副本及其上传标记。
      const int dot = name.lastIndexOf('.');
      const String stem = dot >= 0 ? name.substring(0, dot) : name;
      File directory = SD.open(kRecordingsDir);
      if (directory) {
        File entry;
        while ((entry = directory.openNextFile())) {
          const String fullPath = entry.name();
          const int slash = fullPath.lastIndexOf('/');
          const String entryName = slash >= 0 ? fullPath.substring(slash + 1) : fullPath;
          const bool owned = entryName.startsWith(stem + kSegmentMarker) &&
                             entryName.endsWith(".wav");
          entry.close();
          if (owned) {
            SD.remove(recordingPath(entryName));
            SD.remove(uploadedMarkerPath(entryName));
            SD.remove(uploadFailedMarkerPath(entryName));
          }
        }
        directory.close();
      }
    }
  }
  sdUnlock();
  if (!deleted) {
    publishBleFileError("delete", name, "Unable to delete recording");
    return;
  }
  StaticJsonDocument<192> document;
  document["t"] = "deleted";
  document["n"] = name;
  publishBleFileEvent(document);
}

class BleServerCallbacks final : public BLEServerCallbacks {
  void onConnect(BLEServer *server, esp_ble_gatts_cb_param_t *param) override {
    bleClientConnected = true;
    bleInactiveSince = 0;
    bleStatusUpdatePending = true;
    if (server != nullptr && param != nullptr) {
      // BLE+WiFi 共存：请求 120-200ms 连接间隔 + 从机延迟 2，把射频窗口
      // 让给 WiFi——否则 modem sleep 共存调度下 WiFi 反复被 AP 踢出。
      esp_ble_conn_update_params_t params = {};
      memcpy(params.bda, param->connect.remote_bda, sizeof(esp_bd_addr_t));
      params.min_int = 96;   // 120 ms（单位 1.25ms）
      params.max_int = 160;  // 200 ms
      params.latency = 2;
      params.timeout = 2000;
      esp_ble_gap_update_conn_params(&params);
    }
  }

  void onDisconnect(BLEServer *) override {
    bleClientConnected = false;
    bleInactiveSince = millis();
    bleAdvertisingRestartPending = true;
  }
};

class BleControlCallbacks final : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *characteristic) override {
    String command = characteristic->getValue().c_str();
    command.trim();
    String verb = command;
    verb.toUpperCase();
    BleCommand request = {};
    bool enqueue = true;
    if (verb == "START" || verb.startsWith("START:")) {
      request.type = BleCommandType::Start;
      if (verb.startsWith("START:")) {
        const String epochText = command.substring(strlen("START:"));
        request.epoch = static_cast<uint32_t>(strtoul(epochText.c_str(), nullptr, 10));
      }
    } else if (verb == "STOP") {
      request.type = BleCommandType::Stop;
    } else if (verb.startsWith("TIME:")) {
      request.type = BleCommandType::SyncTime;
      const String epochText = command.substring(strlen("TIME:"));
      request.epoch = static_cast<uint32_t>(strtoul(epochText.c_str(), nullptr, 10));
    } else if (verb.startsWith("FILES")) {
      request.type = BleCommandType::ListFiles;
      const int separator = verb.indexOf(':');
      request.cursor = separator >= 0 ? static_cast<uint16_t>(verb.substring(separator + 1).toInt()) : 0;
    } else if (verb.startsWith("DELETE:")) {
      request.type = BleCommandType::DeleteFile;
      // Keep the original file name intact. SD file names are case-sensitive.
      const String name = command.substring(strlen("DELETE:"));
      name.toCharArray(request.fileName, sizeof(request.fileName));
    } else if (verb == "OTA" || verb == "OTA:INSTALL") {
      request.type = BleCommandType::CheckUpdate;
      request.installUpdate = verb == "OTA:INSTALL";
    } else if (verb == "WIFI") {
      request.type = BleCommandType::EnableWifi;
    } else if (command.startsWith("STORAGE:")) {
      enqueue = false;
      handleStorageBleCommand(command);
    } else {
      enqueue = false;
    }
    if (enqueue && bleCommandQueue != nullptr) {
      xQueueSend(bleCommandQueue, &request, 0);
    }
    bleStatusUpdatePending = true;
  }
};

void setupBle() {
  const String name = bleDeviceName();
  BLEDevice::init(name.c_str());
  BLEDevice::setMTU(185);
  BLEServer *bleServer = BLEDevice::createServer();
  bleServer->setCallbacks(new BleServerCallbacks());
  BLEService *service = bleServer->createService(kBleServiceUuid);
  bleControlCharacteristic = service->createCharacteristic(
      kBleControlUuid, BLECharacteristic::PROPERTY_WRITE);
  bleStatusCharacteristic = service->createCharacteristic(
      kBleStatusUuid, BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
  bleFilesCharacteristic = service->createCharacteristic(kBleFilesUuid, BLECharacteristic::PROPERTY_NOTIFY);
  bleStatusCharacteristic->addDescriptor(new BLE2902());
  bleFilesCharacteristic->addDescriptor(new BLE2902());
  bleControlCharacteristic->setCallbacks(new BleControlCallbacks());
  service->start();
  BLEAdvertising *advertising = BLEDevice::getAdvertising();
  // The recorder App filters scans by this service UUID.  Keep it in the
  // advertising packet; without it the device can appear in phone settings
  // but cannot be discovered by the App after an OTA update.
  advertising->addServiceUUID(kBleServiceUuid);
  BLEAdvertisementData scanResponseData;
  // A full per-device name and a 128-bit service UUID do not both fit in the
  // 31-byte primary advertising packet. The App gets the name from this scan
  // response, while the service UUID above keeps discovery reliable.
  scanResponseData.setName(std::string(name.c_str()));
  advertising->setScanResponseData(scanResponseData);
  advertising->setScanResponse(true);
  BLEDevice::startAdvertising();
  bleInactiveSince = millis();
  bleStatusUpdatePending = true;
  Serial.println("BLE control ready.");
}

void handlePendingBleControl() {
  BleCommand command = {};
  if (bleCommandQueue == nullptr || xQueueReceive(bleCommandQueue, &command, 0) != pdTRUE) {
    return;
  }
  if (command.type == BleCommandType::Start) {
    if (command.epoch != 0) {
      if (command.epoch < 1700000000UL || command.epoch > 4102444800UL) {
        Serial.println("BLE START clock rejected: invalid Unix epoch.");
      } else {
        timeval clockValue = {};
        clockValue.tv_sec = static_cast<time_t>(command.epoch);
        if (settimeofday(&clockValue, nullptr) == 0) {
          Serial.printf("BLE START clock synchronized: %lu\n", static_cast<unsigned long>(command.epoch));
        } else {
          Serial.println("BLE START failed to set device clock.");
        }
      }
    }
    if (!startRecording()) {
      Serial.println("BLE START failed.");
    }
  } else if (command.type == BleCommandType::Stop) {
    if (!stopRecording()) {
      Serial.println("BLE STOP failed.");
    }
  } else if (command.type == BleCommandType::SyncTime) {
    if (command.epoch < 1700000000UL || command.epoch > 4102444800UL) {
      Serial.println("BLE TIME rejected: invalid Unix epoch.");
    } else {
      timeval clockValue = {};
      clockValue.tv_sec = static_cast<time_t>(command.epoch);
      if (settimeofday(&clockValue, nullptr) == 0) {
        Serial.printf("BLE clock synchronized: %lu\n", static_cast<unsigned long>(command.epoch));
      } else {
        Serial.println("BLE TIME failed to set device clock.");
      }
    }
  } else if (command.type == BleCommandType::ListFiles) {
    publishBleFileList(command.cursor);
  } else if (command.type == BleCommandType::DeleteFile) {
    deleteBleFile(String(command.fileName));
  } else if (command.type == BleCommandType::CheckUpdate) {
    otaInstallRequested = command.installUpdate;
    otaUpdateRequested = true;
    otaStatus = "requested";
  } else if (command.type == BleCommandType::EnableWifi) {
    // The upload task performs the association off the main/BLE loop.
    wifiAccessRequested = true;
    wifiAccessUntil = millis() + kWifiAccessLeaseMs;
  }
  bleStatusUpdatePending = true;
}

void writeSleepMarker() {
  if (!sdReady) {
    return;
  }
  SD.remove(kSleepMarkerPath);
  File marker = SD.open(kSleepMarkerPath, FILE_WRITE);
  if (!marker) {
    Serial.println("SLEEP_MARKER_WRITE_FAILED");
    return;
  }
  marker.printf("entered_at=%s\nmillis=%lu\n", isoTimestamp().c_str(), static_cast<unsigned long>(millis()));
  marker.flush();
  marker.close();
}

void reportSleepMarker() {
  if (!sdReady || !SD.exists(kSleepMarkerPath)) {
    return;
  }
  File marker = SD.open(kSleepMarkerPath, FILE_READ);
  if (!marker) {
    return;
  }
  const String contents = marker.readString();
  marker.close();
  Serial.printf("LAST_SLEEP_MARKER %s", contents.c_str());
}

void setSdBusHighImpedance() {
  pinMode(kSdCsPin, INPUT);
  pinMode(kSdSckPin, INPUT);
  pinMode(kSdMosiPin, INPUT);
  pinMode(kSdMisoPin, INPUT);
}

void powerOffSdCard() {
  SD.end();
  sdSpi.end();
  sdReady = false;
  setSdBusHighImpedance();
#if RECORDER_SD_POWER_SWITCH
  pinMode(kSdPowerEnPin, OUTPUT);
  digitalWrite(kSdPowerEnPin, LOW);
  delay(kSdPowerDownDelayMs);
#endif
}

void powerOnSdCard() {
#if RECORDER_SD_POWER_SWITCH
  gpio_hold_dis(static_cast<gpio_num_t>(kSdPowerEnPin));
  pinMode(kSdPowerEnPin, OUTPUT);
  digitalWrite(kSdPowerEnPin, HIGH);
  delay(kSdPowerUpDelayMs);
#endif
}

void powerOnMic() {
#ifdef RECORDER_MIC_PWR_EN_GPIO
  gpio_hold_dis(static_cast<gpio_num_t>(kMicPowerEnPin));
  pinMode(kMicPowerEnPin, OUTPUT);
  digitalWrite(kMicPowerEnPin, HIGH);
  // Let the mic rail and DC servo settle before the PDM clock starts.
  delay(10);
#endif
}

void powerOffMicForSleep() {
#ifdef RECORDER_MIC_PWR_EN_GPIO
  pinMode(kMicPowerEnPin, OUTPUT);
  digitalWrite(kMicPowerEnPin, LOW);
  gpio_hold_en(static_cast<gpio_num_t>(kMicPowerEnPin));
#endif
}

void powerDownPeripheralsForDeepSleep() {
  // Stop peripheral traffic before removing TF power. Leaving any SPI signal
  // high while the card is unpowered can feed it through an input protection diode.
  if (audioTaskHandle != nullptr) {
    vTaskSuspend(audioTaskHandle);
  }
  i2s_stop(I2S_NUM_0);
  i2s_driver_uninstall(I2S_NUM_0);
  // A stopped but floating PDM clock can leave the microphone input undefined.
  // Hold it low so the microphone remains quiescent throughout deep sleep.
  pinMode(kMicClockPin, OUTPUT);
  digitalWrite(kMicClockPin, LOW);
  pinMode(kMicDataPin, INPUT);
  // Remove mic rail power entirely through deep sleep (U2 load switch).
  powerOffMicForSleep();

  BLEDevice::stopAdvertising();
  BLEDevice::deinit(true);

  powerOffSdCard();
#if RECORDER_SD_POWER_SWITCH
  gpio_hold_en(static_cast<gpio_num_t>(kSdPowerEnPin));
#endif
  gpio_hold_en(static_cast<gpio_num_t>(kMicClockPin));
  gpio_deep_sleep_hold_en();
  Serial.printf("DEEP_SLEEP_POWER tf_switch=%s tf_en=LOW mic_clk=LOW\n",
                RECORDER_SD_POWER_SWITCH ? "enabled" : "unavailable");
}

[[noreturn]] void startDeepSleep() {
  // The S3 USB-Serial-JTAG service is initialized by Serial.begin().  Unlike
  // the radio stacks, it is not part of the recorder's peripheral shutdown.
  // Close it explicitly. Do not call Serial.flush() before this: when USB is
  // unplugged, flushing USB-Serial-JTAG can block and prevent deep sleep.
  Serial.end();
#if RECORDER_DEEP_SLEEP_DIAGNOSTIC_WAKE_US > 0
  esp_sleep_enable_timer_wakeup(RECORDER_DEEP_SLEEP_DIAGNOSTIC_WAKE_US);
#else
  // The product wakes only by Reset, so no configured wake source must survive
  // into deep sleep. This also protects against libraries enabling one.
  esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_ALL);
#endif
  esp_deep_sleep_start();
}

void enterDeepSleepWhenIdle() {
#if RECORDER_FORCE_SLEEP_TEST
  // Electrical diagnosis only: bypass BLE/upload state so the current reading
  // after this point is the board's true forced-deep-sleep consumption.
  if (millis() < kBleIdleSleepDelayMs) {
    return;
  }
  Serial.println("FORCED_DEEP_SLEEP_TEST");
#if RECORDER_DIRECT_DEEP_SLEEP_TEST
  // Deliberately skip all orderly teardown. This distinguishes a failed
  // teardown call from the actual deep-sleep hardware state after the complete
  // recorder stack has already been initialized.
  // Do not flush USB-Serial-JTAG here. With USB physically unplugged,
  // Serial.flush() can wait indefinitely for the absent host and prevent
  // esp_deep_sleep_start() from ever being reached.
  startDeepSleep();
#else
  stopWifiWhenIdle();
  writeSleepMarker();
  powerDownPeripheralsForDeepSleep();
  delay(20);
  startDeepSleep();
#endif
#else
  // Never power down while an App-requested network lease or OTA operation can
  // still be using the radio or flash. The force-sleep test above is the only
  // profile allowed to bypass these production safety gates.
  const String blocker = deepSleepBlockerReason();
  if (!blocker.isEmpty()) {
    logSleepBlocker(blocker);
    return;
  }
  Serial.printf("BLE idle for %lu seconds; entering deep sleep. Press Reset to wake.\n",
                static_cast<unsigned long>(kBleIdleSleepDelayMs / 1000UL));
  stopWifiWhenIdle();
  writeSleepMarker();
  powerDownPeripheralsForDeepSleep();
  delay(20);
  startDeepSleep();
#endif
}

void normalizePdmPcmByteOrder(uint8_t *buffer, size_t length) {
  if (pcmByteSwapMode < 0) {
    uint64_t nativeEnergy = 0;
    uint64_t swappedEnergy = 0;
    for (size_t offset = 0; offset + 1 < length; offset += sizeof(int16_t)) {
      const uint16_t nativeBits = static_cast<uint16_t>(buffer[offset]) |
                                  (static_cast<uint16_t>(buffer[offset + 1]) << 8);
      const uint16_t swappedBits = static_cast<uint16_t>(buffer[offset + 1]) |
                                   (static_cast<uint16_t>(buffer[offset]) << 8);
      const int32_t nativeSample = static_cast<int16_t>(nativeBits);
      const int32_t swappedSample = static_cast<int16_t>(swappedBits);
      nativeEnergy += static_cast<uint64_t>(nativeSample * nativeSample);
      swappedEnergy += static_cast<uint64_t>(swappedSample * swappedSample);
    }
    if (nativeEnergy != 0 || swappedEnergy != 0) {
      // Byte-order corruption turns normal microphone data into near-full-scale
      // noise. Require a clear energy difference before changing byte order.
      pcmByteSwapMode = swappedEnergy * 4 < nativeEnergy ? 1 : 0;
    }
  }
  if (pcmByteSwapMode != 1) {
    return;
  }
  for (size_t offset = 0; offset + 1 < length; offset += sizeof(int16_t)) {
    const uint8_t first = buffer[offset];
    buffer[offset] = buffer[offset + 1];
    buffer[offset + 1] = first;
  }
}

// Digital gain for the PDM mic path. The custom expansion board's acoustic
// coupling is measurably weaker than the reference board; this compensates.
// 8x keeps shouted peaks (~2-3k raw) well inside int16 with headroom.
#ifndef RECORDER_MIC_DIGITAL_GAIN
#define RECORDER_MIC_DIGITAL_GAIN 1
#endif
constexpr int kMicDigitalGain = RECORDER_MIC_DIGITAL_GAIN;

void applyMicDigitalGain(uint8_t *buffer, size_t length) {
  if (kMicDigitalGain <= 1) {
    return;
  }
  int16_t *samples = reinterpret_cast<int16_t *>(buffer);
  const size_t sampleCount = length / sizeof(int16_t);
  for (size_t i = 0; i < sampleCount; ++i) {
    int32_t amplified = static_cast<int32_t>(samples[i]) * kMicDigitalGain;
    if (amplified > 32767) amplified = 32767;
    if (amplified < -32768) amplified = -32768;
    samples[i] = static_cast<int16_t>(amplified);
  }
}

void audioTask(void *) {
  uint8_t buffer[kAudioBufferBytes];
  while (true) {
    if (!recording) {
      vTaskDelay(pdMS_TO_TICKS(10));
      continue;
    }

    size_t bytesRead = 0;
    const esp_err_t result = i2s_read(
        I2S_NUM_0, buffer, sizeof(buffer), &bytesRead, pdMS_TO_TICKS(100));
    if (result != ESP_OK || bytesRead == 0 || !recording) {
      continue;
    }
    normalizePdmPcmByteOrder(buffer, bytesRead);
    applyMicDigitalGain(buffer, bytesRead);

    sdLock();
    if (recording && recordingFile) {
      const size_t written = recordingFile.write(buffer, bytesRead);
      if (written != bytesRead) {
        Serial.printf("RECORD_WRITE_FAILED expected=%u actual=%u\n", static_cast<unsigned>(bytesRead),
                      static_cast<unsigned>(written));
        lastRecordingErrorCode = "TF_CARD_WRITE_FAILED";
        recording = false;
      } else {
        // 写卡锁内只拷内部 RAM（快、不碰 PSRAM 总线）；攒批后锁外进 PSRAM
        if (liveStreamReady() &&
            liveStagingUsed + bytesRead <= sizeof(liveStagingBuffer)) {
          memcpy(liveStagingBuffer + liveStagingUsed, buffer, bytesRead);
          liveStagingUsed += bytesRead;
        } else if (liveStreamReady()) {
          liveStagingUsed = 0;  // 暂存满：丢弃本批（写卡优先，0.25s 音频代价）
        }
        if (millis() - lastRecordingFlushAt >= kRecordingFlushIntervalMs) {
          // Bound the amount of audio at risk if the battery disappears without
          // allowing the normal stop path to close the file.
          recordingFile.flush();
          lastRecordingFlushAt = millis();
        }
        if (segmentFile) {
          segmentFile.write(buffer, bytesRead);
          if (millis() - segmentStartedAt >= kSegmentSeconds * 1000UL) {
            // 满 60 秒：切出一段已完结的分段副本，继续录下一段。
            finalizeSegmentFile();
            if (recording) {
              segmentIndex += 1;
              if (!openNextSegmentFile()) {
                Serial.println("SEGMENT_PREVIEW_DISABLED");
              }
            }
          }
        }
      }
    }
    sdUnlock();
    // 锁外攒批：4KB 一批进 PSRAM ring，PSRAM 访问不与 SD 写卡同临界区
    if (liveStreamReady() && liveStagingUsed >= 4096) {
      liveRingPush(liveStagingBuffer, liveStagingUsed);
      liveStagingUsed = 0;
    }
  }
}

bool initializeMicrophone() {
  powerOnMic();
  const i2s_config_t config = {
      .mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_RX | I2S_MODE_PDM),
      .sample_rate = kSampleRate,
      .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
      .channel_format = kMicChannelFormat,
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
  if (i2s_set_pin(I2S_NUM_0, &pins) != ESP_OK) {
    return false;
  }
  // 8x followed by the fixed 8x hardware stage gives a 64x PDM clock,
  // producing 16 kHz PCM for the Sense microphone.
  return i2s_set_pdm_rx_down_sample(I2S_NUM_0, I2S_PDM_DSR_8S) == ESP_OK;
}

bool initializeSdCard() {
#if RECORDER_SD_STANDALONE_INIT
  for (uint8_t attempt = 0; attempt < 3; ++attempt) {
    powerOffSdCard();
    delay(kSdPowerCycleOffMs);
    powerOnSdCard();
    sdSpi.begin(kSdSckPin, kSdMisoPin, kSdMosiPin, kSdCsPin);
    if (!SD.begin(kSdCsPin, sdSpi, 20000000) || SD.cardType() == CARD_NONE) {
      Serial.printf("microSD mount attempt %u failed.\n", static_cast<unsigned>(attempt + 1));
      continue;
    }
    if (!SD.exists(kRecordingsDir) && !SD.mkdir(kRecordingsDir)) {
      Serial.printf("microSD directory setup attempt %u failed.\n", static_cast<unsigned>(attempt + 1));
      continue;
    }
    return true;
  }
  powerOffSdCard();
  return false;
#else
  // A failed mount gets a complete power cycle so the card does not remain in
  // an undefined bus state. Keep it powered off after the final failed attempt.
  for (uint8_t attempt = 0; attempt < 3; ++attempt) {
#if RECORDER_SD_POWER_SWITCH
    powerOffSdCard();
    delay(kSdPowerCycleOffMs);
    powerOnSdCard();
#endif
#if RECORDER_SD_LEGACY_INIT
    SD.end();
    sdSpi.end();
    delay(50);
#endif
    pinMode(kSdCsPin, OUTPUT);
    digitalWrite(kSdCsPin, HIGH);
    sdSpi.begin(kSdSckPin, kSdMisoPin, kSdMosiPin, kSdCsPin);
    if (!SD.begin(kSdCsPin, sdSpi, 20000000) || SD.cardType() == CARD_NONE) {
      Serial.printf("microSD mount attempt %u failed.\n", static_cast<unsigned>(attempt + 1));
      continue;
    }
    if (!SD.exists(kRecordingsDir) && !SD.mkdir(kRecordingsDir)) {
      Serial.printf("microSD directory setup attempt %u failed.\n", static_cast<unsigned>(attempt + 1));
      continue;
    }
    return true;
  }
#if RECORDER_SD_POWER_SWITCH
  powerOffSdCard();
#endif
  return false;
#endif
}

void retrySdMountIfNeeded() {
  if (sdReady || recording || millis() < nextSdRetryAt) {
    return;
  }
  nextSdRetryAt = millis() + kSdRetryIntervalMs;
#if RECORDER_SD_BLE_BEFORE_INIT
  setupBle();
#endif

  sdReady = initializeSdCard();
  if (sdReady) {
    Serial.println("microSD remounted.");
    uploadScanRequested = true;
    bleStatusUpdatePending = true;
  }
}

template <typename FileLike>
bool sha256FileRange(FileLike &file, size_t offset, size_t length, uint8_t output[32]) {
  uint8_t buffer[kHashBufferBytes];
  mbedtls_sha256_context context;
  mbedtls_sha256_init(&context);
  int result = mbedtls_sha256_starts_ret(&context, 0);
  size_t remaining = length;
  file.seek(offset);
  while (result == 0 && remaining > 0) {
    const size_t read = file.read(buffer, min(sizeof(buffer), remaining));
    if (read == 0) break;
    remaining -= read;
    result = mbedtls_sha256_update_ret(&context, buffer, read);
  }
  if (result == 0 && remaining == 0) result = mbedtls_sha256_finish_ret(&context, output);
  mbedtls_sha256_free(&context);
  return result == 0 && remaining == 0;
}

String awsAuthorization(const char *method, const String &canonicalUri, const String &canonicalQuery,
                        const String &contentType, const String &payloadHash, const char *date,
                        const char *timestamp) {
  const String host = minioHost();
  const String canonicalHeaders = "content-type:" + contentType + "\n" + "host:" + host + "\n" +
      "x-amz-content-sha256:" + payloadHash + "\n" + "x-amz-date:" + timestamp + "\n";
  const String signedHeaders = "content-type;host;x-amz-content-sha256;x-amz-date";
  const String canonicalRequest = String(method) + "\n" + canonicalUri + "\n" + canonicalQuery + "\n" +
      canonicalHeaders + "\n" + signedHeaders + "\n" + payloadHash;
  const String scope = String(date) + "/" + runtimeCloudStorage.region + "/s3/aws4_request";
  const String stringToSign = "AWS4-HMAC-SHA256\n" + String(timestamp) + "\n" + scope + "\n" + sha256Hex(canonicalRequest);
  uint8_t signature[32];
  if (!signingKey(date, signature) || !hmacSha256(signature, sizeof(signature), stringToSign, signature)) return "";
  return "AWS4-HMAC-SHA256 Credential=" + runtimeCloudStorage.accessKey + "/" + scope +
      ", SignedHeaders=" + signedHeaders + ", Signature=" + hexString(signature, sizeof(signature));
}

String s3QueryEncode(const String &value) {
  static const char hex[] = "0123456789ABCDEF";
  String encoded;
  for (size_t index = 0; index < value.length(); ++index) {
    const uint8_t character = static_cast<uint8_t>(value[index]);
    if ((character >= 'A' && character <= 'Z') || (character >= 'a' && character <= 'z') ||
        (character >= '0' && character <= '9') || character == '-' || character == '_' || character == '.' || character == '~') {
      encoded += static_cast<char>(character);
    } else {
      encoded += '%'; encoded += hex[character >> 4]; encoded += hex[character & 0x0F];
    }
  }
  return encoded;
}

bool beginMinioHttp(HTTPClient &http, WiFiClient &plainClient, WiFiClientSecure &secureClient, const String &url) {
  bool started = false;
  if (runtimeCloudStorage.endpoint.startsWith("https://")) {
    if (runtimeCloudStorage.tlsCaCert.isEmpty()) return false;
    secureClient.setCACert(runtimeCloudStorage.tlsCaCert.c_str());
    started = http.begin(secureClient, url);
  } else {
    started = http.begin(plainClient, url);
  }
  if (started) {
    http.setConnectTimeout(10000);
    http.setTimeout(kMultipartRequestTimeoutMs);
  }
  return started;
}

template <typename ClientType, typename FileLike>
bool uploadPartWithClient(ClientType &client, FileLike &file, size_t offset, size_t length,
                          const String &path, const String &authorization,
                          const String &payloadHash, const char *timestamp, String &etag,
                          uint16_t port) {
  String host = minioHost();
  const int colon = host.lastIndexOf(':');
  if (colon > 0) {
    port = static_cast<uint16_t>(host.substring(colon + 1).toInt());
    host = host.substring(0, colon);
  }
  if (!client.connect(host.c_str(), port, 10000)) return false;
  const String header = "PUT " + path + " HTTP/1.1\r\nHost: " + minioHost() + "\r\nContent-Type: audio/wav\r\nContent-Length: " + String(length) +
      "\r\nx-amz-content-sha256: " + payloadHash + "\r\nx-amz-date: " + timestamp + "\r\nAuthorization: " + authorization + "\r\nConnection: close\r\n\r\n";
  if (client.write(reinterpret_cast<const uint8_t *>(header.c_str()), header.length()) != header.length()) { client.stop(); return false; }
  uint8_t buffer[1024]; size_t remaining = length; size_t sent = 0; uint32_t lastProgress = millis();
  file.seek(offset);
  while (remaining > 0) {
    const size_t read = file.read(buffer, min(sizeof(buffer), remaining));
    if (read == 0) { client.stop(); return false; }
    size_t written = 0;
    while (written < read) {
      const size_t count = client.write(buffer + written, read - written);
      if (count > 0) { written += count; sent += count; remaining -= count; lastProgress = millis(); uploadBytesSent = offset + sent; bleStatusUpdatePending = true; }
      else if (!client.connected() || millis() - lastProgress > 15000) { client.stop(); return false; }
      else delay(10);
    }
  }
  const uint32_t responseDeadline = millis() + kMultipartRequestTimeoutMs;
  String response;
  while (millis() < responseDeadline) {
    while (client.available()) response += static_cast<char>(client.read());
    if (response.indexOf("\r\n\r\n") >= 0 || !client.connected()) break;
    delay(1);
  }
  client.stop();
  const int firstLineEnd = response.indexOf("\r\n");
  const bool ok = firstLineEnd > 0 && response.substring(0, firstLineEnd).indexOf(" 2") >= 0;
  String responseHeaders = response;
  responseHeaders.toUpperCase();
  const int etagStart = responseHeaders.indexOf("ETAG:");
  const int etagEnd = etagStart >= 0 ? response.indexOf("\r\n", etagStart) : -1;
  Serial.printf("MULTIPART_PART_RESPONSE %s etag=%d\n", firstLineEnd > 0 ? response.substring(0, firstLineEnd).c_str() : "missing", etagStart);
  if (!ok || etagEnd < 0) {
    lastPartFailure = "MinIO part response: " + (firstLineEnd > 0 ? response.substring(0, firstLineEnd) : "no HTTP response") +
        (etagEnd < 0 ? " (ETag missing)" : "");
    return false;
  }
  etag = response.substring(etagStart + 5, etagEnd); etag.trim();
  return !etag.isEmpty();
}

template <typename FileLike>
bool uploadPartWithProgress(FileLike &file, size_t offset, size_t length, const String &path,
                            const String &authorization, const String &payloadHash,
                            const char *timestamp, String &etag) {
  const bool tls = runtimeCloudStorage.endpoint.startsWith("https://");
  if (tls) {
    if (runtimeCloudStorage.tlsCaCert.isEmpty()) return false;
    WiFiClientSecure secureClient;
    secureClient.setCACert(runtimeCloudStorage.tlsCaCert.c_str());
    return uploadPartWithClient(
        secureClient, file, offset, length, path, authorization, payloadHash,
        timestamp, etag, 443);
  }
  WiFiClient plainClient;
  return uploadPartWithClient(
      plainClient, file, offset, length, path, authorization, payloadHash,
      timestamp, etag, 80);
}

template <typename FileLike>
bool uploadRecordingToMinioWith(FileLike &file, const String &name) {
  const String objectKey = minioObjectKey(name);
  const String canonicalUri = usesVirtualHostStorage()
      ? "/" + objectKey
      : "/" + runtimeCloudStorage.bucket + "/" + objectKey;
  const size_t totalBytes = file.size();
  uploadBytesTotal = totalBytes;
  lastUploadError = "";
  UploadResumeState resume;
  if (!loadUploadResumeState(name, static_cast<uint32_t>(totalBytes), resume)) {
    clearUploadResumeState(name);
    char date[9] = {};
    char timestamp[17] = {};
    if (!awsTimestamp(date, timestamp)) {
      lastUploadError = "Clock is not valid for S3 signing.";
      file.close();
      return false;
    }
    const String emptyHash = sha256Hex("");
    const String createQuery = "uploads=";
    HTTPClient createHttp; WiFiClient createPlain; WiFiClientSecure createSecure;
    const String createAuth = awsAuthorization("POST", canonicalUri, createQuery, "audio/wav", emptyHash, date, timestamp);
    Serial.println("MULTIPART_CREATE_CONNECT");
    if (createAuth.isEmpty() || !beginMinioHttp(createHttp, createPlain, createSecure, minioEndpoint() + canonicalUri + "?" + createQuery)) {
      lastUploadError = "Unable to start multipart upload."; file.close(); return false;
    }
    createHttp.addHeader("Content-Type", "audio/wav"); createHttp.addHeader("x-amz-content-sha256", emptyHash);
    createHttp.addHeader("x-amz-date", timestamp); createHttp.addHeader("Authorization", createAuth);
    Serial.println("MULTIPART_CREATE_POST");
    const int createStatus = createHttp.POST("");
    Serial.printf("MULTIPART_CREATE_RESPONSE %d\n", createStatus);
    const String createBody = createHttp.getString(); createHttp.end();
    const int uploadIdStart = createBody.indexOf("<UploadId>"); const int uploadIdEnd = createBody.indexOf("</UploadId>");
    if (createStatus < 200 || createStatus >= 300 || uploadIdStart < 0 || uploadIdEnd <= uploadIdStart + 10) {
      lastUploadError = "MinIO multipart creation failed (HTTP " + String(createStatus) + ")."; file.close(); return false;
    }
    resume.fileSize = static_cast<uint32_t>(totalBytes);
    resume.uploadId = createBody.substring(uploadIdStart + 10, uploadIdEnd);
    if (!saveUploadResumeState(name, resume)) {
      lastUploadError = "Unable to save multipart checkpoint.";
      file.close();
      return false;
    }
  } else {
    Serial.printf("MULTIPART_RESUME %s part=%u\n", name.c_str(), resume.completedPartCount + 1);
  }

  const String uploadId = resume.uploadId;
  size_t offset = min(totalBytes, static_cast<size_t>(resume.completedPartCount) * kMultipartPartBytes);
  uint16_t partNumber = resume.completedPartCount + 1;
  uploadBytesSent = offset;
  for (; offset < totalBytes; ++partNumber) {
    const size_t partSize = min(kMultipartPartBytes, totalBytes - offset);
    uint8_t hash[32];
    if (!sha256FileRange(file, offset, partSize, hash)) { lastUploadError = "Unable to hash upload part."; file.close(); return false; }
    const String payloadHash = hexString(hash, sizeof(hash));
    const String query = "partNumber=" + String(partNumber) + "&uploadId=" + s3QueryEncode(uploadId);
    String etag;
    bool partUploaded = false;
    uint32_t attempt = 0;
    while (!partUploaded) {
      ++attempt;
      lastPartFailure = "";
      uploadBytesSent = offset;
      char partDate[9] = {};
      char partTimestamp[17] = {};
      if (!awsTimestamp(partDate, partTimestamp)) { lastUploadError = "Clock is not valid for upload part."; file.close(); return false; }
      const String partAuth = awsAuthorization("PUT", canonicalUri, query, "audio/wav", payloadHash, partDate, partTimestamp);
      partUploaded = !partAuth.isEmpty() && uploadPartWithProgress(
          file, offset, partSize, canonicalUri + "?" + query, partAuth, payloadHash, partTimestamp, etag);
      if (!partUploaded) {
        lastUploadError = lastPartFailure.isEmpty()
            ? "MinIO part " + String(partNumber) + " retry " + String(attempt) + "."
            : lastPartFailure;
        if (lastPartFailure.indexOf(" 404") >= 0) {
          clearUploadResumeState(name);
          lastUploadError = "Multipart session expired; restarting upload.";
          file.close();
          return false;
        }
        const uint32_t retryDelayMs = min(30000UL, attempt * 2000UL);
        delay(retryDelayMs);
      }
    }
    resume.completedParts += "<Part><PartNumber>" + String(partNumber) + "</PartNumber><ETag>" + etag + "</ETag></Part>";
    ++resume.completedPartCount;
    if (!saveUploadResumeState(name, resume)) {
      lastUploadError = "Unable to save multipart checkpoint.";
      file.close();
      return false;
    }
    offset += partSize; uploadBytesSent = offset; bleStatusUpdatePending = true;
  }
  const String completeBody = "<CompleteMultipartUpload>" + resume.completedParts + "</CompleteMultipartUpload>";
  const String completeHash = sha256Hex(completeBody); const String completeQuery = "uploadId=" + s3QueryEncode(uploadId);
  char completeDate[9] = {};
  char completeTimestamp[17] = {};
  if (!awsTimestamp(completeDate, completeTimestamp)) { lastUploadError = "Clock is not valid for multipart completion."; file.close(); return false; }
  HTTPClient completeHttp; WiFiClient completePlain; WiFiClientSecure completeSecure;
  const String completeAuth = awsAuthorization("POST", canonicalUri, completeQuery, "application/xml", completeHash, completeDate, completeTimestamp);
  if (completeAuth.isEmpty() || !beginMinioHttp(completeHttp, completePlain, completeSecure, minioEndpoint() + canonicalUri + "?" + completeQuery)) { lastUploadError = "Unable to complete multipart upload."; file.close(); return false; }
  completeHttp.addHeader("Content-Type", "application/xml"); completeHttp.addHeader("x-amz-content-sha256", completeHash);
  completeHttp.addHeader("x-amz-date", completeTimestamp); completeHttp.addHeader("Authorization", completeAuth);
  const int completeStatus = completeHttp.POST(completeBody); completeHttp.end(); file.close();
  if (completeStatus < 200 || completeStatus >= 300) {
    if (completeStatus == 404) clearUploadResumeState(name);
    lastUploadError = "MinIO multipart completion failed (HTTP " + String(completeStatus) + ").";
    return false;
  }
  if (!markUploaded(name)) {
    lastUploadError = "Upload succeeded but marker write failed.";
    return false;
  }
  clearUploadResumeState(name);
  lastUploadError = "";
  return true;
}

bool uploadRecordingToMinio(const String &name) {
  if (!cloudConfigured()) {
    lastUploadError = "Cloud configuration is incomplete.";
    return false;
  }
  if (!ensureClock()) {
    lastUploadError = "Waiting for NTP clock synchronization.";
    return false;
  }
  // 每次小块读取都短暂持有 SD 锁，网络传输期间不持锁，
  // 录音任务（audioTask）得以继续写卡（边录边传）。
  UploadFileReader file;
  if (!file.open(recordingPath(name)) || file.size() <= sizeof(WavHeader)) {
    lastUploadError = "Unable to open WAV for upload.";
    file.close();
    return false;
  }
  return uploadRecordingToMinioWith(file, name);
}

bool pushLivePcmChunk(const String &device, const String &stem) {
  if (!livePushBuffer) return true;
  const size_t length = liveRingTake(livePushBuffer, kLivePushBytes);
  if (length == 0) {
    return true;
  }
  WiFiClient plainClient;
  const String host = kLiveStreamUrl;
  const int schemeEnd = host.indexOf("//");
  const String rest = schemeEnd > 0 ? host.substring(schemeEnd + 2) : host;
  const int slash = rest.indexOf('/');
  const String authority = slash > 0 ? rest.substring(0, slash) : rest;
  const String path = slash > 0 ? rest.substring(slash) : "/";
  const int colon = authority.lastIndexOf(':');
  const String hostName = colon > 0 ? authority.substring(0, colon) : authority;
  const int port = colon > 0 ? authority.substring(colon + 1).toInt() : 80;
  if (!plainClient.connect(hostName.c_str(), port)) {
    Serial.println("LIVE_PUSH_CONNECT_FAILED");
    return false;
  }
  plainClient.printf(
      "POST %s?device=%s&recording=%s HTTP/1.1\r\n"
      "Host: %s\r\nContent-Type: application/octet-stream\r\n"
      "Content-Length: %u\r\nConnection: close\r\n\r\n",
      path.c_str(), device.c_str(), stem.c_str(), authority.c_str(),
      static_cast<unsigned>(length));
  // 8KB 子块发送 + 块间让步：64KB 连续 PSRAM 读会压总线，干扰 SD 写卡
  for (size_t offset = 0; offset < length; offset += 8192) {
    const size_t chunk = (length - offset) < 8192 ? (length - offset) : 8192;
    plainClient.write(livePushBuffer + offset, chunk);
    vTaskDelay(pdMS_TO_TICKS(2));
  }
  plainClient.flush();
  String statusLine = plainClient.readStringUntil('\n');
  const bool ok = statusLine.indexOf(" 200") > 0;
  plainClient.stop();
  if (!ok) {
    Serial.printf("LIVE_PUSH_HTTP %s\n", statusLine.c_str());
  }
  return ok;
}

void uploadTask(void *) {
  uint32_t lastLivePushAt = 0;
  while (true) {
    if (cloudStorageChangedPending && !recording && !uploadInProgress) {
      clearUploadMarkers();
      cloudStorageChangedPending = false;
      uploadScanRequested = true;
      bleStatusUpdatePending = true;
    }
    if (!sdReady || !cloudConfigured()) {
      stopWifiWhenIdle();
      vTaskDelay(pdMS_TO_TICKS(2000));
      continue;
    }
    const bool liveStreamActive =
        liveStreamReady() && recording && currentFileName.length() > 0;
    const bool livePending =
        liveStreamActive && liveRingAvailable() > 0 &&
        millis() - lastLivePushAt >= kLivePcmPushIntervalMs;
    if (!uploadScanRequested && !livePending) {
      // Keep BLE available without scanning the TF card while idle.
      if (wifiAccessLeaseActive()) {
        maintainWifi(true);
      } else {
        stopWifiWhenIdle();
      }
      vTaskDelay(pdMS_TO_TICKS(2000));
      continue;
    }
    if (livePending || (liveStreamActive && liveRingAvailable() > 0)) {
      // 录音中维持 WiFi：直播推流与分段上传共用网络与重试节奏。
      if (!maintainWifi()) {
        vTaskDelay(pdMS_TO_TICKS(kUploadRetryIntervalMs));
        continue;
      }
      if (livePending) {
        String stem = currentFileName;
        if (stem.endsWith(".wav")) {
          stem = stem.substring(0, stem.length() - 4);
        }
        if (pushLivePcmChunk(deviceId(), stem)) {
          lastLivePushAt = millis();
        } else {
          // 推送失败按上传重试节奏再试，期间 ring 会持续积累最新音频
          lastLivePushAt = millis() - kLivePcmPushIntervalMs +
              kUploadRetryIntervalMs;
        }
      }
    } else if (!uploadScanRequested) {
      continue;
    }
    sdLock();
    const bool liveWhileRecording = kLiveUploadEnabled && recording;
    const String pendingName = findNextPendingRecording(liveWhileRecording);
    sdUnlock();
    // 录音中只放行分段上传；完整录音照旧等录音结束再传。
    if (pendingName.isEmpty() || (recording && !isSegmentFileName(pendingName))) {
      if (liveStreamActive && liveRingAvailable() > 0) {
        // 直播推流进行中：保持 WiFi，等下一次推送/分段（不进 idle 停网）
        vTaskDelay(pdMS_TO_TICKS(100));
        continue;
      }
      // BLE stays available; Wi-Fi is only needed while completed recordings await upload.
      uploadScanRequested = false;
      if (wifiAccessLeaseActive()) {
        maintainWifi(true);
      } else {
        stopWifiWhenIdle();
      }
      vTaskDelay(pdMS_TO_TICKS(2000));
      continue;
    }
    if (!maintainWifi()) {
      vTaskDelay(pdMS_TO_TICKS(kUploadRetryIntervalMs));
      continue;
    }

    sdLock();
    const String pendingNameForUpload = findNextPendingRecording(liveWhileRecording);
    const bool stillAllowed =
        !pendingNameForUpload.isEmpty() &&
        (!recording || isSegmentFileName(pendingNameForUpload));
    sdUnlock();
    if (!stillAllowed) {
      continue;
    }
    uploadingFileName = pendingNameForUpload;
    uploadInProgress = true;
    bleStatusUpdatePending = true;
    Serial.printf("UPLOAD_BEGIN %s\n", pendingNameForUpload.c_str());
    const bool uploaded = uploadRecordingToMinio(pendingNameForUpload);
    const bool unreadableFile = !uploaded && lastUploadError == "Unable to open WAV for upload.";
    if (unreadableFile) {
      const bool marked = markUploadFailed(pendingNameForUpload, lastUploadError);
      Serial.printf("UPLOAD_SKIPPED %s %s\n", pendingNameForUpload.c_str(),
                    marked ? "(unreadable SD file)" : "(unable to write failure marker)");
    }
    if (uploaded) {
      Serial.printf("UPLOAD_DONE %s\n", pendingNameForUpload.c_str());
    } else if (!unreadableFile) {
      Serial.printf("UPLOAD_RETRY %s%s%s\n", pendingNameForUpload.c_str(),
                    lastUploadError.isEmpty() ? "" : " - ",
                    lastUploadError.isEmpty() ? "" : lastUploadError.c_str());
    }
    uploadInProgress = false;
    uploadingFileName = "";
    bleStatusUpdatePending = true;
    vTaskDelay(pdMS_TO_TICKS(uploaded || unreadableFile ? 250 : kUploadRetryIntervalMs));
  }
}

template <typename JsonContainer>
void addStatus(JsonContainer &document) {
  document["device_id"] = deviceId();
  document["device_name"] = kDeviceName;
  document["firmware"] = kFirmwareVersion;
  document["online"] = mqttClient.connected() && WiFi.status() == WL_CONNECTED;
  document["recording"] = recording;
  document["sample_rate"] = kSampleRate;
  document["bit_depth"] = 16;
  document["channels"] = 1;
  document["battery_percent"] = batteryPercent();
  document["sd_total_mb"] = sdReady ? SD.totalBytes() / 1024 / 1024 : 0;
  document["sd_free_mb"] = sdReady ? (SD.totalBytes() - SD.usedBytes()) / 1024 / 1024 : 0;
  document["current_file"] = recording ? currentFileName :
      (uploadInProgress ? uploadingFileName : "");
  document["last_completed_file"] = lastCompletedFileName;
  document["recording_seconds"] = recording ? (millis() - recordingStartedAt) / 1000 : 0;
  document["wifi_rssi_dbm"] = WiFi.RSSI();
  document["wifi_connected"] = WiFi.status() == WL_CONNECTED;
  document["wifi_ip"] = WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString() : "";
  document["mqtt_connected"] = mqttClient.connected();
  document["recovery_ap_active"] = recoveryApActive;
  document["cloud_configured"] = cloudConfigured();
  document["cloud_provider"] = runtimeCloudStorage.provider;
  document["cloud_config_revision"] = runtimeCloudStorage.revision;
  document["cloud_config_status"] = cloudConfigStatus;
  document["upload_in_progress"] = uploadInProgress;
  document["upload_sent_bytes"] = uploadBytesSent;
  document["upload_total_bytes"] = uploadBytesTotal;
  document["last_upload_error"] = lastUploadError;
  document["sleep_blocker"] = deepSleepBlockerReason();
}

void handleStatus() {
  StaticJsonDocument<1024> document;
  addStatus(document);
  sendJson(kHttpOk, document);
}

void handleRecordStart() {
  if (!startRecording()) {
    sendError(kHttpServiceUnavailable, "Unable to start recording. Check microSD card.");
    return;
  }
  handleStatus();
}

void handleRecordStop() {
  if (!stopRecording()) {
    sendError(kHttpInternalServerError, "Unable to finalize recording.");
    return;
  }
  handleStatus();
}

void handleTimeSync() {
  DynamicJsonDocument request(256);
  if (deserializeJson(request, server.arg("plain"))) {
    sendError(kHttpBadRequest, "Invalid JSON body.");
    return;
  }
  const uint64_t epoch = request["epoch"] | 0ULL;
  if (epoch < 1700000000ULL || epoch > 4102444800ULL) {
    sendError(kHttpBadRequest, "Invalid Unix epoch.");
    return;
  }
  timeval clockValue = {};
  clockValue.tv_sec = static_cast<time_t>(epoch);
  if (settimeofday(&clockValue, nullptr) != 0) {
    sendError(kHttpInternalServerError, "Unable to set device time.");
    return;
  }
  handleStatus();
}

void handleFiles() {
  DynamicJsonDocument document(16384);
  JsonArray files = document.createNestedArray("files");
  File directory = SD.open(kRecordingsDir);
  if (!directory) {
    sendError(kHttpInternalServerError, "Unable to read recordings directory.");
    return;
  }
  File entry;
  while ((entry = directory.openNextFile())) {
    if (!entry.isDirectory() && String(entry.name()).endsWith(".wav")) {
      JsonObject file = files.createNestedObject();
      String path = entry.name();
      const int slash = path.lastIndexOf('/');
      file["name"] = slash >= 0 ? path.substring(slash + 1) : path;
      const size_t size = entry.size();
      file["size"] = size;
      file["duration_seconds"] = size > sizeof(WavHeader) ? (size - sizeof(WavHeader)) / kBytesPerSecond : 0;
      file["created_at"] = isoTimestamp(entry.getLastWrite());
    }
    entry.close();
  }
  directory.close();
  sendJson(kHttpOk, document);
}

void handleFileRoute() {
  const String uri = server.uri();
  if (!uri.startsWith("/files/")) {
    sendError(kHttpNotFound, "Not found.");
    return;
  }
  const String oldName = urlDecode(uri.substring(strlen("/files/")));
  if (!isValidRecordingName(oldName)) {
    sendError(kHttpBadRequest, "Invalid file name.");
    return;
  }
  if (recording && oldName == currentFileName && server.method() != HTTP_GET) {
    sendError(kHttpConflict, "Recording is still active.");
    return;
  }
  const String oldPath = recordingPath(oldName);

  if (server.method() == HTTP_GET) {
    File file = SD.open(oldPath, FILE_READ);
    if (!file) {
      sendError(kHttpNotFound, "File not found.");
      return;
    }
    server.sendHeader("Content-Disposition", contentDispositionHeader(oldName));
    server.streamFile(file, "audio/wav");
    file.close();
    return;
  }

  if (server.method() == HTTP_DELETE) {
    if (!SD.exists(oldPath)) {
      sendError(kHttpNotFound, "File not found.");
      return;
    }
    if (!SD.remove(oldPath)) {
      sendError(kHttpInternalServerError, "Unable to delete file.");
      return;
    }
    server.send(kHttpNoContent);
    return;
  }

  if (server.method() == HTTP_PATCH) {
    DynamicJsonDocument request(512);
    if (deserializeJson(request, server.arg("plain"))) {
      sendError(kHttpBadRequest, "Invalid JSON body.");
      return;
    }
    const String newName = request["name"] | "";
    if (!isValidRecordingName(newName)) {
      sendError(kHttpBadRequest, "Invalid target file name.");
      return;
    }
    const String newPath = recordingPath(newName);
    if (!SD.exists(oldPath)) {
      sendError(kHttpNotFound, "File not found.");
      return;
    }
    if (SD.exists(newPath)) {
      sendError(kHttpConflict, "Target file already exists.");
      return;
    }
    if (!SD.rename(oldPath.c_str(), newPath.c_str())) {
      sendError(kHttpInternalServerError, "Unable to rename file.");
      return;
    }
    File renamedFile = SD.open(newPath, FILE_READ);
    StaticJsonDocument<384> response;
    response["name"] = newName;
    response["size"] = renamedFile ? renamedFile.size() : 0;
    renamedFile.close();
    sendJson(kHttpOk, response);
    return;
  }

  sendError(kHttpMethodNotAllowed, "Method not allowed.");
}

void setupWebServer() {
  server.on("/status", HTTP_GET, handleStatus);
  server.on("/record/start", HTTP_POST, handleRecordStart);
  server.on("/record/stop", HTTP_POST, handleRecordStop);
  server.on("/time", HTTP_POST, handleTimeSync);
  server.on("/files", HTTP_GET, handleFiles);
  server.onNotFound(handleFileRoute);
  server.begin();
}

}  // namespace

void setup() {
  Serial.begin(115200);
  loadRuntimeCloudStorage();
  loadMqttCommandHistory();
  gpio_deep_sleep_hold_dis();
  gpio_hold_dis(static_cast<gpio_num_t>(kSdPowerEnPin));
  gpio_hold_dis(static_cast<gpio_num_t>(kMicClockPin));
#if RECORDER_SD_POWER_SWITCH
  pinMode(kSdPowerEnPin, OUTPUT);
  digitalWrite(kSdPowerEnPin, LOW);
#endif
  pinMode(kMicClockPin, OUTPUT);
  digitalWrite(kMicClockPin, LOW);
#if RECORDER_SD_POWER_SWITCH
  powerOffSdCard();
#endif
  Serial.println();
  Serial.println("AI Recording Bean booting...");
  Serial.printf("Firmware: %s, device ID: %s\n", kFirmwareVersion, deviceId().c_str());
  Serial.printf("RESET_REASON %d\n", static_cast<int>(esp_reset_reason()));
  fileMutex = xSemaphoreCreateRecursiveMutex();
  liveRingMutex = xSemaphoreCreateRecursiveMutex();
  if (kLiveStreamEnabled && psramFound()) {
    liveRing = (uint8_t *)heap_caps_malloc(kLiveRingBytes, MALLOC_CAP_SPIRAM);
    livePushBuffer =
        (uint8_t *)heap_caps_malloc(kLivePushBytes, MALLOC_CAP_SPIRAM);
    liveStreamRuntimeReady = liveRing != nullptr && livePushBuffer != nullptr;
    Serial.printf(
        "LIVE_STREAM %s (ring=%u push=%u PSRAM)\n",
        liveStreamRuntimeReady ? "ready" : "disabled",
        static_cast<unsigned>(kLiveRingBytes),
        static_cast<unsigned>(kLivePushBytes));
  } else {
    Serial.println("LIVE_STREAM disabled");
  }
  if (fileMutex == nullptr) {
    Serial.println("ERROR: file mutex allocation failed.");
    while (true) {
      delay(1000);
    }
  }
  bleCommandQueue = xQueueCreate(16, sizeof(BleCommand));
  if (bleCommandQueue == nullptr) {
    Serial.println("ERROR: BLE command queue allocation failed.");
    while (true) {
      delay(1000);
    }
  }

  sdReady = initializeSdCard();
  if (sdReady) {
    reportSleepMarker();
    recoverInterruptedRecordings();
  }
  microphoneReady = initializeMicrophone();
  // Repeat identity after hardware initialization so native-USB hosts that
  // reconnect during reset can still capture an unambiguous boot record.
  Serial.printf("Firmware: %s, device ID: %s\n", kFirmwareVersion, deviceId().c_str());
  Serial.printf("microphone PDM channel: %s\n",
                RECORDER_MIC_RIGHT_CHANNEL ? "right" : "left");
  Serial.printf("hardware profile: %s; TF power switch: %s\n",
                RECORDER_CUSTOM_EXPANSION_BOARD ? "custom expansion board" : "legacy XIAO Sense",
                RECORDER_SD_POWER_SWITCH ? "GPIO40 active-high" : "not controlled");
#ifdef RECORDER_MIC_PWR_EN_GPIO
  Serial.printf("microphone power switch: GPIO%d active-high\n", kMicPowerEnPin);
#else
  Serial.println("microphone power switch: not controlled (hard-wired rail)");
#endif
  Serial.printf("microSD: %s\n", sdReady ? "ready" : "ERROR not detected");
  Serial.printf("microphone: %s\n", microphoneReady ? "ready" : "ERROR initialization failed");
  xTaskCreatePinnedToCore(audioTask, "audio", 8192, nullptr, 2, &audioTaskHandle, 0);

  // Initialize storage and the microphone before bringing up BLE unless the
  // legacy boot-order diagnostic explicitly requests the old sequence.
#if !RECORDER_SD_BLE_BEFORE_INIT
  setupBle();
#endif

  // Bring up the network stack without waiting for a Wi-Fi association.
  if (mqttConfigured()) {
    mqttClient.setServer(RECORDER_MQTT_HOST, RECORDER_MQTT_PORT);
    mqttClient.setCallback(onMqttMessage);
    mqttClient.setBufferSize(3072);
    mqttClient.setKeepAlive(30);
  }
  if (cloudConfigured() || mqttConfigured()) {
    WiFi.mode(WIFI_STA);
  } else {
    startRecoveryAccessPoint();
  }
  setupWebServer();
  xTaskCreatePinnedToCore(uploadTask, "minio_upload", 12288, nullptr, 1, nullptr, 1);

  if (cloudConfigured()) {
    Serial.println("BLE control ready; Wi-Fi upload will reconnect in the background.");
  } else {
    Serial.println("Cloud configuration missing; recovery hotspot is ready.");
  }
  if (mqttConfigured()) {
    Serial.printf("Smart Hub MQTT enabled: %s:%d\n", RECORDER_MQTT_HOST, RECORDER_MQTT_PORT);
  }
  if (recoveryApActive) {
    Serial.printf("Recovery SSID: %s, http://%s\n", kApSsid, WiFi.softAPIP().toString().c_str());
  }
}

void loop() {
  server.handleClient();
  maintainMqtt();
  retrySdMountIfNeeded();
  handlePendingBleControl();
  if (recording && RECORDER_TEST_RECORDING_LIMIT_SECONDS > 0 &&
      millis() - recordingStartedAt >= RECORDER_TEST_RECORDING_LIMIT_SECONDS * 1000UL) {
    stopRecording();
  }
  if (otaUpdateRequested && !recording && !uploadInProgress) {
    startOtaTask();
  }
  if (bleAdvertisingRestartPending) {
    bleAdvertisingRestartPending = false;
    BLEDevice::startAdvertising();
  }
  if (bleStatusUpdatePending || millis() >= nextBleStatusAt) {
    // 录音中状态通知降到 5 秒一次：BLE notify 会占用共存射频窗口，
    // 给 WiFi 推流让路（APP 侧轮询后端 /live/frame 补实时性）。
    const uint32_t bleStatusInterval = recording ? 5000 : 1000;
    nextBleStatusAt = millis() + bleStatusInterval;
    const bool forceStatus = bleStatusUpdatePending;
    bleStatusUpdatePending = false;
    publishBleStatus(forceStatus);
  }
  enterDeepSleepWhenIdle();
  delay(2);
}
