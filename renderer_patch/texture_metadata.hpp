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
        u32(bytes + 76) != 32 || !memcmp(bytes + 84, "DX10", 4)) return false;
    memcpy(&tag, bytes + 32, sizeof tag);
    bool palette = u32(bytes + 80) == 0x20 && !u32(bytes + 84) &&
        u32(bytes + 88) == 8 && !u32(bytes + 92) && !u32(bytes + 96) &&
        !u32(bytes + 100) && !u32(bytes + 104) && !u32(bytes + 112);
    bool volume = u32(bytes + 112) == 0x200000 && u32(bytes + 24) == 1 &&
        u32(bytes + 80) == 4 && (u32(bytes + 8) & 0x801007) == 0x801007 &&
        (u32(bytes + 108) & 0x1008) == 0x1008 &&
        (u32(bytes + 84) == 0x31545844 || u32(bytes + 84) == 0x33545844 ||
         u32(bytes + 84) == 0x35545844);
    bool supported = tag.version == 1 ? !u32(bytes + 112) && !palette :
        tag.version == 2 && (palette || volume);
    return !memcmp(tag.magic, "ACVUI2X", 8) && supported &&
        tag.logical_width && tag.logical_height &&
        tag.logical_width <= tag.physical_width && tag.logical_height <= tag.physical_height &&
        tag.physical_width <= 16384 && tag.physical_height <= 16384 &&
        tag.physical_width == u32(bytes + 16) && tag.physical_height == u32(bytes + 12);
}
inline void put32(uint8_t* data, uint32_t value) { memcpy(data, &value, 4); }
inline size_t upload_size_parsed(const uint8_t* bytes, size_t size, const Tag& tag) {
    if (tag.version != 2 || size > 64u*1024*1024) return 0;
    uint32_t levels = u32(bytes + 28);
    if (!levels) levels = 1;
    uint32_t width = tag.physical_width, height = tag.physical_height, maximum = width > height ? width : height;
    uint32_t limit = 0;
    for (; maximum; maximum >>= 1) ++limit;
    if (levels > limit || (levels > 1 && (!(u32(bytes+8) & 0x20000) ||
        !(u32(bytes+108) & 0x400000)))) return 0;
    bool palette = u32(bytes+80) == 0x20;
    if (palette && ((u32(bytes+8) & 0x1007) != 0x1007 ||
                    !(u32(bytes+108) & 0x1000) || u32(bytes+24))) return 0;
    size_t count = 0;
    size_t block = u32(bytes+84) == 0x31545844 ? 8 : 16;
    for (uint32_t level=0; level<levels; ++level) {
        count += palette ? size_t(width)*height : size_t((width+3)/4)*((height+3)/4)*block;
        width = width > 1 ? width/2 : 1;
        height = height > 1 ? height/2 : 1;
    }
    if (size != 128 + (palette ? 1024 : 0) + count) return 0;
    size_t result = 128 + count*(palette ? 4 : 1);
    return result <= 64u*1024*1024 ? result : 0;
}
inline size_t upload_size(const void* data, size_t size) {
    Tag tag{};
    if (!parse_tag(data, size, tag)) return 0;
    return upload_size_parsed(static_cast<const uint8_t*>(data), size, tag);
}
inline bool prepare_upload_parsed(const uint8_t* bytes, size_t size, const Tag& tag,
                                  uint8_t* output, size_t capacity) {
    size_t needed = upload_size_parsed(bytes, size, tag);
    if (!needed || !output || capacity < needed) return false;
    memcpy(output, bytes, 128);
    if (u32(bytes+80) == 0x20) {
        put32(output+8, (u32(bytes+8) & ~0x80000u) | 8);
        put32(output+20, u32(bytes+16)*4);
        const uint32_t pf[] = {32,0x41,0,32,0xff0000,0xff00,0xff,0xff000000};
        memcpy(output+76, pf, sizeof pf);
        uint32_t bgra[256];
        for (uint32_t i=0; i<256; ++i) {
            const auto* rgba = bytes+128+size_t(i)*4;
            bgra[i] = uint32_t(rgba[2]) | (uint32_t(rgba[1]) << 8) |
                      (uint32_t(rgba[0]) << 16) | (uint32_t(rgba[3]) << 24);
        }
        const auto* src = bytes+1152;
        auto* dst = output+128;
        const size_t pixels = size-1152;
        for (size_t i=0; i<pixels; ++i) put32(dst+i*4, bgra[src[i]]);
    } else {
        memcpy(output+128, bytes+128, size-128);
        put32(output+8, u32(bytes+8) & ~0x800000u);
        put32(output+24, 0); put32(output+112, 0);
    }
    return true;
}
inline bool prepare_upload(const void* data, size_t size, uint8_t* output, size_t capacity) {
    Tag tag{};
    if (!parse_tag(data, size, tag)) return false;
    return prepare_upload_parsed(static_cast<const uint8_t*>(data), size, tag, output, capacity);
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
inline uint8_t max4(uint8_t a, uint8_t b, uint8_t c, uint8_t d) {
    uint8_t m0 = a > b ? a : b;
    uint8_t m1 = c > d ? c : d;
    return m0 > m1 ? m0 : m1;
}
inline bool build_alpha(uint8_t* mask, const Tag& tag, const uint8_t* pixels,
                        size_t pitch, uint32_t format, uint32_t actual_width, uint32_t actual_height) {
    if (!mask || !pixels || !tag.logical_width || !tag.logical_height ||
        actual_width != tag.physical_width || actual_height != tag.physical_height ||
        !minimum_pitch(actual_width, format) || pitch < minimum_pitch(actual_width, format)) return false;
    const uint32_t lw = tag.logical_width, lh = tag.logical_height;
    switch (format) {
        case 20: case 22: case 23: case 24: case 30: case 33:
            memset(mask, 255, size_t(lh) * lw);
            return true;
    }
    if (actual_width == lw * 2 && actual_height == lh * 2) {
        if (format == 21 || format == 32) {
            for (uint32_t y = 0; y < lh; ++y) {
                const auto* r0 = pixels + size_t(y) * 2 * pitch;
                const auto* r1 = r0 + pitch;
                auto* out = mask + size_t(y) * lw;
                for (uint32_t x = 0; x < lw; ++x)
                    out[x] = max4(r0[x * 8 + 3], r0[x * 8 + 7], r1[x * 8 + 3], r1[x * 8 + 7]);
            }
            return true;
        }
        if (format == 25) {
            for (uint32_t y = 0; y < lh; ++y) {
                const auto* r0 = pixels + size_t(y) * 2 * pitch;
                const auto* r1 = r0 + pitch;
                auto* out = mask + size_t(y) * lw;
                for (uint32_t x = 0; x < lw; ++x)
                    out[x] = ((r0[x * 4 + 1] | r0[x * 4 + 3] | r1[x * 4 + 1] | r1[x * 4 + 3]) & 128u) ? 255 : 0;
            }
            return true;
        }
        if (format == 26) {
            for (uint32_t y = 0; y < lh; ++y) {
                const auto* r0 = pixels + size_t(y) * 2 * pitch;
                const auto* r1 = r0 + pitch;
                auto* out = mask + size_t(y) * lw;
                for (uint32_t x = 0; x < lw; ++x)
                    out[x] = max4(r0[x * 4 + 1], r0[x * 4 + 3], r1[x * 4 + 1], r1[x * 4 + 3]) >> 4;
            }
            return true;
        }
        if (format == 28) {
            for (uint32_t y = 0; y < lh; ++y) {
                const auto* r0 = pixels + size_t(y) * 2 * pitch;
                const auto* r1 = r0 + pitch;
                auto* out = mask + size_t(y) * lw;
                for (uint32_t x = 0; x < lw; ++x)
                    out[x] = max4(r0[x * 2], r0[x * 2 + 1], r1[x * 2], r1[x * 2 + 1]);
            }
            return true;
        }
        if (format == 0x31545844) {
            const uint32_t blocks_x = (actual_width + 3) / 4;
            const uint32_t blocks_y = (actual_height + 3) / 4;
            for (uint32_t by = 0; by < blocks_y; ++by) {
                const auto* row = pixels + size_t(by) * pitch;
                auto* out0 = mask + size_t(by * 2) * lw;
                const bool has_y1 = (by * 2 + 1) < lh;
                auto* out1 = has_y1 ? out0 + lw : nullptr;
                for (uint32_t bx = 0; bx < blocks_x; ++bx) {
                    const auto* block = row + size_t(bx) * 8;
                    const uint32_t lx = bx * 2;
                    const bool has_x1 = (lx + 1) < lw;
                    const uint32_t colors = u32(block);
                    if ((uint16_t)colors > (uint16_t)(colors >> 16)) {
                        out0[lx] = 255;
                        if (has_x1) out0[lx + 1] = 255;
                        if (has_y1) {
                            out1[lx] = 255;
                            if (has_x1) out1[lx + 1] = 255;
                        }
                    } else {
                        const uint32_t bits = u32(block + 4);
                        out0[lx] = (bits & 0x00000f0fu) == 0x00000f0fu ? 0 : 255;
                        if (has_x1) out0[lx + 1] = (bits & 0x0000f0f0u) == 0x0000f0f0u ? 0 : 255;
                        if (has_y1) {
                            out1[lx] = (bits & 0x0f0f0000u) == 0x0f0f0000u ? 0 : 255;
                            if (has_x1) out1[lx + 1] = (bits & 0xf0f00000u) == 0xf0f00000u ? 0 : 255;
                        }
                    }
                }
            }
            return true;
        }
        if (format == 0x33545844) {
            const uint32_t blocks_x = (actual_width + 3) / 4;
            const uint32_t blocks_y = (actual_height + 3) / 4;
            for (uint32_t by = 0; by < blocks_y; ++by) {
                const auto* row = pixels + size_t(by) * pitch;
                auto* out0 = mask + size_t(by * 2) * lw;
                const bool has_y1 = (by * 2 + 1) < lh;
                auto* out1 = has_y1 ? out0 + lw : nullptr;
                for (uint32_t bx = 0; bx < blocks_x; ++bx) {
                    const auto* b = row + size_t(bx) * 16;
                    const uint32_t lx = bx * 2;
                    const bool has_x1 = (lx + 1) < lw;
                    out0[lx] = max4(b[0] & 15, b[0] >> 4, b[2] & 15, b[2] >> 4) * 17;
                    if (has_x1) out0[lx + 1] = max4(b[1] & 15, b[1] >> 4, b[3] & 15, b[3] >> 4) * 17;
                    if (has_y1) {
                        out1[lx] = max4(b[4] & 15, b[4] >> 4, b[6] & 15, b[6] >> 4) * 17;
                        if (has_x1) out1[lx + 1] = max4(b[5] & 15, b[5] >> 4, b[7] & 15, b[7] >> 4) * 17;
                    }
                }
            }
            return true;
        }
        if (format == 0x35545844) {
            const uint32_t blocks_x = (actual_width + 3) / 4;
            const uint32_t blocks_y = (actual_height + 3) / 4;
            for (uint32_t by = 0; by < blocks_y; ++by) {
                const auto* row = pixels + size_t(by) * pitch;
                auto* out0 = mask + size_t(by * 2) * lw;
                const bool has_y1 = (by * 2 + 1) < lh;
                auto* out1 = has_y1 ? out0 + lw : nullptr;
                for (uint32_t bx = 0; bx < blocks_x; ++bx) {
                    const auto* b = row + size_t(bx) * 16;
                    const uint32_t lx = bx * 2;
                    const bool has_x1 = (lx + 1) < lw;
                    uint8_t t[8] = {b[0], b[1]};
                    if (t[0] > t[1]) {
                        t[2] = (6 * t[0] + 1 * t[1]) / 7;
                        t[3] = (5 * t[0] + 2 * t[1]) / 7;
                        t[4] = (4 * t[0] + 3 * t[1]) / 7;
                        t[5] = (3 * t[0] + 4 * t[1]) / 7;
                        t[6] = (2 * t[0] + 5 * t[1]) / 7;
                        t[7] = (1 * t[0] + 6 * t[1]) / 7;
                    } else {
                        t[2] = (4 * t[0] + 1 * t[1]) / 5;
                        t[3] = (3 * t[0] + 2 * t[1]) / 5;
                        t[4] = (2 * t[0] + 3 * t[1]) / 5;
                        t[5] = (1 * t[0] + 4 * t[1]) / 5;
                        t[6] = 0;
                        t[7] = 255;
                    }
                    const uint32_t lo = u32(b + 2);
                    out0[lx] = max4(t[lo & 7], t[(lo >> 3) & 7], t[(lo >> 12) & 7], t[(lo >> 15) & 7]);
                    if (has_x1)
                        out0[lx + 1] = max4(t[(lo >> 6) & 7], t[(lo >> 9) & 7], t[(lo >> 18) & 7], t[(lo >> 21) & 7]);
                    if (has_y1) {
                        const uint32_t hi = (lo >> 24) | (uint32_t(b[6]) << 8) | (uint32_t(b[7]) << 16);
                        out1[lx] = max4(t[hi & 7], t[(hi >> 3) & 7], t[(hi >> 12) & 7], t[(hi >> 15) & 7]);
                        if (has_x1)
                            out1[lx + 1] = max4(t[(hi >> 6) & 7], t[(hi >> 9) & 7], t[(hi >> 18) & 7], t[(hi >> 21) & 7]);
                    }
                }
            }
            return true;
        }
    }
    const uint8_t ceiling = format == 26 ? 15 : 255;
    uint32_t lefts[256], rights[256];
    for (uint32_t x0 = 0; x0 < lw; x0 += 256) {
        const uint32_t tile = (lw - x0) < 256 ? (lw - x0) : 256;
        for (uint32_t i = 0; i < tile; ++i) {
            const uint32_t x = x0 + i;
            lefts[i] = uint64_t(x) * actual_width / lw;
            rights[i] = (uint64_t(x + 1) * actual_width + lw - 1) / lw;
        }
        for (uint32_t y = 0; y < lh; ++y) {
            const uint32_t top = uint64_t(y) * actual_height / lh;
            const uint32_t bottom = (uint64_t(y + 1) * actual_height + lh - 1) / lh;
            auto* out = mask + size_t(y) * lw + x0;
            for (uint32_t i = 0; i < tile; ++i) {
                uint8_t maximum = 0;
                // Any opaque source texel keeps thin button edges clickable.
                for (uint32_t py = top; py < bottom && maximum < ceiling; ++py)
                    for (uint32_t px = lefts[i]; px < rights[i]; ++px) {
                        uint8_t alpha = alpha_at(pixels, pitch, format, px, py);
                        if (alpha > maximum) {
                            maximum = alpha;
                            if (maximum == ceiling) break;
                        }
                    }
                out[i] = maximum;
            }
        }
    }
    return true;
}
}
