"""Unit tests for credential-leak sanitization in browser_worker.py."""

from unittest.mock import MagicMock, call, patch

import pytest

from tournament_scheduler.pipeline.browser_worker import (
    GOTO_RETRY_TIMEOUT_MS,
    GOTO_TIMEOUT_MS,
    BrowserWorker,
    _redact_credentials,
    _sanitize_html,
)


class TestSanitizeHtml:
    def test_strips_password_input_value(self):
        html = '<input type="password" id="pass" name="pass" value="secret123">'
        result = _sanitize_html(html)
        assert "secret123" not in result
        assert 'value=""' in result

    def test_strips_email_input_value(self):
        html = '<input id="email" type="text" value="user@example.com">'
        result = _sanitize_html(html)
        assert "user@example.com" not in result
        assert 'value=""' in result

    def test_leaves_unrelated_input_values_untouched(self):
        html = '<input type="text" id="other" value="hello">'
        assert _sanitize_html(html) == html

    def test_leaves_checkbox_value_untouched(self):
        html = '<input type="checkbox" name="remember" value="1" checked>'
        assert _sanitize_html(html) == html

    def test_empty_html_returns_empty(self):
        assert _sanitize_html("") == ""

    def test_strips_username_input_by_name(self):
        html = '<input name="username" value="myuser">'
        result = _sanitize_html(html)
        assert "myuser" not in result
        assert 'value=""' in result


class TestRedactCredentials:
    def test_redacts_email_when_env_var_set(self, monkeypatch):
        monkeypatch.setenv("BOOKUP_EMAIL", "user@example.com")
        monkeypatch.delenv("BOOKUP_PASSWORD", raising=False)
        text = 'text input (placeholder=user@example.com)'
        result = _redact_credentials(text)
        assert "user@example.com" not in result
        assert "[REDACTED]" in result

    def test_redacts_password_when_env_var_set(self, monkeypatch):
        monkeypatch.setenv("BOOKUP_PASSWORD", "SuperSecret123")
        monkeypatch.delenv("BOOKUP_EMAIL", raising=False)
        text = 'text input (placeholder=SuperSecret123)'
        result = _redact_credentials(text)
        assert "SuperSecret123" not in result
        assert "[REDACTED]" in result

    def test_no_op_when_env_vars_unset(self, monkeypatch):
        monkeypatch.delenv("BOOKUP_EMAIL", raising=False)
        monkeypatch.delenv("BOOKUP_PASSWORD", raising=False)
        text = "some label text with no credentials"
        assert _redact_credentials(text) == text

    def test_empty_text_returns_empty(self, monkeypatch):
        monkeypatch.setenv("BOOKUP_EMAIL", "user@example.com")
        assert _redact_credentials("") == ""


class TestDefenseInDepth:
    """Verify Layers 2-3 substring redaction catch what Layer 1 regex misses."""

    def test_regex_misses_nonstandard_name_pwd(self):
        """Layer 1 (_sanitize_html) does NOT strip value from name='pwd'."""
        html = '<input name="pwd" value="SuperSecret123">'
        result = _sanitize_html(html)
        # Layer 1 gap: non-standard input name not in regex
        assert "SuperSecret123" in result

    def test_regex_misses_nonstandard_name_credential(self):
        """Layer 1 does NOT strip value from name='credential'."""
        html = '<input name="credential" value="user@example.com">'
        result = _sanitize_html(html)
        assert "user@example.com" in result

    def test_layer2_catches_what_layer1_misses(self, monkeypatch):
        """Layer 2 (_redact_credentials) scrubs credential even if Layer 1 missed it."""
        monkeypatch.setenv("BOOKUP_PASSWORD", "SuperSecret123")
        monkeypatch.delenv("BOOKUP_EMAIL", raising=False)
        # Simulate HTML that passed through Layer 1 unscathed (e.g. name='pwd')
        text = '<input name="pwd" value="SuperSecret123">'
        result = _redact_credentials(text)
        assert "SuperSecret123" not in result
        assert "[REDACTED]" in result

    def test_both_layers_combined(self, monkeypatch):
        """Layer 1 + Layer 2 together cover standard AND non-standard input names."""
        monkeypatch.setenv("BOOKUP_EMAIL", "user@example.com")
        monkeypatch.setenv("BOOKUP_PASSWORD", "SuperSecret123")
        # Standard name (caught by Layer 1)
        html_std = '<input type="password" value="SuperSecret123">'
        after_l1 = _sanitize_html(html_std)
        assert "SuperSecret123" not in after_l1  # Layer 1 caught it
        # Non-standard name (missed by Layer 1, caught by Layer 2)
        html_nonstd = '<input name="pwd" value="SuperSecret123">'
        after_l1 = _sanitize_html(html_nonstd)
        assert "SuperSecret123" in after_l1  # Layer 1 missed it
        after_l2 = _redact_credentials(after_l1)
        assert "SuperSecret123" not in after_l2  # Layer 2 caught it
        assert "[REDACTED]" in after_l2

    def test_layer2_redacts_in_iframe_snapshot_context(self, monkeypatch):
        """Layer 2 catches credential in realistic iframe HTML snapshot."""
        monkeypatch.setenv("BOOKUP_EMAIL", "user@example.com")
        monkeypatch.setenv("BOOKUP_PASSWORD", "SuperSecret123")
        # Simulate iframe HTML with user's email echoed in a label/placeholder
        text = '<div class="user-info">Logget inn som user@example.com</div>'
        result = _redact_credentials(text)
        assert "user@example.com" not in result
        assert "[REDACTED]" in result

    def test_layer2_redacts_credential_in_error_text(self, monkeypatch):
        """Layer 2 scrubs credential even in unstructured error-like text."""
        monkeypatch.setenv("BOOKUP_PASSWORD", "s3cr3t!pass")
        monkeypatch.delenv("BOOKUP_EMAIL", raising=False)
        text = 'Error: could not fill field with value s3cr3t!pass (timeout)'
        result = _redact_credentials(text)
        assert "s3cr3t!pass" not in result
        assert "[REDACTED]" in result


