// Camera frames over the ESP32-S3 native USB port (USB-Serial/JTAG, 12 Mbps full speed).
// Lets a laptop take the video over the USB cable while its Wi-Fi stays on the internet.
//
// Host -> device, one command per line:
//   S                start streaming JPEG frames
//   X                stop streaming
//   Q                send status (JSON)
//   C <var> <val>    set a sensor value (same names as CameraWebServer's /control), then status
// Device -> host, packets: 4-byte type, uint32 LE payload length, uint32 LE millis(), payload
//   "CSDF"  one JPEG frame
//   "CSDJ"  status JSON
// Only this protocol is sent on the native USB port; logs stay on UART0 (CH340 port).
#include <Arduino.h>
#include "esp_camera.h"
#include "HWCDC.h"

// HWCDCSerial only exists when "USB CDC On Boot" is enabled (which would move Serial/logs to USB),
// so the native port gets its own instance here.
static HWCDC usbCam;

static volatile bool streaming = false;
static uint32_t framesSent = 0;

static bool sendPacket(const char type[4], const uint8_t *data, uint32_t len) {
  uint8_t hdr[12];
  memcpy(hdr, type, 4);
  memcpy(hdr + 4, &len, 4);
  uint32_t ms = millis();
  memcpy(hdr + 8, &ms, 4);
  if (usbCam.write(hdr, sizeof(hdr)) != sizeof(hdr)) {
    return false;
  }
  size_t sent = 0;
  while (sent < len) {  // write() may accept less than asked when the TX buffer is full
    size_t n = usbCam.write(data + sent, len - sent);
    if (n == 0) {
      return false;  // host stopped reading (TX timeout)
    }
    sent += n;
  }
  return true;
}

static void sendStatus() {
  sensor_t *s = esp_camera_sensor_get();
  char json[420];
  int n = snprintf(json, sizeof(json),
                   "{\"framesize\":%u,\"quality\":%u,\"brightness\":%d,\"contrast\":%d,\"saturation\":%d,"
                   "\"ae_level\":%d,\"aec\":%u,\"aec_value\":%u,\"agc\":%u,\"agc_gain\":%u,\"gainceiling\":%u,"
                   "\"awb\":%u,\"wb_mode\":%u,\"vflip\":%u,\"hmirror\":%u,\"pid\":%u,\"streaming\":%d,\"frames\":%lu}",
                   s->status.framesize, s->status.quality, s->status.brightness, s->status.contrast,
                   s->status.saturation, s->status.ae_level, s->status.aec, s->status.aec_value, s->status.agc,
                   s->status.agc_gain, s->status.gainceiling, s->status.awb, s->status.wb_mode, s->status.vflip,
                   s->status.hmirror, s->id.PID, streaming ? 1 : 0, (unsigned long)framesSent);
  sendPacket("CSDJ", (const uint8_t *)json, (uint32_t)n);
}

static int setVar(const char *var, int val) {
  sensor_t *s = esp_camera_sensor_get();
  if (!strcmp(var, "framesize")) {
    return s->pixformat == PIXFORMAT_JPEG ? s->set_framesize(s, (framesize_t)val) : -1;
  }
  if (!strcmp(var, "quality")) return s->set_quality(s, val);
  if (!strcmp(var, "brightness")) return s->set_brightness(s, val);
  if (!strcmp(var, "contrast")) return s->set_contrast(s, val);
  if (!strcmp(var, "saturation")) return s->set_saturation(s, val);
  if (!strcmp(var, "ae_level")) return s->set_ae_level(s, val);
  if (!strcmp(var, "aec")) return s->set_exposure_ctrl(s, val);
  if (!strcmp(var, "aec_value")) return s->set_aec_value(s, val);
  if (!strcmp(var, "agc")) return s->set_gain_ctrl(s, val);
  if (!strcmp(var, "agc_gain")) return s->set_agc_gain(s, val);
  if (!strcmp(var, "gainceiling")) return s->set_gainceiling(s, (gainceiling_t)val);
  if (!strcmp(var, "awb")) return s->set_whitebal(s, val);
  if (!strcmp(var, "wb_mode")) return s->set_wb_mode(s, val);
  if (!strcmp(var, "vflip")) return s->set_vflip(s, val);
  if (!strcmp(var, "hmirror")) return s->set_hmirror(s, val);
  return -1;
}

static void handleCommand(char *line) {
  switch (line[0]) {
    case 'S': streaming = true; break;
    case 'X': streaming = false; break;
    case 'Q': sendStatus(); break;
    case 'C': {
      char var[24];
      int val;
      if (sscanf(line + 1, "%23s %d", var, &val) == 2) {
        setVar(var, val);
      }
      sendStatus();
      break;
    }
  }
}

static void usbCamTask(void *) {
  char line[64];
  size_t n = 0;
  int fails = 0;
  for (;;) {
    while (usbCam.available()) {
      int c = usbCam.read();
      if (c == '\n' || c == '\r') {
        line[n] = 0;
        if (n) {
          handleCommand(line);
        }
        n = 0;
      } else if (n < sizeof(line) - 1) {
        line[n++] = (char)c;
      }
    }
    if (!streaming) {
      vTaskDelay(pdMS_TO_TICKS(10));
      continue;
    }
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) {
      vTaskDelay(pdMS_TO_TICKS(5));
      continue;
    }
    bool ok = sendPacket("CSDF", fb->buf, fb->len);
    esp_camera_fb_return(fb);
    if (ok) {
      framesSent++;
      fails = 0;
    } else if (++fails > 10) {
      streaming = false;  // nobody is reading: stop until the host asks again
      fails = 0;
    }
  }
}

void startUsbStream() {
  usbCam.setRxBufferSize(256);
  usbCam.setTxBufferSize(16384);
  usbCam.setTxTimeoutMs(200);
  usbCam.begin();
  xTaskCreatePinnedToCore(usbCamTask, "usbcam", 6144, NULL, 3, NULL, 0);
}
