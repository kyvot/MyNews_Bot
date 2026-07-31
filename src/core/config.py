from logging import Logger
import logging
from pathlib import Path
import sys
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from utils.language import (
    resolve_deepl_target_language,
    resolve_language,
)


PROJECT_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Config(BaseSettings):
    RUN_MODE: str = "long"
    PROXY_URL: str = "socks5://127.0.0.1:2080"

    XTGTOK: str
    BOT_TOKEN: str
    DEFAULT_ADMIN_ID: int
    GROUP_ID: int
    CHANNEL_ID: int

    BASE_URL: str
    WEBHOOK_PATH: str = "/webhook"
    WEBHOST: str = "0.0.0.0"
    WEBPORT: int = 8000

    DB_USER: str = "postgresql"
    DB_PASSWORD: str = "db_password"
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "news_db"

    LIMIT_RSS_NEWS: int = 10
    LIMIT_REQ: int = 3
    SLEEP_REQ: float = 0.5
    ATTEMPTS_FOR_FETCH: int = 3
    TIMEOUT_FETCH: float = 10.0
    PARSER_AUTOSTART: bool = False
    PARSER_INTERVAL_SECONDS: int = 900
    PARSER_MAX_POSTS_PER_CYCLE: int = 20
    PARSER_PUBLISH_DELAY_SECONDS: float = 3.0
    PARSER_TELEGRAM_MAX_RETRIES: int = 3
    PARSER_TARGET_VALIDATION_TIMEOUT: int = 5
    PARSER_VOTING_HOURS: int = 72
    PARSER_MAX_IMAGES: int = 4
    NEWS_HIT_SCORE: int = 3
    NEWS_PUBLICATION_LEASE_SECONDS: int = 900
    PARSER_TRANSLATION_ENABLED: bool = True
    PARSER_TRANSLATION_REQUIRED: bool = True
    PARSER_TRANSLATION_MAX_CHARS: int = 3_500
    DEEPL_API_KEY: SecretStr | None = None
    # Leave unset to select Free for ``:fx`` keys and Pro otherwise.
    DEEPL_TRANSLATE_URL: str | None = None
    DEEPL_PROXY_URL: str | None = None
    DEEPL_REQUEST_TIMEOUT: float = 20.0
    DEEPL_MAX_RETRIES: int = 2
    DEEPL_MAX_CONCURRENCY: int = 4

    ARTICLE_FETCH_TIMEOUT: float = 15.0
    ARTICLE_MAX_BYTES: int = 5_000_000
    ARTICLE_MAX_CHARS: int = 60_000
    ARTICLE_EXTRACT_WORKERS: int = 1
    ARTICLE_EXTRACT_TIMEOUT: float = 30.0
    ARTICLE_MAX_CONCURRENCY: int = 2
    AI_PROVIDER: str = "openrouter"
    OPENROUTER_CHAT_COMPLETIONS_URL: str = (
        "https://openrouter.ai/api/v1/chat/completions"
    )
    OPENROUTER_API_KEY: SecretStr | None = None
    OPENROUTER_MODEL: str = "openrouter/free"
    OPENROUTER_HTTP_REFERER: str | None = None
    OPENROUTER_APP_TITLE: str | None = "MyNews"
    OPENROUTER_PROXY_URL: str | None = None
    AI_LANGUAGE: str = "Russian"
    AI_EXTRACTIVE_FALLBACK_ENABLED: bool = False
    AI_TEMPERATURE: float = 0.2
    AI_MAX_OUTPUT_TOKENS: int = 350
    AI_REQUEST_TIMEOUT: float = 45.0
    AI_MAX_RETRIES: int = 3
    AI_MAX_CONCURRENCY: int = 2
    AI_CHUNK_CHARS: int = 16_000
    AI_SUMMARY_MAX_CHARS: int = 2_500

    SCHEDULER_TIMEZONE: str = "Europe/Moscow"
    SCHEDULER_MISFIRE_GRACE_TIME: int = 60
    SCHEDULED_POST_MAX_ATTEMPTS: int = 3
    SCHEDULED_POST_RETRY_DELAY: int = 60
    SCHEDULED_POST_RECONCILE_INTERVAL: int = 30
    SCHEDULED_POST_LEASE_TIMEOUT: int = 300
    SCHEDULER_SHUTDOWN_GRACE_SECONDS: float = 30.0
    SCHEDULER_SHUTDOWN_CANCEL_SECONDS: float = 10.0

    @field_validator("PROXY_URL")
    @classmethod
    def validate_telegram_proxy(cls, value: str) -> str:
        return _validate_proxy_url(
            value,
            allowed_schemes={"socks5"},
            setting_name="PROXY_URL",
        )

    @field_validator("GROUP_ID", "CHANNEL_ID", mode="before")
    @classmethod
    def normalize_telegram_destination_id(cls, value: int | str) -> int:
        return _normalize_telegram_destination_id(value)

    @field_validator("AI_LANGUAGE")
    @classmethod
    def validate_ai_language(cls, value: str) -> str:
        resolve_language(value)
        resolve_deepl_target_language(value)
        return value.strip()

    @field_validator("OPENROUTER_PROXY_URL", "DEEPL_PROXY_URL")
    @classmethod
    def validate_external_proxy(
        cls,
        value: str | None,
        info,
    ) -> str | None:
        if value is None:
            return None
        return _validate_proxy_url(
            value,
            allowed_schemes={"http", "https", "socks5"},
            setting_name=info.field_name,
        )

    @field_validator("DEEPL_TRANSLATE_URL")
    @classmethod
    def validate_deepl_translate_url(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        value = value.strip()
        parsed = urlsplit(value)
        if (
            parsed.scheme.casefold() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "DEEPL_TRANSLATE_URL must be an HTTPS endpoint URL"
            )
        return value

    @model_validator(mode="after")
    def validate_required_translation_mode(self) -> "Config":
        if (
            self.PARSER_TRANSLATION_REQUIRED
            and not self.PARSER_TRANSLATION_ENABLED
        ):
            raise ValueError(
                "PARSER_TRANSLATION_ENABLED must be true when "
                "PARSER_TRANSLATION_REQUIRED is true"
            )
        if self.DEEPL_TRANSLATE_URL is None:
            api_key = (
                self.DEEPL_API_KEY.get_secret_value()
                if self.DEEPL_API_KEY is not None
                else ""
            )
            host = (
                "api-free.deepl.com"
                if not api_key or api_key.endswith(":fx")
                else "api.deepl.com"
            )
            self.DEEPL_TRANSLATE_URL = (
                f"https://{host}/v2/translate"
            )
        return self

    @property
    def DB_URL(self):
        return (
            f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    model_config = SettingsConfigDict(
        env_file=PROJECT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


def _validate_proxy_url(
    value: str,
    *,
    allowed_schemes: set[str],
    setting_name: str,
) -> str:
    value = value.strip()
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"{setting_name} contains an invalid port") from error

    if (
        parsed.scheme.casefold() not in allowed_schemes
        or not parsed.hostname
        or port is None
        or port < 1
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        schemes = ", ".join(sorted(allowed_schemes))
        raise ValueError(
            f"{setting_name} must be a proxy URL with a host and port "
            f"(allowed schemes: {schemes})"
        )
    return value


def _normalize_telegram_destination_id(value: int | str) -> int:
    """Return a Bot API chat ID for a supergroup/channel destination.

    Telegram links expose the compact positive peer ID in ``t.me/c/...``,
    while the Bot API expects the corresponding negative ``-100...`` ID.
    Accepting both forms keeps every Telegram call on one canonical value.
    """
    try:
        chat_id = int(str(value).strip())
    except (TypeError, ValueError) as error:
        raise ValueError(
            "Telegram destination ID must be an integer"
        ) from error

    if chat_id == 0:
        raise ValueError("Telegram destination ID cannot be zero")
    if chat_id < 0:
        return chat_id

    # A positive absolute value such as ``1001234567890`` is just missing
    # its sign. Smaller positive values are compact channel peer IDs.
    if chat_id >= 1_000_000_000_000:
        return -chat_id
    return -(1_000_000_000_000 + chat_id)


stg = Config()
logger = None
if stg.RUN_MODE == "web":
    logger: Logger = logging.getLogger("uvicorn.error")
else:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    logger: Logger = logging.getLogger(__name__)
