# XIAO ESP32S3 Sense TF Card Power Switch Retrofit

## Scope

This retrofit adds GPIO-controlled power to the microSD card on the existing
XIAO ESP32S3 Sense expansion board. It does not switch the microphone supply.

## Verified Expansion-Board Network

Source inspected:

- `XIAO_ESP32S3_ExpBoard_v1.0_SCH.pdf`
- `XIAO ESP32S3 Exp. Board v1.0.sch`
- `XIAO ESP32S3 Exp. Board v1.0.brd`

The official expansion board has a dedicated SD-card supply path:

```text
XIAO VCC_3V3 -> FB1 (120 ohm ferrite bead, 0402) -> VCC_SD_3V3 -> microSD VDD
```

`VCC_SD_3V3` powers only the microSD card, its pull-ups, ESD diode, and local
100 nF + 1 uF capacitors. The PDM microphone uses the separate `VCC_3V3` net,
so the microphone remains powered by this modification.

`FB1` is at board coordinate `(12.827 mm, 20.574 mm)` and is a 0402 part. On
the official PCB, FB1 pad 1 is the source (`VCC_3V3`) and pad 2 is the SD-side
load (`VCC_SD_3V3`).

## Recommended Circuit

Use a dedicated high-side load switch, not a mechanical switch and not an LED
power module.

Recommended IC: `TPS22919DCKR` (TI, 6-pin SC-70).

Relevant verified characteristics:

- Input voltage: 1.6 V to 5.5 V
- Continuous-current rating: 1.5 A
- ON input is active high
- QOD can discharge the output after power-off

Circuit:

```text
FB1 pad 1 / VCC_3V3 ---------------- TPS22919 IN
FB1 pad 2 / VCC_SD_3V3 ------------- TPS22919 OUT
XIAO GND ---------------------------- TPS22919 GND
XIAO D1 / GPIO2 --------------------- TPS22919 ON
GPIO2 / ON -------------------------- 100 kOhm ---------------- GND
TPS22919 QOD ------------------------ TPS22919 OUT
TPS22919 NC ------------------------- no connection

100 nF: IN to GND, placed near the switch
```

The expansion board already has 100 nF and 1 uF on `VCC_SD_3V3`, so no second
output capacitor is needed on the retrofit board. The 100 kOhm pulldown is
mandatory: it keeps the SD supply off while ESP32-S3 GPIO2 is high-impedance
during reset and deep sleep wake-up.

## Physical Retrofit Procedure

1. Remove `FB1`; do not bridge its pads afterward.
2. Use a small assembled load-switch daughterboard. Do not attempt to solder a
   bare SC-70 package directly onto the Sense expansion board.
3. Solder four short 30 AWG insulated wires:
   - FB1 pad 1 -> switch IN
   - FB1 pad 2 -> switch OUT
   - a nearby GND pad -> switch GND
   - XIAO D1 / GPIO2 -> switch ON
4. Inspect for shorts with a multimeter before reconnecting the battery or USB.
5. With the switch disabled, verify that FB1 pad 2 is not connected to 3.3 V.
6. With the switch enabled, verify that FB1 pad 2 is approximately 3.3 V.
7. Only then install the firmware support and test a short recording.

The FB1 pads are 0402-sized. This is microscope / hot-air work; use a repair
shop or make the daughterboard with an assembled PCB service if suitable tools
are not available.

## Existing Expansion-Board Pads: What Can and Cannot Be Used

The exposed pads on the Sense expansion board are not spare GPIOs.

| Visible pad type | Verified network | Suitable for this retrofit |
| --- | --- | --- |
| `D11` | GPIO42 / PDM microphone clock | No. Used while recording. |
| `D12` | GPIO41 / PDM microphone data | No. Used while recording. |
| Round test pads `TP1`, `TP2`, `TP3`, `TP5` | Camera power rails (VIN, 2.8 V, or 1.8 V) | No. They are supplies, not controllable GPIO. |
| Round test pad `TP4` | GND | Yes, only as the switch GND connection. |
| Camera FPC pins | Camera buses and power rails | No. Removing the camera does not turn them into usable GPIO. |

Use the exposed `D1 / GPIO2` pad on the XIAO main board for the load-switch
ON signal. It is an actually unused GPIO in the current firmware. Use a GND
pad on either board for GND. FB1 remains the correct source/output break point:
without removing FB1 (or cutting its trace), a new switch would be bypassed by
the original direct 3.3 V path.

## Firmware Requirements

GPIO2 must be held low before deep sleep and whenever SD is not in use.

Before SD power-off:

1. Finish every write and close all files.
2. Call `SD.end()` and end the SPI bus.
3. Set CS, SCK, MOSI, and MISO to input / high impedance. CS must not remain
   driven high, otherwise it can back-power an unpowered card through its input
   protection circuit.
4. Drive GPIO2 low and enable its deep-sleep GPIO hold.

Before SD use:

1. Release the GPIO hold.
2. Drive GPIO2 high.
3. Wait at least 10 ms for the card supply to stabilize.
4. Start SPI and mount the card.

The SD rail must be enabled for recording, upload, file listing, file deletion,
and file download. It may be disabled after an upload completes and while the
device is only advertising BLE.

## Expected Result

The earlier forced-deep-sleep measurements showed that the TF / storage branch
accounts for roughly 40 mA while it is powered. The switch removes that branch
in deep sleep. The remaining sleep consumption then depends on the XIAO board,
the load-switch leakage, and the battery protection circuit rather than on the
SD card.

This does not eliminate the card's consumption during recording or upload;
those functions require the card to be powered.
