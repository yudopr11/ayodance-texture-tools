// Standalone D3D9 test; never opens, attaches to, or modifies the game.
#include "d3d9_proxy.cpp"
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>

int main(int argc,char** argv) {
    if (argc < 3) { fprintf(stderr,"Expected absolute d3dx9_43.dll and pilot d3d9.dll paths, then optional tagged DDS files\n"); return 2; }
    HMODULE proxy = LoadLibraryA(argv[2]);
    assert(proxy);
    const char* names[] = {"Direct3DCreate9","Direct3DCreate9Ex","D3DPERF_BeginEvent",
        "D3DPERF_EndEvent","D3DPERF_GetStatus","D3DPERF_QueryRepeatFrame","D3DPERF_SetMarker",
        "D3DPERF_SetRegion","D3DPERF_SetOptions","AcvInstallV64"};
    for (const char* name : names) assert(GetProcAddress(proxy,name));
    HMODULE dx = LoadLibraryA(argv[1]); assert(dx);
    original_create = reinterpret_cast<CreateTexture>(GetProcAddress(dx,"D3DXCreateTextureFromFileInMemoryEx"));
    assert(original_create);
    using Factory = IDirect3D9* (WINAPI*)(UINT);
    auto factory = reinterpret_cast<Factory>(GetProcAddress(proxy,"Direct3DCreate9")); assert(factory);
    IDirect3D9* d3d = factory(D3D_SDK_VERSION); assert(d3d);
    D3DADAPTER_IDENTIFIER9 adapter{};
    assert(SUCCEEDED(d3d->GetAdapterIdentifier(D3DADAPTER_DEFAULT,0,&adapter)));
    printf("Adapter: %s\n",adapter.Description);
    fflush(stdout);
    HWND window = CreateWindowExA(0,"STATIC","ACV offline renderer probe",WS_POPUP,0,0,32,32,
                                  nullptr,nullptr,GetModuleHandleW(nullptr),nullptr);
    assert(window);
    D3DPRESENT_PARAMETERS pp{};
    pp.Windowed = TRUE; pp.SwapEffect = D3DSWAPEFFECT_DISCARD;
    pp.hDeviceWindow = window; pp.BackBufferWidth = 32; pp.BackBufferHeight = 32;
    uint8_t dds[128+32] = {};
    memcpy(dds,"DDS ",4);
    auto put = [&](size_t offset,uint32_t value) { memcpy(dds+offset,&value,4); };
    put(4,124); put(8,0x100f); put(12,2); put(16,4); put(20,16);
    put(76,32); put(80,0x41); put(88,32); put(92,0xff0000); put(96,0xff00);
    put(100,0xff); put(104,0xff000000); put(108,0x1000);
    acv::Tag tag = {{'A','C','V','U','I','2','X',0},1,2,1,4,2};
    memcpy(dds+32,&tag,sizeof tag);
    dds[128+3] = 7; dds[128+7] = 255; dds[128+16+15] = 200;
    ImageInfo info{};
    using GetInfo = HRESULT (WINAPI*)(const void*,UINT,ImageInfo*);
    auto get_info = reinterpret_cast<GetInfo>(GetProcAddress(dx,"D3DXGetImageInfoFromFileInMemory"));
    assert(get_info && SUCCEEDED(get_info(dds,sizeof dds,&info)));
    assert(info.Width == 4 && info.Height == 2);
    auto install = reinterpret_cast<BOOL (WINAPI*)()>(GetProcAddress(proxy,"AcvInstallV64"));
    assert(install && !install()); // Exercise the built DLL, not the source copy linked in this probe.
    puts("PASS: x86 DLL exports, real D3DX tagged-DDS parser and fail-closed image guard");
    IDirect3DDevice9* device = nullptr;
    HRESULT device_result = d3d->CreateDevice(0,D3DDEVTYPE_HAL,window,D3DCREATE_SOFTWARE_VERTEXPROCESSING,&pp,&device);
    printf("CreateDevice HRESULT: 0x%08lx\n",static_cast<unsigned long>(device_result));
    fflush(stdout);
    if (FAILED(device_result)) {
        fprintf(stderr,"GPU checks blocked: close full-screen D3D applications and retry; no game was changed.\n");
        d3d->Release(); DestroyWindow(window); FreeLibrary(proxy); FreeLibrary(dx);
        return 3;
    }
    IDirect3DTexture9* texture = nullptr;
    HRESULT result = create_tagged(device,dds,sizeof dds,1,1,1,0,D3DFMT_UNKNOWN,D3DPOOL_MANAGED,
                                   3,3,0,&info,nullptr,&texture,true);
    assert(SUCCEEDED(result) && texture);
    D3DSURFACE_DESC desc{};
    assert(SUCCEEDED(texture->GetLevelDesc(0,&desc)));
    assert(desc.Width == 4 && desc.Height == 2 && info.Width == 2 && info.Height == 1);
    DWORD size = sizeof tag;
    acv::Tag private_tag{};
    assert(SUCCEEDED(texture->GetPrivateData(tag_guid,&private_tag,&size)));
    assert(private_tag.logical_width == 2 && private_tag.physical_width == 4);
    D3DLOCKED_RECT lock{};
    assert(SUCCEEDED(texture->LockRect(0,&lock,nullptr,D3DLOCK_READONLY)));
    uint8_t mask[2];
    assert(acv::build_alpha(mask,private_tag,static_cast<const uint8_t*>(lock.pBits),lock.Pitch,
                           desc.Format,desc.Width,desc.Height));
    assert(mask[0] == 255 && mask[1] == 200);
    texture->UnlockRect(0); texture->Release(); texture = nullptr;
    result = create_tagged(device,dds,sizeof dds,1,1,1,0,D3DFMT_UNKNOWN,D3DPOOL_MANAGED,
                           3,3,0,&info,nullptr,&texture,false);
    assert(SUCCEEDED(result) && texture);
    assert(SUCCEEDED(texture->GetLevelDesc(0,&desc)));
    assert(desc.Width == 1 && desc.Height == 1 && info.Width == 4 && info.Height == 2);
    size = sizeof tag;
    assert(FAILED(texture->GetPrivateData(tag_guid,&private_tag,&size)));
    texture->Release();
    // Reproduce the recorded single-level 640x1024 UI request without the game.
    const UINT ui_width = 640, ui_height = 1024, ui_size = 128+ui_width*ui_height*4;
    auto* ui = static_cast<uint8_t*>(calloc(1,ui_size)); assert(ui);
    memcpy(ui,dds,128);
    acv::put32(ui+12,ui_height); acv::put32(ui+16,ui_width); acv::put32(ui+20,ui_width*4);
    acv::Tag ui_tag = {{'A','C','V','U','I','2','X',0},1,320,512,ui_width,ui_height};
    memcpy(ui+32,&ui_tag,sizeof ui_tag);
    for (UINT pixel=128; pixel<ui_size; pixel+=4) { ui[pixel]=50; ui[pixel+1]=100; ui[pixel+2]=150; ui[pixel+3]=127; }
    auto storage = [](IDirect3DTexture9* value) {
        uint64_t bytes = 0;
        for (UINT level=0; level<value->GetLevelCount(); ++level) {
            D3DSURFACE_DESC level_desc{}; assert(SUCCEEDED(value->GetLevelDesc(level,&level_desc)));
            assert(level_desc.Format == D3DFMT_A8R8G8B8 && level_desc.Pool == D3DPOOL_MANAGED);
            bytes += uint64_t(level_desc.Width)*level_desc.Height*4;
        }
        return bytes;
    };
    IDirect3DTexture9* baseline = nullptr;
    result = original_create(device,ui,ui_size,ui_width,ui_height,0,0,D3DFMT_UNKNOWN,D3DPOOL_MANAGED,
                             3,3,0,&info,nullptr,&baseline);
    assert(SUCCEEDED(result) && baseline && baseline->GetLevelCount() == 11);
    uint64_t before = storage(baseline);
    texture = nullptr;
    result = create_tagged(device,ui,ui_size,1,1,0,0,D3DFMT_UNKNOWN,D3DPOOL_MANAGED,
                          3,3,0,&info,nullptr,&texture,true);
    assert(SUCCEEDED(result) && texture && texture->GetLevelCount() == 1);
    assert(info.Width == 320 && info.Height == 512);
    assert(SUCCEEDED(texture->GetLevelDesc(0,&desc)) && desc.Width == ui_width && desc.Height == ui_height);
    uint64_t after = storage(texture);
    assert(before == 3495244 && after == 2621440);
    D3DLOCKED_RECT baseline_lock{};
    assert(SUCCEEDED(baseline->LockRect(0,&baseline_lock,nullptr,D3DLOCK_READONLY)));
    assert(SUCCEEDED(texture->LockRect(0,&lock,nullptr,D3DLOCK_READONLY)));
    for (UINT row=0; row<ui_height; ++row)
        assert(!memcmp(static_cast<uint8_t*>(baseline_lock.pBits)+row*baseline_lock.Pitch,
                       static_cast<uint8_t*>(lock.pBits)+row*lock.Pitch,ui_width*4));
    texture->UnlockRect(0); baseline->UnlockRect(0);
    printf("PASS: single-level UI stays 640x1024 BGRA8/managed, 11 -> 1 levels, %llu -> %llu texel bytes; level-zero RGBA unchanged\n",
           static_cast<unsigned long long>(before),static_cast<unsigned long long>(after));
    baseline->Release(); texture->Release(); free(ui);
    for (int argument=3; argument<argc; ++argument) {
        FILE* file = fopen(argv[argument],"rb"); assert(file);
        assert(!fseek(file,0,SEEK_END));
        long length = ftell(file); assert(length >= 128 && length <= 64*1024*1024);
        rewind(file);
        auto* input = static_cast<uint8_t*>(malloc(size_t(length))); assert(input);
        assert(fread(input,1,size_t(length),file) == size_t(length)); fclose(file);
        acv::Tag sample{}; assert(acv::parse_tag(input,size_t(length),sample));
        uint32_t levels = acv::u32(input+28); if (!levels) levels = 1;
        texture = nullptr;
        result = create_tagged(device,input,UINT(length),1,1,levels,0,D3DFMT_UNKNOWN,
            D3DPOOL_MANAGED,3,3,0,&info,nullptr,&texture,true);
        assert(SUCCEEDED(result) && texture);
        assert(SUCCEEDED(texture->GetLevelDesc(0,&desc)));
        assert(desc.Width == sample.physical_width && desc.Height == sample.physical_height &&
               info.Width == sample.logical_width && info.Height == sample.logical_height &&
               texture->GetLevelCount() == levels);
        assert(SUCCEEDED(texture->LockRect(0,&lock,nullptr,D3DLOCK_READONLY)));
        auto* alpha = static_cast<uint8_t*>(malloc(size_t(sample.logical_width)*sample.logical_height));
        assert(alpha && acv::build_alpha(alpha,sample,static_cast<const uint8_t*>(lock.pBits),
               lock.Pitch,desc.Format,desc.Width,desc.Height));
        free(alpha); texture->UnlockRect(0); texture->Release(); free(input);
        printf("PASS: tagged file GPU dimensions, %u mips and native alpha: %s\n",levels,argv[argument]);
    }
    device->Release(); d3d->Release(); DestroyWindow(window); FreeLibrary(proxy); FreeLibrary(dx);
    puts("PASS: x86 DLL exports, actual D3DX DDS parsing, physical GPU size, logical metadata, alpha mapping, untagged path, fail-closed image guard");
}
