# ESP32-S3 Recorder Firmware

Firmware project for the Seeed Studio XIAO ESP32S3 with the custom recording expansion board. It records the onboard PDM microphone as `16 kHz / 16-bit / mono WAV` files to the onboard microSD card, then directly uploads completed files to an S3-compatible object store. The backend scanner writes Markdown to the same object store; a separate local sync task writes notes into Obsidian.

## Hardware Assumptions

- PDM microphone: CLK `GPIO42`, DATA `GPIO41`
- microSD on the custom expansion board: CS `GPIO3`, SCK `GPIO7`, MISO `GPIO8`, MOSI `GPIO9`
- microSD power switch: TPS22919 active-high enable on `GPIO40`
- Wi-Fi: normal operation joins the configured home Wi-Fi or phone hotspot
- Recovery hotspot: `AI-Recording-Bean` / `recordingbean`, only enabled while Wi-Fi is unavailable or configuration is missing
- BLE: `AI Recording Bean` publishes a control service for nearby phone start/stop commands; it never carries WAV audio

This version intentionally has no physical recording button. Recording starts and stops only through the phone App's BLE controls. `GPIO0` remains reserved for the board's BOOT/download function, and `GPIO1` is left unused so a floating pin cannot trigger a recording after flashing or reset.

## Configure Wi-Fi And The Initial S3 Store

Before building, copy `src/device_config.example.h` to `src/device_config.h` and fill in the Wi-Fi and initial S3-compatible store values. `device_config.h` is ignored by Git so secrets are not committed. After the firmware is running, the Android App can replace the S3 configuration over BLE without rebuilding.

```bash
cd firmware/esp32s3_recorder
cp src/device_config.example.h src/device_config.h
```

- `RECORDER_MINIO_ENDPOINT` is the initial S3 API endpoint, such as `http://192.168.x.x:30900`; do not append the bucket name.
- The target bucket must already exist. WAV objects are uploaded under `recordings/<device_id>/` by default. Replacing only the expansion board does not change the XIAO identity: the current main board remains `bean_ye_2`. The production profile checks known XIAO MACs first and gives any genuinely new XIAO its own eFuse-derived identity.
- The endpoint must be reachable from the board's Wi-Fi. A MinIO server running only on an unavailable laptop cannot receive uploads.
- For HTTPS, set `RECORDER_MINIO_USE_TLS` to `1` and set `RECORDER_MINIO_TLS_CA_CERT`. The firmware refuses HTTPS uploads without a CA certificate. The same CA PEM is required when HTTPS is configured from the phone.
- ESP32-S3 only supports 2.4 GHz Wi-Fi. Enable a compatible 2.4 GHz mode when using a phone hotspot.

The device signs uploads using the configured MinIO access key. For a production deployment, replace this direct long-lived credential with a device-scoped key or a short-lived presigned-upload service.

## Build And Upload

Install PlatformIO, then from this folder run:

```bash
pio run
pio run --target upload
pio device monitor
```

The default environment is `xiao_esp32s3_custom_board_release_038`. It enables the custom-board GPIO40 TF power switch, the T3902 right PDM channel, and firmware `0.3.8`. The hardware profile does not contain a fixed device name. The original `xiao_esp32s3_sense` profile remains available only as an explicit legacy build.

The purchased 8 GB microSD card must be formatted as FAT32. The firmware creates a `/recordings` directory automatically. At the current WAV format, it holds roughly 60-70 hours before file-system overhead.

At boot, the firmware powers the TF card through `GPIO40`, waits for the supply to settle, and then mounts it. A failed mount is retried with a complete power cycle. Before deep sleep it closes the file system, stops SPI, changes all four TF signal pins to high impedance, switches off TF power, and holds `GPIO40` low throughout deep sleep to prevent leakage or signal-pin back-powering. It also holds the PDM clock low while the microphone is idle. OTA work and an App-requested Wi-Fi access lease block production deep sleep.

