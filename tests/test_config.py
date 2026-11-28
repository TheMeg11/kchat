"""Railway startup-origin regressions; no database or network access."""
import pytest

from app.config import Settings


@pytest.fixture
def production(monkeypatch):
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "test-project")
    monkeypatch.delenv("RAILWAY_PUBLIC_DOMAIN", raising=False)
    monkeypatch.delenv("RAILWAY_PRIVATE_DOMAIN", raising=False)
    monkeypatch.delenv("PUBLIC_URL", raising=False)

    def make(public_url=""):
        return Settings(
            _env_file=None,
            public_url=public_url,
            encryption_key="test-encryption-key-1234567890",
            backup_password="test-backup-password-1234567890",
            webhook_secret="test-webhook-secret-1234567890",
        )
    return make


def test_railway_public_domain_supplies_https_origin(production, monkeypatch):
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "hamchat-production.up.railway.app")
    config = production()
    config.validate_production()
    assert config.public_url == "https://hamchat-production.up.railway.app"
    config.validate_production()  # Idempotent, also used by webhook/security code.
    assert config.public_url == "https://hamchat-production.up.railway.app"


def test_explicit_https_url_wins_over_railway_domain(production, monkeypatch):
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "unused.up.railway.app")
    config = production("  https://chat.example.com/  ")
    config.validate_production()
    assert config.public_url == "https://chat.example.com"


@pytest.mark.parametrize("url", [
    "http://chat.example.com", "chat.example.com", "https://", "https:///missing-host",
    "https://user:password@chat.example.com", "https://chat.example.com?token=secret",
    "https://chat.example.com#fragment", "https://chat.example.com:invalid",
    "https://chat.example.com:99999", "https://chat.\nexample.com",
])
def test_invalid_explicit_url_is_not_masked_by_fallback(production, monkeypatch, url):
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "valid.up.railway.app")
    with pytest.raises(RuntimeError, match="PUBLIC_URL must be a valid HTTPS URL"):
        production(url).validate_production()


@pytest.mark.parametrize("domain", [
    "https://chat.up.railway.app", "chat.up.railway.app/path", "chat.up.railway.app?x=1",
    "user@chat.up.railway.app", "localhost", "chat..railway.app", "-chat.railway.app",
])
def test_railway_fallback_requires_hostname(production, monkeypatch, domain):
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", domain)
    with pytest.raises(RuntimeError, match="RAILWAY_PUBLIC_DOMAIN must be a public hostname"):
        production().validate_production()


def test_missing_public_origin_has_actionable_error_and_ignores_private_domain(production, monkeypatch):
    monkeypatch.setenv("RAILWAY_PRIVATE_DOMAIN", "hamchat.railway.internal")
    with pytest.raises(RuntimeError, match="enable a Railway public domain"):
        production().validate_production()


def test_local_polling_needs_no_public_url(monkeypatch):
    monkeypatch.delenv("RAILWAY_PROJECT_ID", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    config = Settings(_env_file=None, public_url="")
    config.validate_production()
    assert config.public_url == ""


def test_domain_fallback_does_not_weaken_secret_requirements(production, monkeypatch):
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "chat.up.railway.app")
    config = production()
    config.backup_password = "short"
    with pytest.raises(RuntimeError, match="BACKUP_PASSWORD must be configured"):
        config.validate_production()
