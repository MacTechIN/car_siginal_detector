# ESP32-S3 camera firmware (CameraWebServer, modified)

Board: ESP32-S3 N16R8 + OV3660, pin map `CAMERA_MODEL_ESP32S3_EYE`. Based on the Arduino-ESP32 3.3.12
`CameraWebServer` example. Changes:
- **USB video** (`usb_stream.cpp`): JPEG frames and sensor commands over the board's *native* USB port
  (USB-Serial/JTAG, appears as `303A:1001`, e.g. COM7). Packets: 4-byte type (`CSDF` JPEG / `CSDJ` status
  JSON) + uint32 length + uint32 millis + payload; commands `S` start, `X` stop, `Q` status,
  `C <var> <val>`. Measured 2026-09-25: XGA 27.7 fps, UXGA 11.3 fps, ~650 KB/s ceiling. Logs stay on the
  CH340 port (UART0). PC side: `csd/usb_camera.py`.
- Wi-Fi: known networks (`WIFI_SSID`, optional `WIFI_SSID2`) 12 s each; otherwise opens its own access point
  `CSD-CAM` / `csdcam1234` at 192.168.4.1 (used in the car). mDNS name `esp32cam`.
- `camera_config_t config = {}`: the example leaves the struct uninitialised; stack garbage caused
  `frame buffer malloc failed` once the code around it changed.
- Prints PSRAM status before camera init.

Build and upload (this PC: Korean user path breaks the Xtensa linker, so an ASCII core copy is used;
see HANDOVER.md 5장):

```powershell
cp wifi_secrets.example.h wifi_secrets.h   # then edit
$env:ARDUINO_DIRECTORIES_DATA="C:\arduino-esp32"
$u="https://espressif.github.io/arduino-esp32/package_esp32_index.json"
$fqbn="esp32:esp32:esp32s3:PSRAM=opi,FlashSize=16M,FlashMode=qio,PartitionScheme=custom,CDCOnBoot=default,UploadSpeed=230400"
arduino-cli compile --additional-urls $u -b $fqbn --build-path C:\Users\Public\esp32cam_build\cws .
arduino-cli upload  --additional-urls $u -p COM6 -b $fqbn --input-dir C:\Users\Public\esp32cam_build\cws
```

Upload through the board's CH340 USB port (auto reset). To test the access point at home, put `-DFORCE_AP`
in a `build_opt.h` next to the sketch (the Arduino-ESP32 way to add compiler flags), build, then delete
the file.