class TestBrowserLaunchMode:
    def test_manual_bookup_login_launches_headed_browser(self, monkeypatch):
        monkeypatch.setenv("RVV_BOOKUP_MANUAL_LOGIN", "1")
        page = MagicMock()
        browser = MagicMock()
        browser.new_page.return_value = page
        chromium = MagicMock()
        chromium.launch.return_value = browser
        playwright = MagicMock()
        playwright.chromium = chromium
        manager = MagicMock()
        manager.__enter__.return_value = playwright

        with patch("playwright.sync_api.sync_playwright", return_value=manager):
            worker = BrowserWorker()
            worker.start()

        chromium.launch.assert_called_once_with(headless=False)
        page.set_default_timeout.assert_called_once_with(15_000)


class TestCmdGotoRetry:
    """Tests for cmd_goto retry timeout behavior."""

    def _make_worker_with_mock_page(self):
        """Return a BrowserWorker whose _page is a MagicMock and start() is a no-op."""
        worker = BrowserWorker.__new__(BrowserWorker)
        worker._page = MagicMock()
        worker._browser = MagicMock()
        worker._playwright = MagicMock()
        # start() must be a no-op so it doesn't try to launch a real browser
        worker.start = lambda: None
        return worker

    def test_initial_goto_uses_goto_timeout_ms(self):
        """First goto call uses GOTO_TIMEOUT_MS with networkidle wait."""
        worker = self._make_worker_with_mock_page()
        # Simulate snapshot returning minimal data
        worker._snapshot = MagicMock(return_value={"html": "", "url": "", "title": ""})
        worker._page.goto.return_value = None

        result = worker.cmd_goto({"url": "https://example.com", "wait_ms": 0})

        assert result["ok"] is True
        first_call = worker._page.goto.call_args_list[0]
        assert first_call == call(
            "https://example.com",
            timeout=GOTO_TIMEOUT_MS,
            wait_until="networkidle",
        )

    def test_retry_goto_uses_longer_timeout_and_domcontentloaded(self):
        """When first goto raises, retry uses GOTO_RETRY_TIMEOUT_MS and domcontentloaded."""
        worker = self._make_worker_with_mock_page()
        worker._snapshot = MagicMock(return_value={"html": "", "url": "", "title": ""})
        # First call raises, second succeeds
        worker._page.goto.side_effect = [Exception("timeout"), None]

        result = worker.cmd_goto({"url": "https://example.com", "wait_ms": 0})

        assert result["ok"] is True
        assert worker._page.goto.call_count == 2
        retry_call = worker._page.goto.call_args_list[1]
        assert retry_call == call(
            "https://example.com",
            timeout=GOTO_RETRY_TIMEOUT_MS,
            wait_until="domcontentloaded",
        )

    def test_retry_timeout_is_longer_than_initial_timeout(self):
        """GOTO_RETRY_TIMEOUT_MS must be strictly larger than GOTO_TIMEOUT_MS."""
        assert GOTO_RETRY_TIMEOUT_MS > GOTO_TIMEOUT_MS

    def test_returns_error_when_both_goto_attempts_fail(self):
        """Returns ok=False if both initial and retry goto raise."""
        worker = self._make_worker_with_mock_page()
        worker._page.goto.side_effect = Exception("network error")

        result = worker.cmd_goto({"url": "https://example.com", "wait_ms": 0})

        assert result["ok"] is False
        assert "goto feilet" in result["error"]
