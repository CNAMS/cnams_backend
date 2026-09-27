"""Field encryption for names (FR-SRV-9/C9). Fernet when NAME_ENC_KEY set,
else dev-plaintext with 'plain:' marker so prod misconfig is visible, never silent.

KEY: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
"""
from app.config import settings

_MARK = b"plain:"


def _fernet():
    key = getattr(settings, "name_enc_key", "") or ""
    if not key:
        return None
    from cryptography.fernet import Fernet
    return Fernet(key.encode())


def encrypt_name(name: str) -> bytes:
    f = _fernet()
    if f is None:
        return _MARK + name.encode("utf-8")
    return b"enc:" + f.encrypt(name.encode("utf-8"))


def decrypt_name(blob: bytes) -> str:
    b = bytes(blob or b"")
    if b.startswith(b"enc:"):
        f = _fernet()
        if f is None:
            return ""
        return f.decrypt(b[4:]).decode("utf-8")
    if b.startswith(_MARK):
        return b[len(_MARK):].decode("utf-8", "replace")
    # legacy C1 rows stored raw utf-8
    try:
        return b.decode("utf-8")
    except Exception:
        return ""
