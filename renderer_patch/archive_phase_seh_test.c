#include "archive_phase_seh.h"

__declspec(dllimport) void __stdcall RaiseException(unsigned long, unsigned long,
                                                  unsigned long, const unsigned long*);
unsigned long __cdecl _exception_code(void);
struct State { int held, leaves, steps; };
static int failures, order;
static struct State inner;
static int filter(struct State* state, int disposition, unsigned long code) {
    if (code != 0xe0421234) return 0;
    if (state->held != 1 || state->leaves) ++failures;
    return disposition;
}
static int nested_filter(struct State* outer, unsigned long code) {
    filter(&inner,1,code);
    return filter(outer,1,code);
}

static void __cdecl leave(void* context) {
    struct State* state = context;
    if (state->held != 1 || state->leaves) ++failures;
    state->held = 0;
    ++state->leaves;
}
static void __cdecl normal(void* context) {
    struct State* state = context;
    if (state->held != 1 || state->leaves) ++failures;
    ++state->steps;
}
static void __cdecl handled(void* context) {
    struct State* state = context;
    __try { RaiseException(0xe0421234,0,0,0); }
    __except (filter(state,1,_exception_code())) { ++state->steps; }
    if (state->held != 1 || state->leaves) ++failures;
    ++state->steps;
}
static void __cdecl continued(void* context) {
    struct State* state = context;
    __try { RaiseException(0xe0421234,0,0,0); }
    __except (filter(state,-1,_exception_code())) { ++failures; }
    if (state->held != 1 || state->leaves) ++failures;
    ++state->steps;
}
static void __cdecl escaping(void* context) {
    normal(context);
    RaiseException(0xe0421234,0,0,0);
    ++failures;
}
static void __cdecl inner_leave(void* context) { leave(context); if (++order != 1) ++failures; }
static void __cdecl outer_leave(void* context) { leave(context); if (++order != 2) ++failures; }
static void __cdecl nested(void* context) {
    normal(context);
    acv_archive_phase_run(&inner,escaping,inner_leave);
}

int main(void) {
    int i, caught = 0;
    void (__cdecl *bodies[])(void*) = { normal, handled, continued };
    for (i=0;i<3;++i) {
        struct State state = {1,0,0};
        acv_archive_phase_run(&state,bodies[i],leave);
        if (state.held || state.leaves != 1 || state.steps != (i == 1 ? 2 : 1)) ++failures;
    }
    {
        struct State state = {1,0,0};
        __try { acv_archive_phase_run(&state,escaping,leave); }
        __except (filter(&state,1,_exception_code())) {
            if (state.held || state.leaves != 1) ++failures;
            ++caught;
        }
        if (state.held || state.leaves != 1 || state.steps != 1 || caught != 1) ++failures;
    }
    {
        struct State outer = {1,0,0};
        inner.held = 1;
        __try { acv_archive_phase_run(&outer,nested,outer_leave); }
        __except (nested_filter(&outer,_exception_code())) {
            if (outer.held || inner.held || order != 2) ++failures;
            ++caught;
        }
        if (outer.held || inner.held || outer.leaves != 1 || inner.leaves != 1 ||
            order != 2 || caught != 2) ++failures;
    }
    return failures ? 1 : 0;
}
