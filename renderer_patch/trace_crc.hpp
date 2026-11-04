#pragma once
#include <stddef.h>
#include <stdint.h>

namespace acv {
struct TraceCrcTable { uint32_t values[256]; };
constexpr TraceCrcTable crc_table() {
    TraceCrcTable table{};
    for (uint32_t i=0; i<256; ++i) {
        uint32_t value = i;
        for (unsigned bit=0; bit<8; ++bit) value = (value>>1) ^ ((value&1) ? 0xedb88320u : 0);
        table.values[i] = value;
    }
    return table;
}
inline uint32_t trace_crc32(const void* data,size_t size) {
    static constexpr auto table = crc_table();
    auto* bytes = static_cast<const uint8_t*>(data);
    uint32_t crc = 0xffffffffu;
    for (size_t i=0; i<size; ++i) crc = table.values[(crc^bytes[i])&255] ^ (crc>>8);
    return crc ^ 0xffffffffu;
}
}
