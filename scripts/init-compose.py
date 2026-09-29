"""Generate local-only credentials without printing secrets or replacing an existing file."""

import json
import secrets
from pathlib import Path

target = Path(".env.compose")
token = secrets.token_hex(32)
tokens = json.dumps({token: {"tenant": "demo", "role": "operator"}}, separators=(",", ":"))
with target.open("x", encoding="utf-8") as file:
    file.write(f"MSSQL_SA_PASSWORD=Demo1_{secrets.token_hex(20)}\n")
    file.write(f"FIATIUM_APP_PASSWORD=Demo2_{secrets.token_hex(20)}\n")
    file.write(f"FIATIUM_TOKENS='{tokens}'\n")
print("Created .env.compose. Use the token in FIATIUM_TOKENS to sign in locally.")
