#include "texture_metadata.hpp"
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <stdio.h>

int main() {
    acv::Tag tag = {{'A','C','V','U','I','2','X',0},1,2,1,4,2};
    uint8_t dds[128] = {};
    memcpy(dds,"DDS ",4);
    uint32_t header = 124, pixel_header = 32;
    memcpy(dds+4,&header,4); memcpy(dds+76,&pixel_header,4);
    memcpy(dds+12,&tag.physical_height,4); memcpy(dds+16,&tag.physical_width,4);
    memcpy(dds+32,&tag,sizeof tag);
    acv::Tag parsed{};
    assert(acv::parse_tag(dds,sizeof dds,parsed));
    assert(!acv::parse_tag(dds,127,parsed));
    dds[32] = '?'; assert(!acv::parse_tag(dds,sizeof dds,parsed)); dds[32] = 'A';
    dds[16] = 8; assert(!acv::parse_tag(dds,sizeof dds,parsed)); dds[16] = 4;
    dds[48] = 0; assert(!acv::parse_tag(dds,sizeof dds,parsed)); dds[48] = 1;
    uint8_t pixels[40] = {}; // Two ARGB rows with four padding bytes each.
    pixels[3] = 7; pixels[7] = 255; pixels[20+3] = 9;
    pixels[11] = 100; pixels[20+15] = 200;
    uint8_t mask[2] = {};
    assert(acv::build_alpha(mask,tag,pixels,20,21,4,2));
    assert(mask[0] == 255 && mask[1] == 200);
    assert(!acv::build_alpha(mask,tag,pixels,15,21,4,2));
    assert(!acv::build_alpha(mask,tag,pixels,20,21,2,2));
    assert(!acv::build_alpha(mask,tag,pixels,20,999,4,2));
    uint8_t argb4444[] = {0,0xf0,0,0x70};
    assert(acv::alpha_at(argb4444,4,26,0,0) == 15);
    assert(acv::alpha_at(argb4444,4,26,1,0) == 7);
    uint8_t dxt1[16] = {};
    dxt1[4] = 3; dxt1[8+7] = 0xc0;
    assert(acv::alpha_at(dxt1,16,0x31545844,0,0) == 0);
    assert(acv::alpha_at(dxt1,16,0x31545844,1,0) == 255);
    assert(acv::alpha_at(dxt1,16,0x31545844,7,3) == 0);
    dxt1[0] = 1; assert(acv::alpha_at(dxt1,16,0x31545844,0,0) == 255);
    uint8_t dxt3[16] = {}; dxt3[0] = 0xf1; dxt3[7] = 0xa0;
    assert(acv::alpha_at(dxt3,16,0x33545844,0,0) == 17);
    assert(acv::alpha_at(dxt3,16,0x33545844,1,0) == 255);
    assert(acv::alpha_at(dxt3,16,0x33545844,3,3) == 170);
    uint8_t dxt5[16] = {255,0,2};
    assert(acv::alpha_at(dxt5,16,0x35545844,0,0) == 218);
    dxt5[0] = 0; dxt5[1] = 255; dxt5[2] = 6;
    assert(acv::alpha_at(dxt5,16,0x35545844,0,0) == 0);
    dxt5[2] = 7; assert(acv::alpha_at(dxt5,16,0x35545844,0,0) == 255);
    assert(acv::alpha_at(pixels,20,22,0,0) == 255);
    // A non-integer scale must include every covered source texel.
    tag.logical_width = 3;
    uint8_t uneven[3];
    assert(acv::build_alpha(uneven,tag,pixels,20,21,4,2));
    assert(uneven[0] == 255 && uneven[1] == 255 && uneven[2] == 200);
    puts("DDS tag, padded/uncompressed/DXT alpha and conservative logical hit-test checks passed");
}
