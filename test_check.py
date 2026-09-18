import contextlib
import datetime
import email.message
import email.utils
import io
import types
import unittest
import urllib.error
from unittest.mock import call, patch

import check


def http_error(status, headers=None):
    message = email.message.Message()
    for name, value in (headers or {}).items():
        message[name] = value
    return urllib.error.HTTPError(
        "https://index.crates.io/2/uv", status, "failure", message, io.BytesIO()
    )


def success():
    return contextlib.nullcontext(types.SimpleNamespace(status=200))


class CrateLookupTests(unittest.TestCase):
    def setUp(self):
        self.urlopen = self.enterContext(patch("check.urllib.request.urlopen"))
        self.sleep = self.enterContext(patch("check.time.sleep"))
        self.stderr = self.enterContext(contextlib.redirect_stderr(io.StringIO()))

    def test_transient_errors_recover(self):
        for error in (
            *(http_error(status) for status in (403, 408, 429, 500, 502, 503, 504)),
            urllib.error.URLError("connection reset"),
            TimeoutError("timed out"),
        ):
            with self.subTest(error=error):
                self.urlopen.reset_mock()
                self.sleep.reset_mock()
                self.urlopen.side_effect = [error, success()]
                self.assertTrue(check.crate_exists("uv"))
                self.assertEqual(self.urlopen.call_count, 2)
                self.sleep.assert_called_once_with(1)

    def test_missing_crate_is_not_retried(self):
        self.urlopen.side_effect = http_error(404)
        self.assertFalse(check.crate_exists("uv"))
        self.urlopen.assert_called_once()
        self.sleep.assert_not_called()

    def test_permanent_error_fails_immediately(self):
        for status in (400, 401, 405, 501):
            with self.subTest(status=status):
                self.urlopen.reset_mock()
                self.urlopen.side_effect = http_error(status)
                with self.assertRaisesRegex(RuntimeError, f"HTTP {status}"):
                    check.crate_exists("uv")
                self.urlopen.assert_called_once()
                self.sleep.assert_not_called()

    def test_exhaustion_preserves_diagnostics(self):
        self.urlopen.side_effect = [
            http_error(
                403,
                {"Server": "AmazonS3", "X-Cache": "MISS", "X-Amz-Request-Id": "abc"},
            )
            for _ in range(4)
        ]
        with self.assertRaisesRegex(RuntimeError, "HTTP 403") as error:
            check.crate_exists("uv")
        self.assertIn("server='AmazonS3'", str(error.exception))
        self.assertIn("x-cache='MISS'", str(error.exception))
        self.assertIn("x-amz-request-id='abc'", str(error.exception))
        self.assertEqual(self.urlopen.call_count, 4)
        self.assertEqual(self.sleep.call_args_list, [call(1), call(2), call(4)])
        self.assertIn("attempt 4/4", self.stderr.getvalue())

    def test_retry_after_seconds(self):
        self.urlopen.side_effect = [http_error(429, {"Retry-After": "9"}), success()]
        self.assertTrue(check.crate_exists("uv"))
        self.sleep.assert_called_once_with(9)

    def test_retry_after_date(self):
        now = datetime.datetime(2026, 9, 18, tzinfo=datetime.UTC)
        retry_at = email.utils.format_datetime(now + datetime.timedelta(seconds=12))
        self.urlopen.side_effect = [
            http_error(503, {"Retry-After": retry_at}),
            success(),
        ]
        with patch("check.time.time", return_value=now.timestamp()):
            self.assertTrue(check.crate_exists("uv"))
        self.sleep.assert_called_once_with(12)

    def test_excessive_retry_after_does_not_retry_early(self):
        self.urlopen.side_effect = http_error(429, {"Retry-After": "300"})
        with self.assertRaisesRegex(RuntimeError, "retry-after='300'"):
            check.crate_exists("uv")
        self.urlopen.assert_called_once()
        self.sleep.assert_not_called()

    def test_invalid_retry_after_uses_backoff(self):
        self.urlopen.side_effect = [
            http_error(503, {"Retry-After": "invalid"}),
            success(),
        ]
        self.assertTrue(check.crate_exists("uv"))
        self.sleep.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
