import unittest

import numpy as np
import pandas as pd

from src.market_context import (
    approximate_volume_profile,
    build_market_context,
    market_structure,
    normalized_gap_state,
)
from src.stock_dashboard import MA_PERIODS, calculate_indicators


def contextual_prices(rows: int = 520) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=rows, freq="B", tz="Europe/Istanbul")
    trend = np.linspace(80.0, 150.0, rows)
    wave = np.sin(np.arange(rows) / 7.0) * 4.0
    close = trend + wave
    return pd.DataFrame(
        {
            "Open": close - np.cos(np.arange(rows) / 5.0),
            "High": close + 2.2,
            "Low": close - 2.0,
            "Close": close,
            "Volume": 1_000_000 + (np.arange(rows) % 20) * 75_000,
        },
        index=index,
    )


class MarketContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = calculate_indicators(contextual_prices())

    def test_volume_profile_levels_are_ordered(self) -> None:
        profile = approximate_volume_profile(self.data, lookback=100)
        self.assertLessEqual(profile["val"], profile["poc"])
        self.assertLessEqual(profile["poc"], profile["vah"])
        self.assertGreater(profile["width_pct"], 0)

    def test_market_structure_uses_confirmed_pivots(self) -> None:
        structure = market_structure(self.data)
        self.assertTrue(structure["confirmed"])
        self.assertIn(structure["state"], {"HH / HL", "HH / LL", "LH / HL", "LH / LL"})
        self.assertLess(pd.Timestamp(structure["high_time"]), self.data.index[-1])
        self.assertLess(pd.Timestamp(structure["low_time"]), self.data.index[-1])

    def test_normalized_gap_distinguishes_near_cross(self) -> None:
        main = pd.Series([5.0] * 50 + [1.0, 0.2, -0.05])
        signal = pd.Series([0.0] * len(main))
        self.assertEqual(normalized_gap_state(main, signal), "↑ Kesişime yakın")

    def test_context_contains_all_families(self) -> None:
        context = build_market_context(self.data, MA_PERIODS, "2025-01-02")
        family_names = [row[0] for row in context["families"]]
        self.assertEqual(
            family_names,
            ["REJİM", "YAPI", "KONUM", "TREND", "MOMENTUM", "KATILIM", "VOLATİLİTE"],
        )
        self.assertGreaterEqual(len(context["events"]), 1)
        self.assertIn("gerçek footprint delta değildir", context["order_flow_proxy"]["method"])


if __name__ == "__main__":
    unittest.main()
