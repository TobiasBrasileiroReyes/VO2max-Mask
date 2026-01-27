//+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
// VO2max Mask - BLE Raw Stream (200 Hz)
// Sends all data needed for VO2/VO2max calculation to a PC.
// Venturi assumptions: 16mm nozzle.
//+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

#include <Arduino.h>
#include <Wire.h>
#include <BLEDevice.h>
#include <BLEUtils.h>
#include <BLEServer.h>
#include <BLE2902.h>

#include "DFRobot_OxygenSensor.h"
#include "STC31.h"

// -----------------------------
// Configuration
// -----------------------------
static const uint16_t SAMPLE_RATE_HZ = 200;
static const uint32_t SAMPLE_PERIOD_US = 1000000UL / SAMPLE_RATE_HZ; // 5000 us
static const uint8_t PRESSURE_BATCH_SAMPLES = 10; // adjust if MTU is small
static const float PRESSURE_SCALE = 10.0f; // 0.1 Pa resolution (Pa * 10)
static const uint32_t O2_PERIOD_MS = 1000; // 1 Hz O2 updates
static const uint32_t CO2_PERIOD_MS = 1000; // poll CO2 at 1 Hz (sensor updates slower)

// Venturi geometry (16mm)
static const float AREA_1 = 0.000531f; // 26mm diameter
static const float AREA_2 = 0.000201f; // 16mm diameter
static const float CORRECTION_SENSOR = 0.92f;
static const int8_t PRESSURE_SIGN = +1; // +1 if TE+ gives positive on exhale

// Ambient assumptions (used by PC if desired)
static const float TEMP_C = 15.0f;
static const float PRES_PA = 101325.0f;
static const float FI_O2 = 20.90f; // assumed inspired O2

// O2 sensor
DFRobot_OxygenSensor Oxygen;
#define Oxygen_IICAddress ADDRESS_3
#define COLLECT_NUMBER 10

// CO2 sensor (STC31)
STC31 CO2Sensor;
bool co2Ok = false;

// TE SM923x/SM933x minimal driver (I2C)
#define TE_PRESSURE_I2C_ADDR 0x6C
#define TE_REG_DSP_S 0x30

// TE transfer function (Pa)
static const float  TE_PMIN   = -250.0f;
static const float  TE_PMAX   =  250.0f;
static const int16_t TE_OUTMIN = -26215;
static const int16_t TE_OUTMAX =  26214;

static float pressureOffset = 0.0f;

// -----------------------------
// BLE UUIDs (custom)
// -----------------------------
static const char* SERVICE_UUID = "7a1b7d30-2e4b-4b2f-8f1a-2dfe3e5c0e11";
static const char* STREAM_CHAR_UUID = "7a1b7d31-2e4b-4b2f-8f1a-2dfe3e5c0e11";

// Packet types
static const uint8_t PKT_CONFIG = 0x01;
static const uint8_t PKT_PRESSURE = 0x02;
static const uint8_t PKT_O2 = 0x03;
static const uint8_t PKT_CO2 = 0x04;

BLECharacteristic* streamChar = nullptr;
bool deviceConnected = false;
bool configSent = false;

class StreamServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer* pServer) override {
    deviceConnected = true;
    configSent = false;
  }
  void onDisconnect(BLEServer* pServer) override {
    deviceConnected = false;
    configSent = false;
    BLEDevice::startAdvertising();
  }
};

// -----------------------------
// Helpers
// -----------------------------
static void writeU16LE(uint8_t* out, uint16_t v) {
  out[0] = (uint8_t)(v & 0xFF);
  out[1] = (uint8_t)((v >> 8) & 0xFF);
}

static void writeU32LE(uint8_t* out, uint32_t v) {
  out[0] = (uint8_t)(v & 0xFF);
  out[1] = (uint8_t)((v >> 8) & 0xFF);
  out[2] = (uint8_t)((v >> 16) & 0xFF);
  out[3] = (uint8_t)((v >> 24) & 0xFF);
}

static void writeFloatLE(uint8_t* out, float v) {
  static_assert(sizeof(float) == 4, "float must be 4 bytes");
  uint8_t* p = reinterpret_cast<uint8_t*>(&v);
  out[0] = p[0];
  out[1] = p[1];
  out[2] = p[2];
  out[3] = p[3];
}

