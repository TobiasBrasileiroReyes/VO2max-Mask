#pragma once
#include <Arduino.h>
#include <Wire.h>

// Möjliga I2C-adresser på Mikroe CO2 Click
static const uint8_t STC_ADDR_CAND[4] = {0x2C, 0x2A, 0x2B, 0x29};

class STC31 {
public:
  bool begin(TwoWire &w = Wire);
  bool readCO2(float &co2_volpct, float &tempC);  // co2 i vol%, temp i °C
  uint8_t address() const { return _addr; }
  bool ok() const { return _ok; }

private:
  TwoWire* _wire = &Wire;
  uint8_t  _addr = 0x2C;
  bool     _ok   = false;

  bool writeCmd(uint16_t cmd);
  bool writeCmdArg(uint16_t cmd, uint16_t arg);
  bool readWordsNoCRC(uint8_t nWords, uint16_t *out);
};