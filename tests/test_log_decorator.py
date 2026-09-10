"""Tests for the @log_call decorator."""

from __future__ import annotations

import logging
from io import StringIO

import pytest

from tournament_scheduler.utils.log_decorator import log_call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _capture_log(name: str, level: int = logging.INFO) -> tuple[StringIO, logging.Logger]:
    """Attach a StringIO handler to logger *name* and return (stream, logger)."""
    stream = StringIO()
    logger = logging.getLogger(name)
    logger.setLevel(level)
    handler = logging.StreamHandler(stream)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.handlers.clear()
    logger.addHandler(handler)
    return stream, logger


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLogCall:
    """Basic call-logging behaviour."""

    def test_logs_function_call_and_return(self) -> None:
        stream, _ = _capture_log("tests.test_log_decorator")

        @log_call
        def add(a: int, b: int) -> int:
            return a + b

        result = add(3, 5)
        assert result == 8

        log = stream.getvalue()
        assert "CALL" in log
        assert "add(" in log
        assert "3" in log
        assert "5" in log
        assert "RETURN" in log
        assert "8" in log

    def test_logs_kwargs(self) -> None:
        stream, _ = _capture_log("tests.test_log_decorator")

        @log_call
        def build(name: str, age: int = 0) -> dict:
            return {"name": name, "age": age}

        build("Jar", age=10)
        log = stream.getvalue()
        assert "'Jar'" in log
        assert "age=10" in log

    def test_logs_error_and_re_raises(self) -> None:
        stream, _ = _capture_log("tests.test_log_decorator")

        @log_call
        def explode() -> None:
            raise ValueError("kaboom")

        with pytest.raises(ValueError, match="kaboom"):
            explode()

        log = stream.getvalue()
        assert "ERROR" in log
        assert "kaboom" in log

    def test_truncates_long_return_values(self) -> None:
        stream, _ = _capture_log("tests.test_log_decorator")

        @log_call
        def big_list() -> list[int]:
            return list(range(1000))

        big_list()
        log = stream.getvalue()
        # Return repr should be truncated (300 chars max + …)
        return_line = [l for l in log.splitlines() if "RETURN" in l][0]
        assert len(return_line) < 500  # generous upper bound

    def test_preserves_function_metadata(self) -> None:
        @log_call
        def documented(x: int) -> str:
            """Squares the input."""
            return str(x * x)

        assert documented.__name__ == "documented"
        assert documented.__doc__ == "Squares the input."

    def test_includes_qualname_in_log(self) -> None:
        stream, _ = _capture_log("tests.test_log_decorator")

        class Worker:
            @log_call
            def work(self, hours: int) -> str:
                return f"{hours}h"

        Worker().work(4)
        log = stream.getvalue()
        assert "Worker.work" in log
