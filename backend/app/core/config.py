"""
Configurazione applicazione — carica .env e valida le variabili.

Uso:
    from app.core.config import settings
    print(settings.database_url)

Se manca una variabile required, l'import fallisce subito con un messaggio chiaro.
Meglio crash all'avvio che KeyError a meta' del job notturno.
"""
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# Cartella backend/, dove deve stare il .env
BACKEND_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BACKEND_DIR / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Supabase ---
    supabase_url: str = Field(..., description="URL progetto Supabase")
    supabase_anon_key: str = Field(..., description="Anon key (public)")
    supabase_service_key: Optional[str] = Field(
        None, description="Service role key — admin, mai esporre client-side"
    )
    database_url: str = Field(..., description="Postgres connection string (pooler 6543)")

    # --- Fonte dati primaria (Fase 1+) ---
    eodhd_api_key: Optional[str] = None
    fmp_api_key: Optional[str] = None

    # --- Fonti dati secondarie (Fase 1+) ---
    fred_api_key: Optional[str] = None

    # --- AI (Fase 4) ---
    anthropic_api_key: Optional[str] = None

    # --- App ---
    app_env: str = Field("dev", description="dev | prod")
    log_level: str = Field("INFO", description="DEBUG | INFO | WARNING | ERROR")


settings = Settings()  # carica e valida al primo import


if __name__ == "__main__":
    # Smoke test: python -m app.core.config
    print(f"Backend dir: {BACKEND_DIR}")
    print(f"Env file:    {ENV_FILE} (exists={ENV_FILE.exists()})")
    print(f"App env:     {settings.app_env}")
    print(f"Supabase URL: {settings.supabase_url[:40]}...")
    print(f"DB URL set:   {bool(settings.database_url)}")
