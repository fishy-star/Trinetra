"""Settings from environment variables. Secrets are read here and nowhere else."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ENGINE_VERSION = "0.2.0"
ROOT = Path(__file__).resolve().parent.parent
REGISTRY_DIR = ROOT / "data" / "registry"


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str | None
    telegram_webhook_secret: str | None
    whatsapp_token: str | None
    whatsapp_phone_id: str | None
    whatsapp_verify_token: str | None
    aws_region: str
    s3_bucket: str | None
    bedrock_model_id: str | None
    llm_mode: str
    stt_mode: str
    audio_model: str
    reviewers: tuple[str, ...]
    reviewer_password: str | None
    data_dir: Path
    media_ttl_hours: int
    user_id_salt: str | None
    public_base_url: str
    demo_summons_path: Path | None
    demo_altered_path: Path | None

    def __repr__(self) -> str:
        # Never print secrets, even by accident.
        return "Settings(...)"

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ.get
        return cls(
            telegram_bot_token=env("TELEGRAM_BOT_TOKEN"),
            telegram_webhook_secret=env("TELEGRAM_WEBHOOK_SECRET"),
            whatsapp_token=env("WHATSAPP_TOKEN"),
            whatsapp_phone_id=env("WHATSAPP_PHONE_ID"),
            whatsapp_verify_token=env("WHATSAPP_VERIFY_TOKEN"),
            aws_region=env("AWS_REGION", "ap-south-1"),
            s3_bucket=env("S3_BUCKET"),
            bedrock_model_id=env("BEDROCK_MODEL_ID"),
            llm_mode=env("LLM_MODE", "off"),
            stt_mode=env("STT_MODE", "off"),
            audio_model=env("AUDIO_MODEL", "off"),
            reviewers=tuple(r.strip() for r in env("REVIEWERS", "").split(",") if r.strip()),
            reviewer_password=env("REVIEWER_PASSWORD"),
            data_dir=ROOT / env("DATA_DIR", "data/runtime"),  # a relative path is taken from the repo root
            media_ttl_hours=int(env("MEDIA_TTL_HOURS", "24")),
            user_id_salt=env("USER_ID_SALT"),
            public_base_url=env("PUBLIC_BASE_URL", "http://localhost:8000"),
            # Sample files for judge mode; a relative path is taken from the repo root.
            demo_summons_path=ROOT / env("DEMO_SUMMONS_PATH") if env("DEMO_SUMMONS_PATH") else None,
            demo_altered_path=ROOT / env("DEMO_ALTERED_PATH") if env("DEMO_ALTERED_PATH") else None,
        )


settings = Settings.from_env()
