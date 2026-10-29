"""Application settings (12-factor style, overridable through ``backend/.env``)."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    # 默认值只是「没有 .env 时也能起来」的兜底，实际部署一律以 backend/.env 为准。
    # 本地默认走 Postgres.app 的 5432（`zanzhu.sh` 起的那套独立集群在 5433，
    # 两者不通用；换库只需改 .env 一行，不要动这里）。
    database_url: str = "postgresql+psycopg://huangyong@127.0.0.1:5432/zanzhu"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    site_name: str = "产业带企业库"
    # 管理接口令牌。声明成字段是为了让 backend/.env 里那一行生效 ——
    # 不声明的话 pydantic-settings 会忽略它，也不写回 os.environ，
    # 管理接口就永远停留在「未启用」状态（实战踩过）。
    admin_token: str = ""
    data_dir: Path = REPO_ROOT / "data"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def source_dir(self) -> Path:
        return self.data_dir / "source"

    @property
    def curated_dir(self) -> Path:
        return self.data_dir / "curated"


settings = Settings()