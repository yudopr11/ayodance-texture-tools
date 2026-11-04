#include "archive_phase_seh.h"

// Compile this C-only boundary with the MSVC target: GNU-target x86 finally code
// generation is broken in the pinned compiler. Link the COFF object via the C ABI.
void __cdecl acv_archive_phase_run(void* context, void (__cdecl *body)(void*),
                                 void (__cdecl *leave)(void*)) {
    __try {
        body(context);
    } __finally {
        leave(context);
    }
}