For electrical measurement only, build and flash `xiao_esp32s3_custom_board_force_sleep_test_038`. It enters forced deep sleep after 5 seconds and prints `DEEP_SLEEP_POWER` immediately before sleeping. This diagnostic image bypasses normal BLE, upload, OTA, and Wi-Fi lease checks and must be replaced with the production image after measurement.

## Direct Upload Behavior

1. Tap start in the connected phone App. The WAV is written only to microSD while recording.
2. Tap stop in the App to finalize the WAV header and enqueue the file.
3. The background task connects to Wi-Fi, synchronizes NTP time for S3 Signature V4, and uploads the file to `recordings/<device_id>/<filename>.wav` in the configured S3-compatible store. Human-facing recording names use Beijing time (UTC+08:00); S3 request signing remains UTC.
4. A successful upload creates a local `.uploaded` marker. The WAV remains on the SD card.
5. Upload failures preserve the WAV and retry every 10 seconds. Existing unmarked WAV files are also queued after a reboot.

## Unexpected Power Loss Recovery

- While recording, audio is first written as `*.wav.part`, and the TF card is flushed at most every two seconds. A normal stop repairs the WAV length header and renames it to `*.wav`.
- If the board resets or the battery runs out during recording, the next boot repairs the WAV header from the bytes already on the TF card and promotes the `.part` file to a normal `.wav`, ready for upload. The final unflushed interval can be lost; the rest of the recording is retained.
- Multipart upload checkpoints (`UploadId` and confirmed ETags) are stored beside the WAV on the TF card after every successful 5 MB part. After a reboot, the uploader resumes from the first unconfirmed part. If MinIO has expired the multipart session, the firmware safely starts that WAV again from the beginning.
- This does not make an abruptly unplugged microSD card electrically safe. FAT32 and the card controller can still corrupt the current write during physical power removal. Keep the battery connection secure; a future low-voltage stop requires adding a calibrated battery-voltage sensing circuit because this board has no battery ADC in the current design.

## BLE Control

The phone uses BLE service `d3b10000-8a1f-4e23-9d46-5e6d0f434f01` for nearby
recording control, independently of the phone's Wi-Fi connection. The control
characteristic accepts UTF-8 `START`, `START:<unix_epoch>`, `STOP`, and `STATUS`; it also
accepts the storage configuration transaction `STORAGE:BEGIN`, repeated
`STORAGE:CHUNK`, and `STORAGE:COMMIT`. The status
characteristic notifies `recording`, `seconds`, `uploading`, `file`,
`sd_free_mb`, `sd_total_mb`, `cloud`, `cloud_status`, and `cloud_config_revision`.
WAV files are never sent over BLE. The ESP32 continues to use its own configured
Wi-Fi or recovered hotspot path to upload queued recordings to the selected S3-compatible store.

When connected to Wi-Fi, the firmware advertises `_ai-recorder._tcp` with
`device_id` in its TXT record. Its mDNS hostname includes the device ID suffix,
so multiple boards do not collide on one LAN.

## Smart Hub MQTT Integration

The normal production environment keeps Smart Hub MQTT disabled. The explicit
`xiao_esp32s3_custom_board_agent_hub_test` environment enables MQTT and keeps the
existing recording, BLE, SD-card, MinIO, and OTA paths intact.

The test image publishes and subscribes to:

```text
agentos/devices/{device_id}/manifest
agentos/devices/{device_id}/availability
agentos/devices/{device_id}/state
agentos/devices/{device_id}/events
agentos/devices/{device_id}/commands
agentos/devices/{device_id}/acks
```

The current recorder has no physical recording button, so its capabilities are:

```text
record.start
record.stop
status.read
```

For the AgentOS direct-MQTT test image, the broker host is `192.168.0.140`,
the MQTT username is `bean_ye_1`, and the password must be supplied through the
private device configuration provided by the RK3588 administrator. Do not put
that password in this README, PlatformIO project files, source code, logs, or
the repository.

