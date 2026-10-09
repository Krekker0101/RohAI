"""Single browser, seven-day READ-ONLY grant. Never grants physical or setup authority."""

import ctypes
import hashlib
import json
import os
import secrets
import time
from collections.abc import Callable
from pathlib import Path

from smart_traffic_backend.maket.calibration import atomic_write


class Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def protect(data: bytes, *, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        return data
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    operation = library.CryptUnprotectData if decrypt else library.CryptProtectData
    operation.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(Blob),
    ]
    operation.restype = ctypes.c_int
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise OSError("Local viewer pairing protection failed")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.memset(output.data, 0, output.size)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


class ViewerPairing:
    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.path, self.clock = path, clock
        self.digest: str | None = None
        self.expiry = 0.0
        if path.exists():
            state = json.loads(protect(path.read_bytes(), decrypt=True))
            if state.get("scope") != "VIEW_ONLY" or state.get("version") != 1:
                raise ValueError("Unsupported viewer pairing store")
            self.digest, self.expiry = state["digest"], state["expiry"]

    def issue(self) -> str:
        token = secrets.token_urlsafe(32)
        self.digest = hashlib.sha256(token.encode()).hexdigest()
        self.expiry = self.clock() + 7 * 86400
        self._save()
        return token

    def valid(self, token: str) -> bool:
        return bool(
            token
            and len(token) < 256
            and self.digest
            and self.clock() < self.expiry
            and secrets.compare_digest(hashlib.sha256(token.encode()).hexdigest(), self.digest)
        )

    def _save(self) -> None:
        data = json.dumps(
            {"scope": "VIEW_ONLY", "version": 1, "digest": self.digest, "expiry": self.expiry}
        ).encode()
        atomic_write(self.path, protect(data))
        if os.name != "nt":
            self.path.chmod(0o600)

    def revoke(self) -> None:
        self.digest, self.expiry = None, 0.0
        self._save()
