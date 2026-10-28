"""Trace the fatal-report argument in the isolated loader; never starts the game."""

import sys
from collections import deque
from pathlib import Path

from unicorn.unicorn_const import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.x86_const import UC_X86_REG_EIP, UC_X86_REG_ESP

from .emulate_client_loader import LOADER, Probe

REPORT_CODE = 0x2ee74afa


class FailureTrace(Probe):
    def __init__(self, log):
        super().__init__(log)
        self.late = deque(maxlen=32768)
        self.tracing = False
        self.found = False
        self.trace_hooks = [
            self.uc.hook_add(UC_HOOK_CODE, self.instruction, begin=LOADER, end=0x4189fff),
            self.uc.hook_add(UC_HOOK_MEM_WRITE, self.write, begin=0x20000000, end=0x2001ffff),
        ]

    def instruction(self, uc, address, size, _):
        if self.tracing and not self.found:
            self.late.append((address, bytes(uc.mem_read(address, size)).hex(), self.state()))

    def write(self, uc, access, address, size, value, _):
        if not self.tracing or size != 4 or value != REPORT_CODE or self.found:
            return
        self.found = True
        self.say(f"FATAL_REPORT_ARGUMENT pc={uc.reg_read(UC_X86_REG_EIP):08x} destination={address:08x}")
        for pc, code, state in self.late:
            self.say(f"LATE {pc:08x} {code} {state}")
        self.stop("Fatal report argument captured")

    def on_api(self, uc, address, size, _):
        if self.apis.get(address) == "RegOpenKeyA" and not self.tracing:
            esp = uc.reg_read(UC_X86_REG_ESP)
            key = self.text(self.u32(esp + 8)).lower()
            if key.startswith("hardware\\acpi\\"):
                self.tracing = True
                self.say("LATE_TRACE started after ACPI metadata query")
        super().on_api(uc, address, size, _)


def main():
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=False)
    with (output / "trace.txt").open("w", encoding="utf-8") as log:
        probe = FailureTrace(log)
        image = probe.run()
        (output / "image.bin").write_bytes(image)
        print(probe.reason)
        assert probe.found, "Fatal report code was not observed; inspect this run instead of assuming the same failure"


if __name__ == "__main__":
    main()
