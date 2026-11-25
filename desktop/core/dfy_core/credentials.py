# -*- coding: utf-8 -*-
"""DPAPI-protected local secrets (design §7.2: API Key / WebUI token / Arena 凭据).

Only the current Windows user can decrypt what we write, and nothing plaintext ever
hits the disk. The API deliberately has no "read back" route: the GUI can store,
list (fingerprint only) and clear a secret; the core reads it at the moment of use.
If DPAPI is unavailable we refuse instead of quietly falling back to plaintext.
"""
import base64
import ctypes
import hashlib
import json
import os
import time
import ctypes.wintypes as wt

from . import audit, config, security

# Domain separation: a blob copied next to a different app won't open without it.
ENTROPY = b"dafeiyu-desktop-credentials-v1"
UI_FORBIDDEN = 0x00000001
CURRENT_USER = 0x00000000


class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _in_blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _avail():
    return os.name == "nt" and hasattr(ctypes.windll, "crypt32")


def protect(plain: bytes):
    """bytes -> DPAPI blob, or None when this platform can't do it."""
    if not _avail() or plain is None:
        return None
    crypt32 = ctypes.windll.crypt32
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_BLOB), wt.LPCWSTR,
                                         ctypes.POINTER(_BLOB), ctypes.c_void_p,
                                         ctypes.c_void_p, wt.DWORD, ctypes.POINTER(_BLOB)]
    crypt32.CryptProtectData.restype = wt.BOOL
    inb, keep1 = _in_blob(plain)
    ent, keep2 = _in_blob(ENTROPY)
    out = _BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(inb), None, ctypes.byref(ent),
                                    None, None, UI_FORBIDDEN, ctypes.byref(out)):
        return None
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)
        del keep1, keep2


def unprotect(blob: bytes):
    if not _avail() or not blob:
        return None
    crypt32 = ctypes.windll.crypt32
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_BLOB), ctypes.POINTER(wt.LPCWSTR),
                                           ctypes.POINTER(_BLOB), ctypes.c_void_p,
                                           ctypes.c_void_p, wt.DWORD, ctypes.POINTER(_BLOB)]
    crypt32.CryptUnprotectData.restype = wt.BOOL
    inb, keep1 = _in_blob(blob)
    ent, keep2 = _in_blob(ENTROPY)
    desc = wt.LPWSTR()
    out = _BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(inb), ctypes.byref(desc), ctypes.byref(ent),
                                      None, None, UI_FORBIDDEN, ctypes.byref(out)):
        return None
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)
        del keep1, keep2


def store_dir():
    return os.path.join(config.runtime_dir(), "credentials")


def _slot(name):
    if not isinstance(name, str) or not _ok_name(name):
        return None
    return os.path.join(store_dir(), name + ".json")


def _ok_name(name):
    import re
    return bool(re.match(r"^[a-z0-9][a-z0-9_.-]{0,40}$", name or ""))


def _write_atomic(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def save(name, value, label=""):
    """Encrypt and store. `value` is scrubbed everywhere else in the process right away."""
    path = _slot(name) if isinstance(name, str) else None
    if not path:
        return {"error": "凭据名字只允许小写字母、数字和 . _ -（1-41 位）"}
    value = "" if value is None else str(value)
    if not value.strip():
        return {"error": "空值没什么好存的；要清掉请用清除"}
    if not _avail():
        return {"error": "这台机器用不了 Windows DPAPI，我不把密钥写成明文"}
    blob = protect(value.encode("utf-8"))
    if not blob:
        return {"error": "DPAPI 加密失败，没有写入任何东西"}
    # register the secret for redaction before anything else can echo it
    security.add_scrub(value)
    doc = {"name": name, "kind": "dpapi-current-user", "chars": len(value),
           "sha8": hashlib.sha1(value.encode("utf-8")).hexdigest()[:8],
           "label": str(label or "")[:60], "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "blob": base64.b64encode(blob).decode("ascii")}
    os.makedirs(store_dir(), exist_ok=True)
    _write_atomic(path, json.dumps(doc, ensure_ascii=False, indent=1))
    audit.write("credential_save", name=name, chars=len(value))
    return {"ok": True, "name": name, "chars": len(value), "sha8": doc["sha8"],
            "note": "只有当前 Windows 用户能解开；界面上不再回显内容"}


def open_secret(name):
    """Plaintext, for internal use at the moment of a call. Never route this out."""
    path = _slot(name) if isinstance(name, str) else None
    if not path or not os.path.isfile(path):
        return None
    try:
        doc = json.load(open(path, encoding="utf-8"))
        blob = base64.b64decode(doc.get("blob") or "")
    except Exception:
        return None
    plain = unprotect(blob)
    if not plain:
        return None
    # DPAPI gives confidentiality, not integrity: a byte flipped in the blob may still
    # "decrypt" (into garbage, or into the original if the flip landed in the header).
    # This hash catches corruption and half-written saves. It is NOT a defence against
    # someone who can already read this file as the same Windows user — they could
    # decrypt the blob anyway; nothing local can stop that, which is why the file lives
    # in the user's own app-data dir and never leaves it.
    want = doc.get("sha8")
    if want and hashlib.sha1(plain).hexdigest()[:8] != want:
        return None
    return plain.decode("utf-8", "replace")


def list_slots():
    out = []
    d = store_dir()
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json"):
                continue
            try:
                doc = json.load(open(os.path.join(d, fn), encoding="utf-8"))
            except Exception:
                doc = {}
            out.append({"name": doc.get("name") or fn[:-5], "chars": doc.get("chars"),
                         "sha8": doc.get("sha8"), "label": doc.get("label"),
                         "updated_at": doc.get("updated_at"),
                         "algo": doc.get("kind")})
    return {"items": out, "count": len(out), "protected_by": "Windows DPAPI（当前用户）",
            "note": "这里只列指纹和长度，内容不回显"}


def clear(name, confirm=""):
    path = _slot(name) if isinstance(name, str) else None
    if not path:
        return {"error": "凭据名字不合法"}
    if not os.path.isfile(path):
        return {"error": "没有这条凭据"}
    if str(confirm).lower() not in ("yes", "true", "1", "y", "确认"):
        return {"confirm_required": True, "will": "删除本地保存的 %s（只删这个 App 自己存的密文）" % name}
    try:
        os.remove(path)
    except Exception as e:
        return {"error": "删不掉：%s" % str(e)[:120]}
    audit.write("credential_clear", name=name)
    return {"ok": True, "name": name}
