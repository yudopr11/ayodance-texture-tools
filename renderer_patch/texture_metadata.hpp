#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>

namespace acv {
struct Tag {
    char magic[8];
    uint32_t version, logical_width, logical_height, physical_width, physical_height;
};
static_assert(sizeof(Tag) == 28, "DDS tag layout");
inline uint32_t u32(const void* data) {
    uint32_t value;
    memcpy(&value, data, sizeof value);
    return value;
}
inline bool parse_tag(const void* data, size_t size, Tag& tag) {
    if (!data || size < 128) return false;
    const auto* bytes = static_cast<const uint8_t*>(data);
    if (memcmp(bytes, "DDS ", 4) || u32(bytes + 4) != 124 ||
        u32(bytes + 76) != 32 || u32(bytes + 112) ||
        !memcmp(bytes + 84, "DX10", 4)) return false;
    memcpy(&tag, bytes + 32, sizeof tag);
    return !memcmp(tag.magic, "ACVUI2X", 8) && tag.version == 1 &&
        tag.logical_width && tag.logical_height &&
        tag.logical_width <= tag.physical_width && tag.logical_height <= tag.physical_height &&
        tag.physical_width <= 16384 && tag.physical_height <= 16384 &&
        tag.physical_width == u32(bytes + 16) && tag.physical_height == u32(bytes + 12);
}
inline uint8_t alpha_at(const uint8_t* pixels, size_t pitch, uint32_t format,
                        uint32_t x, uint32_t y) {
    bool compressed = format == 0x31545844 || format == 0x33545844 || format == 0x35545844;
    const auto* row = compressed ? pixels : pixels + pitch * y;
    switch (format) {
        case 21: case 32: return row[x * 4 + 3]; // A8R8G8B8 / A8B8G8R8
        case 25: return (row[x * 2 + 1] & 128) ? 255 : 0;
        case 26: return row[x * 2 + 1] >> 4; // Keep the original 0..15 range.
        case 28: return row[x];
        case 20: case 22: case 23: case 24: case 30: case 33: return 255;
    }
    // DXT blocks use a block-row pitch, not a pixel-row pitch.
    const auto* block = pixels + (y / 4) * pitch;
    uint32_t index = (y % 4) * 4 + x % 4;
    if (format == 0x31545844) {
        block += (x / 4) * 8;
        uint32_t colors = u32(block);
        return (uint16_t)colors <= (uint16_t)(colors >> 16) &&
               ((u32(block + 4) >> (index * 2)) & 3) == 3 ? 0 : 255;
    }
    block += (x / 4) * 16;
    if (format == 0x33545844) return ((block[index / 2] >> ((index % 2) * 4)) & 15) * 17;
    if (format == 0x35545844) {
        uint8_t table[8] = {block[0], block[1]};
        if (table[0] > table[1]) {
            for (uint32_t i = 2; i < 8; ++i) table[i] = ((8-i)*table[0] + (i-1)*table[1]) / 7;
        } else {
            for (uint32_t i = 2; i < 6; ++i) table[i] = ((6-i)*table[0] + (i-1)*table[1]) / 5;
            table[6] = 0; table[7] = 255;
        }
        uint64_t indices = 0;
        for (uint32_t i = 0; i < 6; ++i) indices |= uint64_t(block[i+2]) << (8*i);
        return table[(indices >> (index * 3)) & 7];
    }
    return 0;
}
inline size_t minimum_pitch(uint32_t width, uint32_t format) {
    switch (format) {
        case 20: return size_t(width) * 3;
        case 21: case 22: case 32: case 33: return size_t(width) * 4;
        case 23: case 24: case 25: case 26: case 30: return size_t(width) * 2;
        case 28: return width;
        case 0x31545844: return ((width + 3) / 4) * 8;
        case 0x33545844: case 0x35545844: return ((width + 3) / 4) * 16;
        default: return 0;
    }
}
inline bool build_alpha(uint8_t* mask, const Tag& tag, const uint8_t* pixels,
                        size_t pitch, uint32_t format, uint32_t actual_width, uint32_t actual_height) {
    if (!mask || !pixels || !tag.logical_width || !tag.logical_height ||
        actual_width != tag.physical_width || actual_height != tag.physical_height ||
        !minimum_pitch(actual_width, format) || pitch < minimum_pitch(actual_width, format)) return false;
    for (uint32_t y = 0; y < tag.logical_height; ++y) {
        uint32_t top = uint64_t(y) * actual_height / tag.logical_height;
        uint32_t bottom = (uint64_t(y+1) * actual_height + tag.logical_height-1) / tag.logical_height;
        for (uint32_t x = 0; x < tag.logical_width; ++x) {
            uint32_t left = uint64_t(x) * actual_width / tag.logical_width;
            uint32_t right = (uint64_t(x+1) * actual_width + tag.logical_width-1) / tag.logical_width;
            uint8_t maximum = 0;
            // Any opaque source texel keeps thin button edges clickable.
            for (uint32_t py = top; py < bottom; ++py)
                for (uint32_t px = left; px < right; ++px) {
                    uint8_t alpha = alpha_at(pixels, pitch, format, px, py);
                    if (alpha > maximum) maximum = alpha;
                }
            mask[size_t(y) * tag.logical_width + x] = maximum;
        }
    }
    return true;
}
}
