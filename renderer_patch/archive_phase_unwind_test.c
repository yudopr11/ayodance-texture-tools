__declspec(dllimport) void __stdcall RaiseException(unsigned long, unsigned long,
                                                  unsigned long, const unsigned long*);
void __cdecl archive_phase_test_run(void* context, void (__cdecl *body)(void*));
void* __cdecl _exception_info(void);
struct ExceptionRecord { unsigned long code, flags; };
struct ExceptionPointers { struct ExceptionRecord* record; void* context; };
static int expected(void* information, unsigned long code, unsigned long flags) {
    struct ExceptionRecord* record = ((struct ExceptionPointers*)information)->record;
    return record->code == code && (record->flags & 1) == flags;
}
static int steps;
static void __cdecl normal(void* context) { (void)context; ++steps; }
static void __cdecl escaping(void* context) {
    normal(context);
    RaiseException(0xe0421234,0,0,0);
}
static void __cdecl recursive(void* context) {
    archive_phase_test_run(context,normal);
}
static void __cdecl handles_recursive(void* context) {
    __try { archive_phase_test_run(context,normal); }
    __except (expected(_exception_info(),0xe042ac01,1)) { ++steps; }
    ++steps;
}
int archive_phase_unwind_test(void) {
    int caught = 0;
    __try { archive_phase_test_run(0,escaping); }
    __except (expected(_exception_info(),0xe0421234,0)) { ++caught; }
    __try { archive_phase_test_run(0,normal); }
    __except (1) { return 1; }
    __try { archive_phase_test_run(0,recursive); }
    __except (expected(_exception_info(),0xe042ac01,1)) { ++caught; }
    __try { archive_phase_test_run(0,handles_recursive); }
    __except (1) { return 2; }
    __try { archive_phase_test_run(0,normal); }
    __except (1) { return 3; }
    return caught == 2 && steps == 5 ? 0 : 4;
}
