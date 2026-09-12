from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    app_env: Literal['development', 'test', 'production'] = 'development'
    database_url: str = 'postgresql+psycopg://wellbeing:wellbeing@127.0.0.1:55432/wellbeing'
    frontend_origin: str = 'http://localhost:5173'
    backend_host: str = ''
    session_secret: str = 'development-only-change-before-hosting-32-characters'
    oidc_client_id: str = ''
    oidc_client_secret: str = ''
    enable_dev_auth: bool = False
    session_ttl_hours: int = 168
    cookie_secure: bool = False
    llm_enabled: bool = False
    openai_api_key: SecretStr = SecretStr('')
    llm_model: str = Field('', max_length=100, pattern=r'^[a-zA-Z0-9._:-]*$')
    llm_timeout_seconds: float = Field(8, ge=1, le=15)
    llm_hourly_limit: int = Field(5, ge=1, le=20)
    llm_daily_limit: int = Field(20, ge=1, le=100)
    frontend_dist: str = ''

    @field_validator('database_url')
    @classmethod
    def postgres_only(cls, value: str) -> str:
        if value.startswith('postgres://'):
            value = value.replace('postgres://', 'postgresql+psycopg://', 1)
        elif value.startswith('postgresql://'):
            value = value.replace('postgresql://', 'postgresql+psycopg://', 1)
        if not value.startswith('postgresql+psycopg://'):
            raise ValueError('DATABASE_URL must use PostgreSQL with psycopg')
        return value

    @field_validator('frontend_origin')
    @classmethod
    def exact_origin(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.path or parts.query or parts.fragment or parts.username:
            raise ValueError('FRONTEND_ORIGIN must be an exact HTTP(S) origin without a trailing slash')
        return value

    @field_validator('session_ttl_hours')
    @classmethod
    def bounded_session(cls, value: int) -> int:
        if not 1 <= value <= 168:
            raise ValueError('Session duration must be 1 to 168 hours')
        return value

    @field_validator('backend_host')
    @classmethod
    def exact_backend_host(cls, value: str) -> str:
        # Optional second ingress host for a reverse-proxied backend, never a wildcard.
        import re
        if value and (len(value) > 253 or not re.fullmatch(r'[a-zA-Z0-9]+(?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?', value)
                      or any(not label or len(label) > 63 or label.startswith('-') or label.endswith('-') for label in value.split('.'))):
            raise ValueError('BACKEND_HOST must be an exact hostname without scheme, port, path or wildcard')
        return value.lower()

    @model_validator(mode='after')
    def production_safety(self):
        if self.llm_enabled and (not self.openai_api_key.get_secret_value() or not self.llm_model):
            raise ValueError('Enabling LLM requires OPENAI_API_KEY and an explicit LLM_MODEL')
        if bool(self.oidc_client_id) != bool(self.oidc_client_secret):
            raise ValueError('Configure both OIDC client credentials or neither')
        if self.app_env == 'production':
            if self.enable_dev_auth or not self.cookie_secure:
                raise ValueError('Production requires secure cookies and disabled development authentication')
            if not self.frontend_origin.startswith('https://'):
                raise ValueError('Production requires an HTTPS frontend origin')
            if len(self.session_secret) < 32 or self.session_secret.startswith('development-only'):
                raise ValueError('Production requires an independent session secret of at least 32 characters')
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
