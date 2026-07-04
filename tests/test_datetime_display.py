from __future__ import annotations

import unittest
from datetime import datetime

from app.datetime_display import eastern_naive_from_utc_naive, format_utc_naive_eastern


class DatetimeDisplayTests(unittest.TestCase):
    def test_eastern_naive_from_utc_naive_edt(self) -> None:
        # Jul 3 2026 08:52 UTC -> 04:52 EDT (UTC-4)
        utc = datetime(2026, 7, 3, 8, 52)
        et = eastern_naive_from_utc_naive(utc)
        self.assertEqual(et, datetime(2026, 7, 3, 4, 52))

    def test_eastern_naive_from_utc_naive_est(self) -> None:
        # Jan 15 2026 14:00 UTC -> 09:00 EST (UTC-5)
        utc = datetime(2026, 1, 15, 14, 0)
        et = eastern_naive_from_utc_naive(utc)
        self.assertEqual(et, datetime(2026, 1, 15, 9, 0))

    def test_format_utc_naive_eastern_includes_et_label(self) -> None:
        utc = datetime(2026, 7, 3, 8, 52)
        self.assertEqual(format_utc_naive_eastern(utc), "2026-07-03 04:52 ET")

    def test_format_utc_naive_eastern_none(self) -> None:
        self.assertEqual(format_utc_naive_eastern(None), "")

    def test_format_iso_string_utc(self) -> None:
        self.assertEqual(
            format_utc_naive_eastern("2026-07-03T08:52:00"),
            "2026-07-03 04:52 ET",
        )

    def test_format_iso_string_with_z(self) -> None:
        self.assertEqual(
            format_utc_naive_eastern("2026-07-03T08:52:00Z"),
            "2026-07-03 04:52 ET",
        )


if __name__ == "__main__":
    unittest.main()
