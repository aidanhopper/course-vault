from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Server
    VAULT_HOST: str = "0.0.0.0"
    VAULT_PORT: int = 8000
    VAULT_DATA_DIR: Path = Path("./data")
    MEDIA_DIR: Path = Path("./media")

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/vault.db"

    # Ingestion Worker
    MAX_CONCURRENT_DOWNLOADS: int = 1

    # AI Metadata Cleaner (Optional)
    AI_API_KEY: str | None = None
    AI_BASE_URL: str = "https://api.openai.com/v1"
    AI_MODEL: str = "gpt-4o-mini"

    # Jellyfin Integration (Optional)
    JELLYFIN_URL: str | None = None
    JELLYFIN_API_KEY: str | None = None

    @property
    def videos_dir(self) -> Path:
        return self.MEDIA_DIR / "videos"

    @property
    def lectures_dir(self) -> Path:
        return self.videos_dir

    @property
    def documents_dir(self) -> Path:
        return self.MEDIA_DIR / "documents"

    @property
    def slides_dir(self) -> Path:
        return self.MEDIA_DIR / "slides"

    @property
    def audio_dir(self) -> Path:
        return self.MEDIA_DIR / "audio"


settings = Settings()
