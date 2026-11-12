#pragma once

// Copy this file to device_config.h and fill in your own values.
// device_config.h is intentionally ignored because it contains secrets.

#define RECORDER_WIFI_SSID "YOUR_WIFI_OR_PHONE_HOTSPOT"
#define RECORDER_WIFI_PASSWORD "YOUR_WIFI_PASSWORD"

// AgentOS direct MQTT is disabled in the production example. Enable it through
// a dedicated PlatformIO test environment or a private device configuration.
#define RECORDER_MQTT_ENABLED 0
#define RECORDER_MQTT_HOST "<RK3588_LAN_IP>"
#define RECORDER_MQTT_PORT 1883
#define RECORDER_MQTT_USERNAME "<MQTT_USERNAME>"
#define RECORDER_MQTT_PASSWORD "<MQTT_PASSWORD>"

// Optional stable device ID for MinIO paths and device discovery. Leave empty
// to derive an ID from the ESP32 eFuse MAC.
#define RECORDER_DEVICE_ID ""

// The MinIO endpoint must use path-style S3 requests and must not include a
// bucket name. For a local MinIO server this is commonly an http:// LAN URL.
#define RECORDER_MINIO_ENDPOINT "http://192.168.0.111:30900"
#define RECORDER_MINIO_BUCKET "recordingbean"
#define RECORDER_MINIO_ACCESS_KEY "YOUR_MINIO_ACCESS_KEY"
#define RECORDER_MINIO_SECRET_KEY "YOUR_MINIO_SECRET_KEY"
#define RECORDER_MINIO_REGION "us-east-1"

// Set to 1 for HTTPS. In that case provide the server CA certificate below.
// Keeping this empty makes HTTPS uploads fail closed rather than disabling
// certificate validation.
#define RECORDER_MINIO_USE_TLS 0
#define RECORDER_MINIO_TLS_CA_CERT ""

// OTA test/deployment manifest. Use HTTPS and a signed manifest in production.
#define RECORDER_OTA_MANIFEST_URL ""
