#pragma once
#include <windows.h>
#include <stdint.h>

namespace acv {
using ArchiveReadApi = BOOL (WINAPI*)(HANDLE,LPVOID,DWORD,LPDWORD,LPOVERLAPPED);

// A private overlapped handle never changes the native handle's shared file position.
inline bool archive_read_at(ArchiveReadApi read,HANDLE source,uint32_t offset,void* data,
    uint32_t size,uint32_t* received,DWORD& error) {
    *received = 0;
    HANDLE file = ReOpenFile(source,GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,
        FILE_FLAG_OVERLAPPED|FILE_FLAG_RANDOM_ACCESS);
    if (file == INVALID_HANDLE_VALUE) { error = GetLastError(); return false; }
    OVERLAPPED operation{};
    operation.Offset = offset;
    BOOL started = read(file,data,size,nullptr,&operation);
    DWORD failure = started ? ERROR_SUCCESS : GetLastError();
    DWORD count = 0;
    BOOL ok = FALSE;
    if (started || failure == ERROR_IO_PENDING) {
        ok = GetOverlappedResult(file,&operation,&count,TRUE);
        failure = ok ? ERROR_SUCCESS : GetLastError();
    }
    if (!CloseHandle(file) && ok) { ok = FALSE; failure = GetLastError(); }
    if (!ok) { error = failure; return false; }
    *received = count;
    error = count == size ? ERROR_SUCCESS : ERROR_HANDLE_EOF;
    return count == size;
}
} // namespace acv
