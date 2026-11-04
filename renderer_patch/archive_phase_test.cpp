#include "archive_phase.hpp"
#include <stdio.h>

static ArchivePhase phase;
extern "C" int archive_phase_unwind_test(void);
extern "C" void __cdecl archive_phase_test_run(void* context, void (__cdecl *body)(void*)) {
    phase.run(context, body);
}
struct State { LONG active = 0, failures = 0; unsigned calls = 0; };
static void __cdecl work(void* context) {
    auto* state = static_cast<State*>(context);
    if (InterlockedIncrement(&state->active) != 1) InterlockedIncrement(&state->failures);
    const unsigned before = state->calls;
    SwitchToThread();
    state->calls = before + 1;
    if (InterlockedDecrement(&state->active) != 0) InterlockedIncrement(&state->failures);
}
static DWORD WINAPI worker(void* context) {
    for (unsigned i=0;i<256;++i) phase.run(context,work);
    return 0;
}
struct Contention { HANDLE held, release, attempted, entered; };
static void __cdecl hold(void* context) {
    auto* state = static_cast<Contention*>(context);
    if (!SetEvent(state->held) || WaitForSingleObject(state->release,5000) != WAIT_OBJECT_0)
        ExitProcess(6);
}
static void __cdecl enter(void* context) {
    if (!SetEvent(static_cast<Contention*>(context)->entered)) ExitProcess(7);
}
static DWORD WINAPI holder(void* context) { phase.run(context,hold); return 0; }
static DWORD WINAPI contender(void* context) {
    if (!SetEvent(static_cast<Contention*>(context)->attempted)) ExitProcess(8);
    phase.run(context,enter);
    return 0;
}
static void contention_test() {
    Contention state{CreateEventW(nullptr,TRUE,FALSE,nullptr), CreateEventW(nullptr,TRUE,FALSE,nullptr),
                     CreateEventW(nullptr,TRUE,FALSE,nullptr), CreateEventW(nullptr,TRUE,FALSE,nullptr)};
    if (!state.held || !state.release || !state.attempted || !state.entered) ExitProcess(9);
    HANDLE threads[2]{CreateThread(nullptr,0,holder,&state,0,nullptr),nullptr};
    if (!threads[0] || WaitForSingleObject(state.held,5000) != WAIT_OBJECT_0) ExitProcess(10);
    threads[1] = CreateThread(nullptr,0,contender,&state,0,nullptr);
    if (!threads[1] || WaitForSingleObject(state.attempted,5000) != WAIT_OBJECT_0) ExitProcess(11);
    if (WaitForSingleObject(state.entered,100) != WAIT_TIMEOUT) ExitProcess(12);
    if (!SetEvent(state.release) || WaitForMultipleObjects(2,threads,TRUE,5000) != WAIT_OBJECT_0 ||
        WaitForSingleObject(state.entered,0) != WAIT_OBJECT_0) ExitProcess(13);
    for (auto thread : threads) if (!CloseHandle(thread)) ExitProcess(14);
    const HANDLE events[]{state.held,state.release,state.attempted,state.entered};
    for (auto event : events) if (!CloseHandle(event)) ExitProcess(15);
}
int main() {
    if (archive_phase_unwind_test()) return 1;
    contention_test();
    State state;
    HANDLE threads[4]{};
    for (auto& thread : threads) {
        thread = CreateThread(nullptr,0,worker,&state,0,nullptr);
        if (!thread) ExitProcess(2);
    }
    // A leaked lock fails this bounded wait rather than hanging the test forever.
    if (WaitForMultipleObjects(4,threads,TRUE,10000) != WAIT_OBJECT_0) ExitProcess(3);
    for (auto thread : threads) if (!CloseHandle(thread)) return 4;
    if (state.active || state.failures || state.calls != 1024) return 5;
    puts("Archive phase serialization: PASS (contention exclusion, 1024 concurrent calls, unwind release, recursive refusal)");
    return 0;
}
