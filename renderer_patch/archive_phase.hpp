#pragma once
#include <windows.h>
#include "archive_phase_seh.h"

// Serializes whole index/open/query/teardown calls, not just their mapping APIs.
// Install only at a verified startup boundary; this cannot drain unhooked callers.
class ArchivePhase {
    CRITICAL_SECTION lock_{};
    bool active_ = false;
    struct Call {
        ArchivePhase* phase;
        void* context;
        void (__cdecl *body)(void*);
    };
    static void __cdecl body(void* context) {
        auto* call = static_cast<Call*>(context);
        call->body(call->context);
    }
    static void __cdecl leave(void* context) {
        auto* phase = static_cast<Call*>(context)->phase;
        phase->active_ = false;
        LeaveCriticalSection(&phase->lock_);
    }
public:
    ArchivePhase() { InitializeCriticalSection(&lock_); }
    ~ArchivePhase() { DeleteCriticalSection(&lock_); }
    ArchivePhase(const ArchivePhase&) = delete;
    ArchivePhase& operator=(const ArchivePhase&) = delete;

    void run(void* context, void (__cdecl *callback)(void*)) {
        EnterCriticalSection(&lock_);
        if (active_) {
            // A recursive lock must not allow resource reads of a published index prefix.
            LeaveCriticalSection(&lock_);
            RaiseException(0xe042ac01u, EXCEPTION_NONCONTINUABLE, 0, nullptr);
            return;
        }
        active_ = true;
        Call call{this, context, callback};
        acv_archive_phase_run(&call, body, leave);
    }
};
