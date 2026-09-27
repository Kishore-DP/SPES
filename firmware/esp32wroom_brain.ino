// SPES v2 brain firmware — ESP32-WROOM-32D → CLOUD (laptop-free).
//
// On a tap: grab a JPEG from the ESP32-CAM (over the hotspot LAN, by mDNS name),
// record 4 s of mic (INMP441), and POST both as multipart to the Render cloud
// backend over HTTPS. Prints the reply text (X-SPES-Text). The reply BODY is
// 16 kHz mono PCM audio — playing it on a MAX98357A amp is the NEXT step (pins
// reserved below).
//
// Board = "ESP32 Dev Module", Partition = "Huge APP", hold BOOT to upload.
//
// ---- PIN MAP ----
//   INMP441 mic (I2S in):  WS=25, SCK=33, SD=32   (VDD->3V3, GND, L/R->GND)
//   RESERVED MAX98357A amp (I2S out): BCLK=26, LRC=27, DIN=22
//   RESERVED touch-to-talk pad: GPIO4 (touch T0)
//   Trigger now: touch GPIO4  OR  BOOT button (GPIO0)

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ESPmDNS.h>
#include <ESP_I2S.h>

// ---- your hotspot ----
const char *ssid     = "IEatCats";
const char *password = "meow@123";

// ---- cloud backend ----
const char *CLOUD_HOST  = "spes-kbho.onrender.com";
const int   CLOUD_PORT  = 443;
const char *CLOUD_PATH  = "/brain";
const char *SPES_TOKEN  = "";        // set if you enabled SPES_TOKEN on Render
const char *CAM_MDNS    = "spescam"; // the ESP32-CAM's mDNS name (spescam.local)

// ---- mic (INMP441) ----
#define I2S_WS 25
#define I2S_SCK 33
#define I2S_SD 32
I2SClass I2S;
const int SAMPLE_RATE = 16000, RECORD_SEC = 4;
const uint32_t NUM_SAMPLES = (uint32_t)SAMPLE_RATE * RECORD_SEC;
const uint32_t DATA_BYTES = NUM_SAMPLES * 2;

// ---- trigger ----
#define TOUCH_PIN 4          // touch T0
#define TOUCH_THRESH 40      // touchRead below this = touched
#define BOOT_BTN 0

const char *BOUNDARY = "----SPESboundary8x2";

void writeWavHeader(Client &c) {
  uint32_t byteRate = SAMPLE_RATE * 2, chunkSize = 36 + DATA_BYTES;
  uint8_t h[44];
  memcpy(h, "RIFF", 4); h[4]=chunkSize;h[5]=chunkSize>>8;h[6]=chunkSize>>16;h[7]=chunkSize>>24;
  memcpy(h+8, "WAVE", 4); memcpy(h+12, "fmt ", 4);
  h[16]=16;h[17]=0;h[18]=0;h[19]=0; h[20]=1;h[21]=0; h[22]=1;h[23]=0;
  h[24]=SAMPLE_RATE;h[25]=SAMPLE_RATE>>8;h[26]=SAMPLE_RATE>>16;h[27]=SAMPLE_RATE>>24;
  h[28]=byteRate;h[29]=byteRate>>8;h[30]=byteRate>>16;h[31]=byteRate>>24;
  h[32]=2;h[33]=0; h[34]=16;h[35]=0; memcpy(h+36, "data", 4);
  h[40]=DATA_BYTES;h[41]=DATA_BYTES>>8;h[42]=DATA_BYTES>>16;h[43]=DATA_BYTES>>24;
  c.write(h, 44);
}

// Fetch a photo from the ESP32-CAM (local, plain HTTP). Returns malloc'd buffer.
uint8_t *fetchCamPhoto(int &outLen) {
  outLen = 0;
  IPAddress camIP = MDNS.queryHost(CAM_MDNS);
  String url = camIP ? ("http://" + camIP.toString() + "/capture")
                     : (String("http://") + CAM_MDNS + ".local/capture");
  Serial.println("CAM: " + url);
  HTTPClient http; WiFiClient c;
  http.begin(c, url);
  http.setConnectTimeout(6000);
  int code = http.GET();
  if (code != 200) { Serial.printf("  CAM HTTP %d\n", code); http.end(); return NULL; }
  int len = http.getSize();
  if (len <= 0 || len > 60000) { http.end(); return NULL; }
  uint8_t *buf = (uint8_t *)malloc(len);
  if (!buf) { http.end(); return NULL; }
  WiFiClient *s = http.getStreamPtr();
  int got = 0;
  while (http.connected() && got < len) {
    int a = s->available();
    if (a) got += s->readBytes(buf + got, min(a, len - got));
    else delay(1);
  }
  http.end();
  outLen = got;
  Serial.printf("  CAM photo %d bytes\n", got);
  return buf;
}

