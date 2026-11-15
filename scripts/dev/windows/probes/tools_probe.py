"""W0 measurements for the Windows port: IPC, capture, terminal, login check.

Run it in the desktop session with the probe virtualenv, which has dxcam,
pywinpty, pyte and comtypes (these are measurement tools, not pcbridge
dependencies):

    windows-vm.sh session "%USERPROFILE%\\probe-venv\\Scripts\\python ^
        scripts\\dev\\windows\\probes\\tools_probe.py"

The login check reads the test account's password from PCB_TEST_PASSWORD and
never prints it. Prints JSON.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as W
import getpass
import json
import os
import threading
import time

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
PIPE_ACCESS_DUPLEX = 0x3
FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
PIPE_TYPE_BYTE = 0x0
PIPE_REJECT_REMOTE_CLIENTS = 0x8


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", W.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                ("bInheritHandle", W.BOOL)]


kernel32.CreateNamedPipeW.restype = W.HANDLE
kernel32.CreateNamedPipeW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, W.DWORD, W.DWORD,
                                      W.DWORD, W.DWORD, ctypes.POINTER(SECURITY_ATTRIBUTES)]
kernel32.ConnectNamedPipe.argtypes = [W.HANDLE, ctypes.c_void_p]
kernel32.ReadFile.argtypes = [W.HANDLE, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD), ctypes.c_void_p]
kernel32.WriteFile.argtypes = [W.HANDLE, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD), ctypes.c_void_p]
kernel32.CloseHandle.argtypes = [W.HANDLE]
kernel32.DisconnectNamedPipe.argtypes = [W.HANDLE]
# Handles are 64-bit: without these declarations ctypes passes C ints and the
# calls fail quietly (the first run read no SID at all).
kernel32.GetCurrentProcess.restype = W.HANDLE
kernel32.LocalFree.argtypes = [ctypes.c_void_p]
advapi32.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, ctypes.POINTER(W.HANDLE)]
advapi32.GetTokenInformation.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD,
                                         ctypes.POINTER(W.DWORD)]
advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(W.LPWSTR)]
advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
    W.LPCWSTR, W.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
advapi32.LogonUserW.argtypes = [W.LPCWSTR, W.LPCWSTR, W.LPCWSTR, W.DWORD, W.DWORD,
                                ctypes.POINTER(W.HANDLE)]


def current_user_sid() -> str:
    token = W.HANDLE()
    advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token))  # TOKEN_QUERY
    size = W.DWORD()
    advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))  # TokenUser
    buf = ctypes.create_string_buffer(size.value)
    advapi32.GetTokenInformation(token, 1, buf, size, ctypes.byref(size))
    psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
    sid_str = W.LPWSTR()
    advapi32.ConvertSidToStringSidW(ctypes.c_void_p(psid), ctypes.byref(sid_str))
    value = sid_str.value
    kernel32.LocalFree(sid_str)
    kernel32.CloseHandle(token)
    return value


def security_attributes(sddl: str) -> SECURITY_ATTRIBUTES:
    sd = ctypes.c_void_p()
    ok = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        ctypes.c_wchar_p(sddl), 1, ctypes.byref(sd), None)
    if not ok:
        raise OSError(ctypes.get_last_error(), "ConvertStringSecurityDescriptor failed")
    return SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), sd, False)


def pipe_probe() -> dict:
    out: dict = {}
    sid = current_user_sid()
    out["user_sid_prefix"] = sid.rsplit("-", 1)[0] + "-..."
    # Protected DACL: full access for this user only. No inherited entries,
    # so Administrators and SYSTEM get nothing either.
    sa = security_attributes(f"D:P(A;;GA;;;{sid})")
    name = f"\\\\.\\pipe\\pcbridge-probe-{os.getpid()}"
    flags = PIPE_ACCESS_DUPLEX | FILE_FLAG_FIRST_PIPE_INSTANCE
    mode = PIPE_TYPE_BYTE | PIPE_REJECT_REMOTE_CLIENTS
    server = kernel32.CreateNamedPipeW(name, flags, mode, 4, 65536, 65536, 0, ctypes.byref(sa))
    out["create_ok"] = server != INVALID_HANDLE_VALUE
    if not out["create_ok"]:
        out["create_error"] = ctypes.get_last_error()
        return out
    # A second "first instance" with the same name must fail: that is the
    # protection against another process creating the pipe before us.
    squat = kernel32.CreateNamedPipeW(name, flags, mode, 4, 65536, 65536, 0, ctypes.byref(sa))
    out["second_first_instance_refused"] = squat == INVALID_HANDLE_VALUE
    out["second_first_instance_error"] = ctypes.get_last_error()
    if squat != INVALID_HANDLE_VALUE:
        kernel32.CloseHandle(squat)

    rounds = 2000
    payload = b"x" * 200

    def serve() -> None:
        kernel32.ConnectNamedPipe(server, None)
        buf = ctypes.create_string_buffer(65536)
        n = W.DWORD()
        for _ in range(rounds):
            kernel32.ReadFile(server, buf, 65536, ctypes.byref(n), None)
            kernel32.WriteFile(server, buf, n.value, ctypes.byref(n), None)
        kernel32.DisconnectNamedPipe(server)

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    time.sleep(0.1)
    with open(name, "r+b", buffering=0) as client:
        start = time.perf_counter()
        for _ in range(rounds):
            client.write(payload)
            client.read(len(payload))
        elapsed = time.perf_counter() - start
    t.join(5)
    kernel32.CloseHandle(server)
    out["round_trip_us"] = round(elapsed / rounds * 1e6, 1)
    return out


def dxgi_probe() -> dict:
    try:
        import dxcam
    except Exception as exc:  # noqa: BLE001 - a measurement reports every failure
        return {"error": f"import: {exc}"}
    out: dict = {}
    try:
        out["outputs"] = dxcam.output_info()
        cam = dxcam.create(output_color="BGRA")  # no color conversion, so no OpenCV
        times = []
        frame = None
        for _ in range(6):
            t = time.perf_counter()
            got = cam.grab()
            times.append(round((time.perf_counter() - t) * 1000, 2))
            frame = got if got is not None else frame
            time.sleep(0.1)
        out["grab_ms"] = times
        out["frame_shape"] = None if frame is None else list(frame.shape)
        out["none_frames"] = sum(1 for _ in times) - (0 if frame is None else 1)
        del cam
    except Exception as exc:  # noqa: BLE001
        out["error"] = repr(exc)
    return out


def conpty_probe() -> dict:
    try:
        import pyte
        from winpty import PtyProcess
    except Exception as exc:  # noqa: BLE001
        return {"error": f"import: {exc}"}
    out: dict = {}
    screen = pyte.Screen(100, 30)
    stream = pyte.Stream(screen)
    proc = PtyProcess.spawn("cmd.exe", dimensions=(30, 100))
    start = time.monotonic()

    def pump(seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                data = proc.read(4096)
            except EOFError:
                return
            if data:
                stream.feed(data)
            else:
                time.sleep(0.05)

    reader = threading.Thread(target=pump, args=(4.0,), daemon=True)
    reader.start()
    time.sleep(1.0)
    proc.write("echo pcbridge-conpty-%COMPUTERNAME%\r\n")
    proc.write("for /l %i in (1,1,3) do @echo line %i\r\n")
    reader.join(6)
    lines = [line.rstrip() for line in screen.display if line.strip()]
    out["screen_lines"] = lines[-8:]
    out["marker_seen"] = any("pcbridge-conpty-" in line and "echo" not in line for line in lines)
    out["alive_before_exit"] = proc.isalive()
    proc.write("exit\r\n")
    time.sleep(1.0)
    out["alive_after_exit"] = proc.isalive()
    out["elapsed_s"] = round(time.monotonic() - start, 2)
    return out


def logon_probe() -> dict:
    password = os.environ.get("PCB_TEST_PASSWORD")
    if not password:
        return {"skipped": "PCB_TEST_PASSWORD not set"}
    out: dict = {"user": getpass.getuser()}
    for label, value in (("right", password), ("wrong", password + "x")):
        token = W.HANDLE()
        t = time.perf_counter()
        ok = advapi32.LogonUserW(ctypes.c_wchar_p(out["user"]), ctypes.c_wchar_p("."),
                                 ctypes.c_wchar_p(value), 2, 0, ctypes.byref(token))  # INTERACTIVE, DEFAULT
        out[f"{label}_ok"] = bool(ok)
        out[f"{label}_error"] = 0 if ok else ctypes.get_last_error()
        out[f"{label}_ms"] = round((time.perf_counter() - t) * 1000, 1)
        if ok:
            kernel32.CloseHandle(token)
    return out


def main() -> None:
    result = {}
    for name, fn in (("pipe", pipe_probe), ("dxgi", dxgi_probe),
                     ("conpty", conpty_probe), ("logon", logon_probe)):
        try:
            result[name] = fn()
        except Exception as exc:  # noqa: BLE001
            result[name] = {"error": repr(exc)}
    print(json.dumps(result, indent=1, default=str))


if __name__ == "__main__":
    main()
