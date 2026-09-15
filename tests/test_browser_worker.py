"""Unit tests for browser snapshot sanitization and navigation behavior."""

from unittest.mock import MagicMock, call


from tournament_scheduler.pipeline.browser_worker import (
    GOTO_RETRY_TIMEOUT_MS,
    GOTO_TIMEOUT_MS,
    BrowserWorker,
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
