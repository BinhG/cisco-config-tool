from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SecretBox:
    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    @classmethod
    def from_file(cls, path: Path) -> "SecretBox":
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            key = path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            path.write_bytes(key)
        return cls(key)

    def encrypt(self, value: str | None) -> str:
        if not value:
            return ""
        return self._fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt(self, token: str | None) -> str:
        if not token:
            return ""
        try:
            return self._fernet.decrypt(token.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Stored secret cannot be decrypted with the current key.") from exc
