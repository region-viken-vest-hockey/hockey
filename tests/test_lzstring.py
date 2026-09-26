"""Tests for the lz-string codec used to read StyledCalendar's events API.

``decompress_from_utf16`` is the production path (StyledCalendar always
sends data compressed by the real JS ``compressToUTF16``); ``compress_to_utf16``
exists only so the decoder can be roundtrip-tested here without a live
network fixture.
"""

import json

from tournament_scheduler.utils.lzstring import compress_to_utf16, decompress_from_utf16


class TestRoundtrip:
    def test_empty_string(self):
        assert decompress_from_utf16(compress_to_utf16("")) == ""

    def test_single_character(self):
        assert decompress_from_utf16(compress_to_utf16("a")) == "a"

    def test_ascii_text(self):
        text = "hello world, this is a test"
        assert decompress_from_utf16(compress_to_utf16(text)) == text

    def test_repeated_pattern(self):
        text = "abcabcabcabcabcabcabcabc"
        assert decompress_from_utf16(compress_to_utf16(text)) == text

    def test_unicode_text(self):
        text = "Bærum ishall — Ærø ÆØÅ 日本語"
        assert decompress_from_utf16(compress_to_utf16(text)) == text

    def test_json_payload_shape(self):
        payload = json.dumps([
            {
                "id": "abc123",
                "title": "Jutul U11",
                "start": "2026-09-26T17:00:00+02:00",
                "end": "2026-09-26T18:00:00+02:00",
                "allDay": False,
                "recurrence": ["RRULE:FREQ=WEEKLY;UNTIL=20270101T000000Z;BYDAY=SA"],
                "exdate": [],
            },
        ])
        compressed = compress_to_utf16(payload)
        assert decompress_from_utf16(compressed) == payload
        assert json.loads(decompress_from_utf16(compressed)) == json.loads(payload)


class TestDecompressEdgeCases:
    def test_none_returns_empty_string(self):
        assert decompress_from_utf16(None) == ""

    def test_empty_string_returns_none(self):
        assert decompress_from_utf16("") is None