void i2cScan() {
  Serial.println("Scanning I2C...");
  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    uint8_t err = Wire.endTransmission();
    if (err == 0) {
      Serial.print("Found I2C device at 0x");
      if (addr < 16) Serial.print("0");
      Serial.println(addr, HEX);
    }
  }
  Serial.println("Scan done.");
}

bool te_readInt16(uint8_t reg, int16_t &val) {
  Wire.beginTransmission(TE_PRESSURE_I2C_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(TE_PRESSURE_I2C_ADDR, (uint8_t)2) != 2) return false;
  uint8_t lo = Wire.read();
  uint8_t hi = Wire.read();
  val = (int16_t)((hi << 8) | lo);
  return true;
}

bool te_readPressurePa(float &p_pa) {
  int16_t counts = 0;
  if (!te_readInt16(TE_REG_DSP_S, counts)) return false;
  p_pa = TE_PMIN + (((float)counts - (float)TE_OUTMIN) *
                    (TE_PMAX - TE_PMIN) /
                    ((float)TE_OUTMAX - (float)TE_OUTMIN));
  return true;
}

bool te_autozero(uint16_t duration_ms = 800) {
  uint32_t t0 = millis();
  uint16_t n = 0;
  double acc = 0.0;
  while (millis() - t0 < duration_ms) {
    float p;
    if (te_readPressurePa(p)) {
      acc += p;
      n++;
    }
    delay(5);
  }
  if (n < 5) return false;
  pressureOffset = (float)(acc / n);
  return true;
}

size_t buildConfigPacket(uint8_t* out, size_t maxLen) {
  const uint8_t version = 1;
  const size_t needed = 1 + 1 + (8 * 4) + 1;
  if (maxLen < needed) return 0;
  size_t idx = 0;
  out[idx++] = PKT_CONFIG;
  out[idx++] = version;
  writeFloatLE(out + idx, (float)SAMPLE_RATE_HZ); idx += 4;
  writeFloatLE(out + idx, AREA_1); idx += 4;
  writeFloatLE(out + idx, AREA_2); idx += 4;
  writeFloatLE(out + idx, CORRECTION_SENSOR); idx += 4;
  writeFloatLE(out + idx, TEMP_C); idx += 4;
  writeFloatLE(out + idx, PRES_PA); idx += 4;
  writeFloatLE(out + idx, FI_O2); idx += 4;
  writeFloatLE(out + idx, PRESSURE_SCALE); idx += 4;
  out[idx++] = (uint8_t)PRESSURE_SIGN;
  return idx;
}

size_t buildPressurePacket(uint8_t* out, size_t maxLen, uint32_t startIdx,
                           const int16_t* samples, uint16_t count) {
  size_t needed = 1 + 4 + 2 + (count * 2);
  if (maxLen < needed) return 0;
  size_t idx = 0;
  out[idx++] = PKT_PRESSURE;
  writeU32LE(out + idx, startIdx); idx += 4;
  writeU16LE(out + idx, count); idx += 2;
  for (uint16_t i = 0; i < count; i++) {
    int16_t v = samples[i];
    out[idx++] = (uint8_t)(v & 0xFF);
    out[idx++] = (uint8_t)((v >> 8) & 0xFF);
  }
  return idx;
}

size_t buildO2Packet(uint8_t* out, size_t maxLen, uint32_t timeMs, float o2Pct) {
  if (maxLen < 1 + 4 + 2) return 0;
  size_t idx = 0;
  out[idx++] = PKT_O2;
  writeU32LE(out + idx, timeMs); idx += 4;
  uint16_t o2 = (uint16_t)lroundf(o2Pct * 100.0f); // 0.01% resolution
  writeU16LE(out + idx, o2); idx += 2;
  return idx;
}

size_t buildCo2Packet(uint8_t* out, size_t maxLen, uint32_t timeMs, float co2Pct) {
  if (maxLen < 1 + 4 + 2) return 0;
  size_t idx = 0;
  out[idx++] = PKT_CO2;
  writeU32LE(out + idx, timeMs); idx += 4;
  uint16_t co2 = (uint16_t)lroundf(co2Pct * 100.0f); // 0.01% resolution
  writeU16LE(out + idx, co2); idx += 2;
  return idx;
}

void sendPacket(const uint8_t* data, size_t len) {
  if (!deviceConnected || !streamChar || len == 0) return;
  streamChar->setValue(data, len);
  streamChar->notify();
}

