// SPES ESP32-WROOM-32D "brain" firmware — INMP441 mic -> laptop /brain.
// Standalone sketch (no external includes). Board = "ESP32 Dev Module",
// Partition Scheme = "Huge APP", hold BOOT to upload.
//
// Hold BOOT, speak (a question / "read this" / "capture this" / "navigate to..."
// / "SOS"), release. It streams the recording to the laptop's /brain endpoint;
// the laptop classifies + answers and plays the audio on the LAPTOP.
//
// INMP441 wiring: VDD->3V3 (NOT 5V), GND->GND, L/R->GND, WS->25, SCK->33, SD->32.
// Set ssid/password to your WiFi and HOST to the laptop's IP on that network.

#include <WiFi.h>
#include <ESP_I2S.h>

const char *ssid     = "Purushotham 4G";
const char *password = "Nivikishore123";
const char *HOST     = "192.168.29.123";   // laptop IP (change per network)
const uint16_t PORT  = 5000;

#define I2S_WS 25
#define I2S_SCK 33
#define I2S_SD 32
#define BTN 0

I2SClass I2S;
const int SAMPLE_RATE = 16000, RECORD_SEC = 4;
const uint32_t NUM_SAMPLES = (uint32_t)SAMPLE_RATE * RECORD_SEC;
const uint32_t DATA_BYTES = NUM_SAMPLES * 2;

void writeWavHeader(WiFiClient &c) {
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

void recordAndSend() {
  WiFiClient client;
  if (!client.connect(HOST, PORT)) { Serial.println("connect FAILED"); return; }
  client.printf("POST /brain HTTP/1.1\r\nHost: %s:%u\r\nContent-Type: audio/wav\r\nContent-Length: %u\r\nConnection: close\r\n\r\n",
                HOST, PORT, (unsigned)(44 + DATA_BYTES));
  writeWavHeader(client);
  Serial.println(">>> RECORDING 4s - speak now <<<");
  const int BLK = 256; int16_t buf[BLK]; uint32_t sent = 0;
  while (sent < NUM_SAMPLES) {
    int n = 0;
    while (n < BLK && sent < NUM_SAMPLES) {
      int s = I2S.read(); if (s==0||s==-1) continue;
      int32_t v = s >> 14; if (v>32767) v=32767; if (v<-32768) v=-32768;
      buf[n++]=(int16_t)v; sent++;
    }
    client.write((uint8_t*)buf, n*2);
  }
  Serial.println("Sent. Laptop is answering...");
  unsigned long t0 = millis(); String answer = "";
  while (client.connected() && millis()-t0 < 30000) {
    if (client.available()) {
      String line = client.readStringUntil('\n');
      if (line.startsWith("X-SPES-Text:")) { answer = line.substring(12); answer.trim(); }
      if (line == "\r") break;
    }
  }
  client.stop();
  String out=""; for (int i=0;i<answer.length();i++){ char ch=answer[i];
    if(ch=='%'&&i+2<answer.length()){char hx[3]={answer[i+1],answer[i+2],0}; out+=(char)strtol(hx,NULL,16); i+=2;}
    else if(ch=='+') out+=' '; else out+=ch; }
  Serial.println("SPES said: " + out);
}

void setup() {
  Serial.begin(115200); delay(300); pinMode(BTN, INPUT_PULLUP);
  I2S.setPins(I2S_SCK, I2S_WS, -1, I2S_SD, -1);
  if (!I2S.begin(I2S_MODE_STD, SAMPLE_RATE, I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO, I2S_STD_SLOT_LEFT)) {
    Serial.println("I2S FAILED"); while(1) delay(1000);
  }
  WiFi.begin(ssid, password);
  Serial.print("WiFi connecting");
  while (WiFi.status()!=WL_CONNECTED){delay(400);Serial.print(".");}
  Serial.println("\nWiFi connected: " + WiFi.localIP().toString());
  Serial.println("Hold BOOT, speak, release.");
}

void loop() {
  if (digitalRead(BTN)==LOW) { delay(50); recordAndSend();
    Serial.println("Ready (hold BOOT to talk again).");
    while (digitalRead(BTN)==LOW) delay(10); }
}
