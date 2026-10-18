"""Load a DLL (default tools/bhs/bhs.dll) into a running Dead Star instance.

Usage: python -I tools/inject.py [INSTANCE] [DLL]   (default server)

The DLL is first copied to game/<instance>/bhs/bhs-<time>.dll, so it can be rebuilt while an older
copy is still loaded. The copy is loaded with LoadLibraryW from a remote thread.
"""
import ctypes
import ctypes.wintypes as wt
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_state  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.restype = wt.HANDLE
k32.VirtualAllocEx.restype = ctypes.c_void_p
k32.VirtualAllocEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wt.DWORD, wt.DWORD]
k32.WriteProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                   ctypes.c_void_p]
k32.GetModuleHandleW.restype = ctypes.c_void_p
k32.GetProcAddress.restype = ctypes.c_void_p
k32.GetProcAddress.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
k32.CreateRemoteThread.restype = wt.HANDLE
k32.CreateRemoteThread.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                                   ctypes.c_void_p, wt.DWORD, ctypes.c_void_p]


def main():
    instance = sys.argv[1] if len(sys.argv) > 1 else "server"
    dll = Path(sys.argv[2] if len(sys.argv) > 2 else REPO / "tools/bhs/bhs.dll")
    copy = REPO / "game" / instance / "bhs" / f"bhs-{int(time.time())}.dll"
    copy.parent.mkdir(exist_ok=True)
    shutil.copy(dll, copy)
    pid = ai_state.find_process(instance)
    h = k32.OpenProcess(0x043A, False, pid)  # CREATE_THREAD | VM_OPERATION | VM_READ | VM_WRITE | QUERY
    path = ctypes.create_unicode_buffer(str(copy))
    size = ctypes.sizeof(path)
    remote = k32.VirtualAllocEx(h, None, size, 0x3000, 0x04)  # MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE
    k32.WriteProcessMemory(h, remote, path, size, None)
    load = k32.GetProcAddress(k32.GetModuleHandleW("kernel32.dll"), b"LoadLibraryW")
    thread = k32.CreateRemoteThread(h, None, 0, load, remote, 0, None)
    if not thread:
        raise SystemExit(f"CreateRemoteThread failed: {ctypes.get_last_error()}")
    k32.WaitForSingleObject(thread, 10000)
    code = wt.DWORD()
    k32.GetExitCodeThread(thread, ctypes.byref(code))
    print(f"injected {copy.name} into pid {pid}" if code.value else "LoadLibraryW failed")


if __name__ == "__main__":
    main()