// -----------------------------
// Sampling state
// -----------------------------
uint32_t nextSampleUs = 0;
uint32_t sampleIndex = 0;
int16_t pressureBatch[PRESSURE_BATCH_SAMPLES];
uint8_t pressureBatchCount = 0;
uint32_t batchStartIndex = 0;
uint32_t lastBatchMs = 0;
uint32_t lastO2Ms = 0;
uint32_t lastCo2Ms = 0;

// -----------------------------
// Setup / Loop
// -----------------------------
void setup() {
  Serial.begin(115200);
  delay(50);

  Wire.begin(21, 22);
  Wire.setClock(100000);

  i2cScan();

  if (!Oxygen.begin(Oxygen_IICAddress)) {
    Serial.println("O2 sensor init failed");
  } else {
    Serial.println("O2 sensor ok");
  }

  co2Ok = CO2Sensor.begin(Wire);
  if (!co2Ok) {
    Serial.println("CO2 sensor init failed");
  } else {
    Serial.printf("CO2 sensor ok (STC31 addr 0x%02X)\n", CO2Sensor.address());
  }

  if (!te_autozero(1000)) {
    Serial.println("Flow sensor zeroing failed");
  } else {
    Serial.println("Flow sensor zeroed");
  }

  BLEDevice::init("SpiroVO2-RAW");
  BLEDevice::setMTU(185);

  BLEServer* server = BLEDevice::createServer();
  server->setCallbacks(new StreamServerCallbacks());

  BLEService* service = server->createService(SERVICE_UUID);
  streamChar = service->createCharacteristic(
      STREAM_CHAR_UUID, BLECharacteristic::PROPERTY_NOTIFY);
  streamChar->addDescriptor(new BLE2902());

  service->start();

  BLEAdvertising* advertising = BLEDevice::getAdvertising();
  advertising->addServiceUUID(SERVICE_UUID);
  advertising->setScanResponse(true);
  advertising->setMinPreferred(0x06);
  advertising->setMaxPreferred(0x12);
  advertising->start();

  nextSampleUs = micros();
  lastO2Ms = millis();
  lastCo2Ms = millis();
}

void loop() {
  // Send config once per connection
  if (deviceConnected && !configSent) {
    uint8_t packet[64];
    size_t len = buildConfigPacket(packet, sizeof(packet));
    sendPacket(packet, len);
    configSent = true;
  }

  // Sample pressure at fixed rate
  uint32_t nowUs = micros();
  while ((int32_t)(nowUs - nextSampleUs) >= 0) {
    float pPa = NAN;
    if (te_readPressurePa(pPa)) {
      float adj = (float)PRESSURE_SIGN * (pPa - pressureOffset);
      int16_t scaled = (int16_t)lroundf(adj * PRESSURE_SCALE);
      if (pressureBatchCount == 0) {
        batchStartIndex = sampleIndex;
        lastBatchMs = millis();
      }
      if (pressureBatchCount < PRESSURE_BATCH_SAMPLES) {
        pressureBatch[pressureBatchCount++] = scaled;
      }
      sampleIndex++;
    }
    nextSampleUs += SAMPLE_PERIOD_US;
    nowUs = micros();
  }

  // Flush pressure batch if full or stale
  if (pressureBatchCount > 0) {
    if (pressureBatchCount >= PRESSURE_BATCH_SAMPLES || (millis() - lastBatchMs) >= 100) {
      uint8_t packet[2 + 4 + 2 + (PRESSURE_BATCH_SAMPLES * 2)];
      size_t len = buildPressurePacket(packet, sizeof(packet), batchStartIndex,
                                       pressureBatch, pressureBatchCount);
      sendPacket(packet, len);
      pressureBatchCount = 0;
    }
  }

  // Send O2 periodically
  if (millis() - lastO2Ms >= O2_PERIOD_MS) {
    float o2 = Oxygen.ReadOxygenData(COLLECT_NUMBER);
    uint8_t packet[16];
    size_t len = buildO2Packet(packet, sizeof(packet), millis(), o2);
    sendPacket(packet, len);
    lastO2Ms = millis();
  }

  // Send CO2 periodically
  if (millis() - lastCo2Ms >= CO2_PERIOD_MS) {
    if (co2Ok) {
      float co2_pct = NAN;
      float tempC = NAN;
      if (CO2Sensor.readCO2(co2_pct, tempC)) {
        uint8_t packet[16];
        size_t len = buildCo2Packet(packet, sizeof(packet), millis(), co2_pct);
        sendPacket(packet, len);
      }
    }
    lastCo2Ms = millis();
  }
}
