"""Create dedicated local input/config folders; never overwrite or print secrets."""

import os
from pathlib import Path

os.umask(0o077)
config_dir = Path.home() / ".config/study-errorbook"
inputs = Path.home() / "Documents/StudyErrorbook/inbox"
config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
inputs.mkdir(parents=True, exist_ok=True, mode=0o700)
config = config_dir / "config.env"
if not config.exists():
    with config.open("x", encoding="utf-8") as stream:
        stream.write(f"TEXTIN_APP_ID=\nTEXTIN_SECRET_CODE=\nSTUDY_INPUT_ROOT={inputs}\n")
    print("Created a private configuration template; enter TextIn credentials locally.")
else:
    print("Existing private configuration kept unchanged.")
print(f"Configuration: {config}")
print(f"Input folder: {inputs}")
