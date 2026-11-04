#pragma once

#ifdef __cplusplus
extern "C" {
#endif

// Caller acquires ownership before entry; leave runs on normal return or actual unwind.
void __cdecl acv_archive_phase_run(void* context, void (__cdecl *body)(void*),
                                 void (__cdecl *leave)(void*));

#ifdef __cplusplus
}
#endif
