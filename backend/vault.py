"""Secrets are encrypted for the current Windows user using DPAPI."""

import ctypes
import json
import os
from ctypes import wintypes
from uuid import uuid4

from .config import DATA


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def transform(data: bytes, decrypt=False) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Хранилище секретов требует Windows DPAPI")
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt = ctypes.windll.crypt32
    if decrypt:
        ok = crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
    else:
        ok = crypt.CryptProtectData(
            ctypes.byref(source), "SurvivalFlow", None, None, None, 1, ctypes.byref(output)
        )
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.windll.kernel32.LocalFree(output.data)


def secret_path(key):
    if not key.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Некорректный ключ секрета")
    return DATA / "secrets" / f"{key}.dpapi"


def put(key, value):
    path = secret_path(key)
    temporary = path.with_suffix("." + uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(transform(json.dumps(value).encode()))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def get(key):
    path = secret_path(key)
    return json.loads(transform(path.read_bytes(), decrypt=True)) if path.exists() else None


def remove(key):
    secret_path(key).unlink(missing_ok=True)
