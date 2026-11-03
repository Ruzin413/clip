import sqlite3
from contextlib import closing
from pathlib import Path

from models import AIProvider, ProviderSettings


class ProviderSettingsStore:
    """Per-provider API key, model and base URL that the user chose to remember, kept in SQLite."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS provider_settings ("
                "provider TEXT PRIMARY KEY, api_key TEXT, model TEXT, base_url TEXT)"
            )
        # Keys are stored in plain text, so only the owner may read the file.
        path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def all(self) -> dict[AIProvider, ProviderSettings]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT provider, api_key, model, base_url FROM provider_settings").fetchall()
        return {
            AIProvider(provider): ProviderSettings(api_key=api_key, model=model, base_url=base_url)
            for provider, api_key, model, base_url in rows
        }

    def save(self, provider: AIProvider, settings: ProviderSettings) -> None:
        """Set the given fields and keep the ones left as None."""
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO provider_settings (provider, api_key, model, base_url) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(provider) DO UPDATE SET "
                "api_key = COALESCE(excluded.api_key, api_key), "
                "model = COALESCE(excluded.model, model), "
                "base_url = COALESCE(excluded.base_url, base_url)",
                (provider.value, settings.api_key, settings.model, settings.base_url),
            )

    def forget(self, provider: AIProvider) -> None:
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM provider_settings WHERE provider = ?", (provider.value,))
