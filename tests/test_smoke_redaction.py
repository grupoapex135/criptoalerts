"""The setup check must never print a secret (requests quotes the URL, and the Telegram URL has the token)."""
import smoke_test


def test_secrets_are_redacted():
    assert "test-key" in smoke_test.SECRETS  # OPENAI_API_KEY from conftest
    msg = "Max retries exceeded with url: /v1/x?key=test-key (Caused by ...)"
    assert smoke_test.redact(msg) == "Max retries exceeded with url: /v1/x?key=*** (Caused by ...)"
