#include "STC31.h"

// Kommandon (no CRC path)
#define STC_DISABLE_CRC     0x3768
#define STC_SET_BINARY_GAS  0x3615  // arg 0x0003 => CO2 i luft
#define STC_MEASURE_ONESHOT 0x3639

bool STC31::writeCmd(uint16_t cmd) {
  _wire->beginTransmission(_addr);
  _wire->write(uint8_t(cmd >> 8));
  _wire->write(uint8_t(cmd));
  return _wire->endTransmission() == 0;
}
bool STC31::writeCmdArg(uint16_t cmd, uint16_t arg) {
  _wire->beginTransmission(_addr);
  _wire->write(uint8_t(cmd >> 8));
  _wire->write(uint8_t(cmd));
  _wire->write(uint8_t(arg >> 8));
  _wire->write(uint8_t(arg));
  return _wire->endTransmission() == 0;
}
bool STC31::readWordsNoCRC(uint8_t nWords, uint16_t *out) {
  uint8_t nBytes = nWords * 2;
  if (_wire->requestFrom(int(_addr), int(nBytes)) != nBytes) return false;
  for (uint8_t i = 0; i < nWords; i++) {
    uint8_t msb = _wire->read();
    uint8_t lsb = _wire->read();
    out[i] = (uint16_t(msb) << 8) | lsb;
  }
  return true;
}

bool STC31::begin(TwoWire &w) {
  _wire = &w;
  // prova alla kandidater tills någon ackar DISABLE_CRC
  for (uint8_t i = 0; i < 4; i++) {
    _addr = STC_ADDR_CAND[i];
    _wire->beginTransmission(_addr);
    _wire->write(uint8_t(STC_DISABLE_CRC >> 8));
    _wire->write(uint8_t(STC_DISABLE_CRC));
    if (_wire->endTransmission() == 0) { break; }
  }
  // välj “CO2 i luft” (0..25 vol%)
  _ok = writeCmdArg(STC_SET_BINARY_GAS, 0x0003);
  delay(5);
  return _ok;
}

bool STC31::readCO2(float &co2_volpct, float &tempC) {
  if (!writeCmd(STC_MEASURE_ONESHOT)) return false;
  delay(70); // t_meas ~66 ms
  uint16_t w[3] = {0};
  if (!readWordsNoCRC(3, w)) return false;

  // Skalning enligt STC31-datablad:
  // conc[vol%] = (raw - 2^14)/2^15*100 , temp[°C] = raw/200
  co2_volpct = ( (float)w[0] - 16384.0f ) / 32768.0f * 100.0f;
  tempC      = ( (int16_t)w[1] ) / 200.0f;
  return true;
}