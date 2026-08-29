from __future__ import annotations

"""Guard the root Compose file consumed by the real Portainer stack."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
production = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
development = (ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8")

required = (
    "context: .",
    "/data/compose/22/data:/app/data",
    "/data/compose/22/backups:/app/backups",
    'expose:\n      - "8000"',
    "name: deputy-roster-multi_default",
    "- deputy-roster-view",
    "APP_SECRET_KEY: ${APP_SECRET_KEY:-}",
    "SIGNUP_ENABLED: ${SIGNUP_ENABLED:-true}",
    "COOKIE_SECURE: ${COOKIE_SECURE:-true}",
    "TRUSTED_DEVICE_LIMIT: ${TRUSTED_DEVICE_LIMIT:-10}",
    "BACKUP_ENABLED: ${BACKUP_ENABLED:-true}",
    "BACKUP_DIR: ${BACKUP_DIR:-/app/backups}",
    "BACKUP_RETENTION_DAYS: ${BACKUP_RETENTION_DAYS:-30}",
    "BACKUP_HOUR: ${BACKUP_HOUR:-3}",
    "BACKUP_MINUTE: ${BACKUP_MINUTE:-30}",
)
for item in required:
    assert item in production, item
assert "ports:" not in production
assert "APP_SECRET_KEY: ${APP_SECRET_KEY:?" not in production
assert "${APP_PORT:-8096}:8000" in development
assert "./data:/app/data" in development
assert "COOKIE_SECURE: ${COOKIE_SECURE:-false}" in development
assert not (ROOT / "production" / "docker-compose.portainer.yml").exists()

print("root production Compose smoke ok")
