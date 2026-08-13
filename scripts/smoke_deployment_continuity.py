from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="redeputy-continuity-"))
os.environ.update({"DB_PATH": str(tmp / "continuity.sqlite3"), "DATA_DIR": str(tmp), "APP_SECRET_KEY": "obvious-test-key-a"})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.security import decrypt_text, encrypt_text

settings_a = replace(get_settings(), app_secret_key="obvious-test-key-a", data_dir=str(tmp))
settings_b = replace(settings_a, app_secret_key="obvious-test-key-b")
ciphertext_path = tmp / "persisted-encrypted-value.txt"
ciphertext_path.write_text(encrypt_text("fixture persisted credential", settings_a), encoding="utf-8")

# A restart/reinitialization with the same key can read existing encrypted state.
persisted = ciphertext_path.read_text(encoding="utf-8")
assert decrypt_text(persisted, replace(settings_a)) == "fixture persisted credential"

# A different key fails closed instead of producing plausible plaintext.
try:
    decrypt_text(persisted, settings_b)
except Exception:
    pass
else:
    raise AssertionError("encrypted data unexpectedly decrypted with a replacement APP_SECRET_KEY")

print("APP_SECRET_KEY continuity smoke ok")
