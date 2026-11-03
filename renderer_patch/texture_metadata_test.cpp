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
    // Version-2 P8 uses an RGBA palette on disk, BGRA8 on the GPU, including all mips.
    uint8_t p8[1163] = {};
    memcpy(p8,dds,128);
    acv::Tag special = {{'A','C','V','U','I','2','X',0},2,2,1,4,2};
    memcpy(p8+32,&special,sizeof special);
    acv::put32(p8+8,0x2100f); acv::put32(p8+28,3); acv::put32(p8+108,0x401008);
    acv::put32(p8+80,0x20); acv::put32(p8+88,8);
    p8[128]=255; p8[131]=7; p8[134]=255; p8[135]=200;
    p8[1153]=1; p8[1162]=1;
    assert(acv::parse_tag(p8,sizeof p8,parsed));
    assert(acv::upload_size(p8,sizeof p8) == 172);
    uint8_t expanded[172] = {};
    assert(acv::prepare_upload(p8,sizeof p8,expanded,sizeof expanded));
    assert(expanded[128]==0 && expanded[130]==255 && expanded[131]==7);
    assert(expanded[132]==255 && expanded[134]==0 && expanded[135]==200);
    assert(expanded[168]==255 && expanded[171]==200); // Last mip, not just the top level.
    assert(acv::u32(expanded+80)==0x41 && acv::u32(expanded+88)==32);
    assert(acv::build_alpha(mask,special,expanded+128,16,21,4,2) && mask[0]==200);
    assert(!acv::prepare_upload(p8,sizeof p8,expanded,sizeof expanded-1));
    assert(!acv::upload_size(p8,sizeof p8-1));
    acv::put32(p8+28,33); assert(!acv::upload_size(p8,sizeof p8)); acv::put32(p8+28,3);
    p8[40]=1; assert(!acv::parse_tag(p8,sizeof p8,parsed)); p8[40]=2;
    // A depth-one volume becomes a temporary 2D upload without changing DXT bytes.
    uint8_t volume[160] = {};
    memcpy(volume,dds,128); memcpy(volume+32,&special,sizeof special);
    acv::put32(volume+8,0x8a1007); acv::put32(volume+24,1); acv::put32(volume+28,2);
    acv::put32(volume+80,4); acv::put32(volume+84,0x35545844);
    acv::put32(volume+108,0x401008); acv::put32(volume+112,0x200000);
    volume[128]=200; volume[159]=73;
    uint8_t flat[160];
    assert(acv::prepare_upload(volume,sizeof volume,flat,sizeof flat));
    assert(!acv::u32(flat+24) && !acv::u32(flat+112) && !(acv::u32(flat+8)&0x800000));
    assert(!memcmp(flat+128,volume+128,32));
    acv::put32(volume+24,2); assert(!acv::upload_size(volume,sizeof volume));
    acv::put32(volume+24,1); acv::put32(volume+112,0x200200);
    assert(!acv::upload_size(volume,sizeof volume));
    puts("DDS v1/v2 tags, palette/volume upload normalization and logical alpha checks passed");
}
