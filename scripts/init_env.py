"""Create local credentials without printing or overwriting secrets."""
import secrets
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
destination = root / ".env"
if destination.exists():
    if "--complete-missing" not in sys.argv:
        raise SystemExit(".env already exists; no credentials changed")
    existing = destination.read_text(encoding="utf-8")
    declared = {line.split("=", 1)[0].strip(): line.split("=", 1)[1].strip().strip('\"').strip("'") for line in existing.splitlines() if "=" in line and not line.lstrip().startswith("#")}
    template = (root / ".env.example").read_text(encoding="utf-8")
    extra = []
    for line in template.splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key = line.split("=", 1)[0]
        if key not in declared or (key in ("POSTGRES_PASSWORD", "REDIS_PASSWORD", "SESSION_SECRET", "EMBEDDING_API_KEY") and not declared[key]):
            if key in ("POSTGRES_PASSWORD", "REDIS_PASSWORD", "SESSION_SECRET", "EMBEDDING_API_KEY"):
                line = key + "=" + secrets.token_hex(32)
            extra.append(line)
    with destination.open("a", encoding="utf-8") as stream:
        stream.write("\n# Missing v1 settings added without changing existing values\n" + "\n".join(extra) + "\n")
    raise SystemExit("Added missing v1 settings; existing values were preserved")
values = {key: secrets.token_hex(32) for key in ("POSTGRES_PASSWORD", "REDIS_PASSWORD", "SESSION_SECRET", "EMBEDDING_API_KEY")}
values["DATABASE_URL"] = "postgresql://legal_rag:" + values["POSTGRES_PASSWORD"] + "@db:5432/legal_rag"
values["REDIS_URL"] = "redis://:" + values["REDIS_PASSWORD"] + "@redis:6379/0"
text = (root / ".env.example").read_text(encoding="utf-8")
for key, value in values.items():
    text = text.replace(key + "=\n", key + "=" + value + "\n")
destination.write_text(text, encoding="utf-8")
print("Created .env with generated local credentials. Add Google, Gemini and Jev AI keys there.")