Example command:

```json
{
  "protocol_version": "1.0",
  "command_id": "cmd-001",
  "device_id": "bean_ye_1",
  "capability": "record.start",
  "params": {}
}
```

The device acknowledges `running` and then `succeeded` or `failed`. It publishes
the recorder status as retained state and emits `recording.started` or
`recording.stopped` events. When MQTT is enabled, the firmware keeps Wi-Fi awake
and blocks production deep sleep so the device can receive commands; this has a
real battery-life cost and is why the production profile remains unchanged.

Every acknowledgement includes `protocol_version`, `command_id`, `device_id`,
`status`, and a `result` status
snapshot. The snapshot includes `recording`, `online`, `wifi_connected`,
`mqtt_connected`, `sd_total_mb`, `sd_free_mb`, `current_file`, and
`last_completed_file`. Recording failures use stable `error_code` values such as
`TF_CARD_UNAVAILABLE`, `MICROPHONE_UNAVAILABLE`, `TF_CARD_WRITE_FAILED`, and
`RECORDING_FINALIZE_FAILED`. The firmware requires a matching `device_id`, a
bounded `command_id`, an empty `params` object, and rejects unknown command
fields.

The last eight command results are persisted in ESP32 NVS. Receiving a duplicate
`command_id` returns the saved result instead of starting or stopping recording
again. This history is for hardware idempotency; `request_id` remains an
RK3588/Bridge concern and must not be substituted into the device protocol.

On the development Mac, start the LAN broker and hub from the separate hub
project:

```bash
cd "/Users/jh-05/Documents/ChatGPT/外接大脑/智能中枢"
/opt/homebrew/sbin/mosquitto -c config/mosquitto.lan.dev.conf -v
python3 run.py --mqtt-host 127.0.0.1 --mqtt-port 1883
```

Then build the test image from this firmware project:

```bash
cd "/Users/jh-05/Documents/smart recording beam/firmware/esp32s3_recorder"
pio run -e xiao_esp32s3_custom_board_agent_hub_test
pio run -e xiao_esp32s3_custom_board_agent_hub_test --target upload
pio device monitor
```

The test build currently points at the development Mac's LAN address
the RK3588 LAN broker (for example `192.168.0.140:1883`). Change the private
build configuration before using another network. The recorder does not use
the old HTTP-MQTT Bridge address or port.

The cloud scanner `backend/scanner.py` watches `recordings/`, performs transcription,
and creates Markdown at matching `notes/<device_id>/` paths. The Mac that owns
the Vault runs `backend/obsidian_sync.py` to pull notes locally.

## Implemented API

- `GET /status` (includes stable `device_id`)
- `POST /record/start`
- `POST /record/stop`
- `GET /files`
- `GET /files/<name>`
- `DELETE /files/<name>`
- `PATCH /files/<name>` with `{ "name": "new_name.wav" }`

The API contract is shared with [the desktop upper-computer](../../host_app/docs/device_api.md).

## Remaining Real-Board Verification

- The custom board has passed the independent TF, PDM activity, and production boot checks. A real WAV must still be recorded and listened to; electrical PDM activity alone cannot prove audio quality.
- The PDM path is written as `16-bit / little-endian WAV`; the firmware auto-detects the byte order on each new recording and normalizes it before saving. Do not remove this check without recording and listening to a real WAV.
- `START:<unix_epoch>` is used by the Android app to synchronize the clock before creating a WAV, so phone-started recordings receive Beijing-time filenames even without Wi-Fi. The uploader waits for NTP time because S3 Signature V4 requires an accurate clock.
- Battery percentage is returned as `-1` until a final battery voltage divider and ADC calibration are added.
- Measure `VCC_SD_3V3` and total system current during deep sleep, then verify Reset wake, TF remount, BLE advertising, and preservation of existing files. No fixed current threshold is claimed before the first measurement.