void recordAndSend() {
  // 1) get a photo from the CAM (optional; needed for READ/CAPTURE)
  int imgLen = 0;
  uint8_t *img = fetchCamPhoto(imgLen);
  bool hasImg = (img && imgLen > 0);

  // 2) build the multipart parts + exact Content-Length
  String p1 = String("--") + BOUNDARY +
    "\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"a.wav\"\r\n"
    "Content-Type: audio/wav\r\n\r\n";
  String p2 = String("--") + BOUNDARY +
    "\r\nContent-Disposition: form-data; name=\"image\"; filename=\"i.jpg\"\r\n"
    "Content-Type: image/jpeg\r\n\r\n";
  String closing = String("--") + BOUNDARY + "--\r\n";

  uint32_t contentLen = p1.length() + (44 + DATA_BYTES) + 2 + closing.length();
  if (hasImg) contentLen += p2.length() + imgLen + 2;

  // 3) connect to the cloud over HTTPS
  WiFiClientSecure sc;
  sc.setInsecure();               // skip cert check (simple; fine here)
  sc.setTimeout(20);
  Serial.println("Connecting to cloud...");
  if (!sc.connect(CLOUD_HOST, CLOUD_PORT)) {
    Serial.println("  cloud connect FAILED");
    if (img) free(img);
    return;
  }

  sc.printf("POST %s HTTP/1.1\r\n", CLOUD_PATH);
  sc.printf("Host: %s\r\n", CLOUD_HOST);
  if (strlen(SPES_TOKEN)) sc.printf("X-SPES-Token: %s\r\n", SPES_TOKEN);
  sc.printf("Content-Type: multipart/form-data; boundary=%s\r\n", BOUNDARY);
  sc.printf("Content-Length: %u\r\n", contentLen);
  sc.printf("Connection: close\r\n\r\n");

  // 4) body: audio part (record mic LIVE while streaming)
  sc.print(p1);
  writeWavHeader(sc);
  Serial.println(">>> RECORDING 4s - speak now <<<");
  int16_t buf[256]; uint32_t sent = 0;
  while (sent < NUM_SAMPLES) {
    int n = 0;
    while (n < 256 && sent < NUM_SAMPLES) {
      int s = I2S.read(); if (s == 0 || s == -1) continue;
      int32_t v = s >> 14; if (v > 32767) v = 32767; if (v < -32768) v = -32768;
      buf[n++] = (int16_t)v; sent++;
    }
    sc.write((uint8_t *)buf, n * 2);
  }
  sc.print("\r\n");

  // image part
  if (hasImg) {
    sc.print(p2);
    sc.write(img, imgLen);
    sc.print("\r\n");
  }
  sc.print(closing);
  if (img) free(img);

  // 5) read the reply headers -> X-SPES-Text (the body is PCM audio, played later)
  Serial.println("Sent. Waiting for the cloud...");
  unsigned long t0 = millis(); String answer = "";
  while (sc.connected() && millis() - t0 < 60000) {
    String line = sc.readStringUntil('\n');
    if (line.startsWith("X-SPES-Text:")) { answer = line.substring(12); answer.trim(); }
    if (line == "\r" || line.length() == 0) break;   // end of headers
  }
  sc.stop();

  String out = "";
  for (int i = 0; i < answer.length(); i++) {
    char ch = answer[i];
    if (ch == '%' && i + 2 < answer.length()) {
      char hx[3] = { answer[i+1], answer[i+2], 0 }; out += (char)strtol(hx, NULL, 16); i += 2;
    } else if (ch == '+') out += ' '; else out += ch;
  }
  Serial.println("SPES said: " + out);
  // TODO (next step): read the PCM body from `sc` and stream it to the
  //   MAX98357A amp on I2S port 1 (BCLK=26, LRC=27, DIN=22).
}

bool triggered() {
  if (digitalRead(BOOT_BTN) == LOW) return true;
  if (touchRead(TOUCH_PIN) < TOUCH_THRESH) return true;
  return false;
}

void setup() {
  Serial.begin(115200); delay(300);
  pinMode(BOOT_BTN, INPUT_PULLUP);

  I2S.setPins(I2S_SCK, I2S_WS, -1, I2S_SD, -1);
  if (!I2S.begin(I2S_MODE_STD, SAMPLE_RATE, I2S_DATA_BIT_WIDTH_32BIT,
                 I2S_SLOT_MODE_MONO, I2S_STD_SLOT_LEFT)) {
    Serial.println("I2S FAILED"); while (1) delay(1000);
  }

  WiFi.begin(ssid, password);
  Serial.print("WiFi connecting");
  while (WiFi.status() != WL_CONNECTED) { delay(400); Serial.print("."); }
  Serial.println("\nWiFi connected: " + WiFi.localIP().toString());
  MDNS.begin("spesbrain");       // so we can resolve the CAM's mDNS name
  Serial.println("Tap the touch pad (or BOOT), speak, release.");
}

void loop() {
  if (triggered()) {
    delay(50);
    recordAndSend();
    Serial.println("Ready (tap to talk again).");
    while (triggered()) delay(10);
  }
}
