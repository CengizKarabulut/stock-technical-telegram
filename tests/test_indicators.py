import unittest

import numpy as np
import pandas as pd

from src.stock_dashboard import (
    MA_PERIODS,
    ScanConfig,
    build_status,
    calculate_indicators,
    normalize_symbol,
    validate_price_data,
)


def synthetic_prices(rows: int = 500) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=rows, freq="D")
    trend = np.linspace(20.0, 120.0, rows)
    wave = np.sin(np.arange(rows) / 8.0) * 2.0
    close = trend + wave
    return pd.DataFrame(
        {
            "Open": close - 0.4,
            "High": close + 1.1,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.linspace(1_000_000, 2_000_000, rows),
        },
        index=index,
    )


class IndicatorTests(unittest.TestCase):
    def test_bist_symbol_suffix(self) -> None:
        self.assertEqual(normalize_symbol("thyao", "BIST"), "THYAO.IS")
        self.assertEqual(normalize_symbol("THYAO.IS", "BIST"), "THYAO.IS")
        self.assertEqual(normalize_symbol("aapl", "US"), "AAPL")

    def test_price_validation_preserves_provider(self) -> None:
        data = validate_price_data(synthetic_prices(), "TEST", "borsapy/TradingView")
        self.assertEqual(data.attrs["provider"], "borsapy/TradingView")
        with self.assertRaisesRegex(RuntimeError, "en az 382 bar"):
            validate_price_data(synthetic_prices(100), "TEST", "test")

    def test_all_ma_periods_are_calculated(self) -> None:
        result = calculate_indicators(synthetic_prices())
        for length in MA_PERIODS:
            self.assertIn(f"SMA_{length}", result.columns)
            self.assertIn(f"EMA_{length}", result.columns)
            self.assertTrue(np.isfinite(result[f"SMA_{length}"].iloc[-1]))
            self.assertTrue(np.isfinite(result[f"EMA_{length}"].iloc[-1]))

    def test_status_contains_fifteen_ma_rows(self) -> None:
        result = calculate_indicators(synthetic_prices())
        status = build_status(result, ScanConfig("TEST", "US"), "TEST")
        self.assertEqual(len(status["ma"]), 15)
        self.assertEqual([item["period"] for item in status["ma"]], MA_PERIODS)

    def test_core_indicators_are_finite(self) -> None:
        row = calculate_indicators(synthetic_prices()).iloc[-1]
        for column in ["RSI", "MACD", "MACD_SIGNAL", "SMI", "SMI_EMA", "ATR", "ADX", "MFI", "CCI", "OBV"]:
            self.assertTrue(np.isfinite(row[column]), column)


if __name__ == "__main__":
    unittest.main()

