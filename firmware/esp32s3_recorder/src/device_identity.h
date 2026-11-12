#pragma once

#include <stdint.h>

namespace recorder_identity {

constexpr uint64_t reverseMac48(uint64_t value) {
  return ((value & 0x0000000000FFULL) << 40) |
         ((value & 0x00000000FF00ULL) << 24) |
         ((value & 0x000000FF0000ULL) << 8) |
         ((value & 0x0000FF000000ULL) >> 8) |
         ((value & 0x00FF00000000ULL) >> 24) |
         ((value & 0xFF0000000000ULL) >> 40);
}

constexpr bool matchesMac(uint64_t efuseValue, uint64_t recordedMac) {
  return efuseValue == recordedMac || efuseValue == reverseMac48(recordedMac);
}

// These were recorded in the conventional MAC display order. ESP.getEfuseMac()
// may expose the same 48 bits in reverse byte order, depending on the API path.
constexpr uint64_t kBeanYe1RecordedMac = 0x68EE8F4721DCULL;
constexpr uint64_t kBeanYe2RecordedMac = 0x94A990DF65F8ULL;

static_assert(
    matchesMac(0xF865DF90A994ULL, kBeanYe2RecordedMac),
    "bean_ye_2 eFuse byte order must remain recognized");

}  // namespace recorder_identity
