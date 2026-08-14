from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
import yfinance as yf

from src.market_context import build_market_context, diagnostics, normalized_gap_state


MA_PERIODS = [5, 8, 10, 13, 20, 21, 34, 50, 55, 89, 100, 144, 200, 233, 377]
BG = "#0f172a"
PANEL = "#111827"
HEADER = "#223044"
WHITE = "#f8fafc"
MUTED = "#cbd5e1"
GREEN = "#166534"
LIGHT_GREEN = "#22c55e"
RED = "#991b1b"
LIGHT_RED = "#ef4444"
YELLOW = "#a16207"
BLUE = "#2563eb"
PURPLE = "#7e22ce"
GRAY = "#475569"


@dataclass(frozen=True)
class ScanConfig:
    ticker: str
    market: str = "BIST"
    period: str = "2y"
    interval: str = "1d"
    equality_tolerance_pct: float = 0.02
    provider: str = "AUTO"
    anchor_date: str = ""


def normalize_symbol(ticker: str, market: str) -> str:
    symbol = ticker.strip().upper()
    if not symbol:
        raise ValueError("Hisse sembolü boş olamaz.")
    market = market.upper()
    if market == "BIST" and "." not in symbol:
        return f"{symbol}.IS"
    return symbol


def validate_price_data(data: pd.DataFrame, symbol: str, provider: str) -> pd.DataFrame:
    if data.empty:
        raise RuntimeError(f"{symbol} için {provider} fiyat verisi bulunamadı.")
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [column for column in required if column not in data.columns]
    if missing:
        raise RuntimeError(f"{provider} verisinde eksik fiyat sütunları: {', '.join(missing)}")
    data = data[required].dropna(subset=["Open", "High", "Low", "Close"]).copy()
    data["Volume"] = data["Volume"].fillna(0.0)
    if len(data) < max(MA_PERIODS) + 5:
        raise RuntimeError(
            f"{symbol} için en az {max(MA_PERIODS) + 5} bar gerekli; yalnızca {len(data)} bar geldi. "
            "Daha uzun bir period seçin."
        )
    data.attrs["provider"] = provider
    return data


def download_yfinance(config: ScanConfig) -> tuple[str, pd.DataFrame]:
    symbol = normalize_symbol(config.ticker, config.market)
    data = yf.download(
        symbol,
        period=config.period,
        interval=config.interval,
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    return symbol, validate_price_data(data, symbol, "yfinance")


def download_borsapy(config: ScanConfig) -> tuple[str, pd.DataFrame]:
    if config.market.upper() != "BIST":
        raise ValueError("borsapy sağlayıcısı bu raporda yalnızca BIST hisseleri için kullanılabilir.")
    try:
        import borsapy as bp
    except ImportError as exc:
        raise RuntimeError("borsapy kurulu değil; requirements.txt bağımlılıklarını yükleyin.") from exc
    symbol = config.ticker.strip().upper().removesuffix(".IS").removesuffix(".E")
    if not symbol:
        raise ValueError("Hisse sembolü boş olamaz.")
    data = bp.Ticker(symbol).history(period=config.period, interval=config.interval)
    return symbol, validate_price_data(data, symbol, "borsapy/TradingView")


def download_prices(config: ScanConfig) -> tuple[str, pd.DataFrame]:
    provider = config.provider.strip().upper()
    if provider not in {"AUTO", "BORSAPY", "YFINANCE"}:
        raise ValueError(f"Geçersiz veri sağlayıcısı: {config.provider}")
    if provider == "BORSAPY":
        return download_borsapy(config)
    if provider == "YFINANCE" or config.market.upper() != "BIST":
        return download_yfinance(config)
    try:
        return download_borsapy(config)
    except Exception as exc:
        print(f"Uyarı: borsapy/TradingView başarısız oldu ({exc}); yfinance yedeği deneniyor.")
        return download_yfinance(config)


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False, min_periods=length).mean()


def rma(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    change = series.diff()
    gain = change.clip(lower=0)
    loss = -change.clip(upper=0)
    avg_gain = rma(gain, length)
    avg_loss = rma(loss, length)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100 - 100 / (1 + rs)
    return result.where(avg_loss != 0, 100.0)


def true_range(data: pd.DataFrame) -> pd.Series:
    return pd.concat(
        [
            data["High"] - data["Low"],
            (data["High"] - data["Close"].shift()).abs(),
            (data["Low"] - data["Close"].shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)


def adx_dmi(data: pd.DataFrame, length: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    up_move = data["High"].diff()
    down_move = -data["Low"].diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=data.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=data.index)
    atr_value = rma(true_range(data), length)
    plus_di = 100 * rma(plus_dm, length) / atr_value.replace(0, np.nan)
    minus_di = 100 * rma(minus_dm, length) / atr_value.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return plus_di, minus_di, rma(dx, length)


def supertrend(data: pd.DataFrame, length: int = 10, factor: float = 3.0) -> tuple[pd.Series, pd.Series]:
    atr_value = rma(true_range(data), length)
    hl2 = (data["High"] + data["Low"]) / 2
    upper = hl2 + factor * atr_value
    lower = hl2 - factor * atr_value
    final_upper = upper.copy()
    final_lower = lower.copy()
    trend = pd.Series(index=data.index, dtype=float)
    direction = pd.Series(index=data.index, dtype=float)
    for i in range(1, len(data)):
        if pd.isna(atr_value.iloc[i]):
            continue
        prev_close = data["Close"].iloc[i - 1]
        if pd.notna(final_upper.iloc[i - 1]) and not (upper.iloc[i] < final_upper.iloc[i - 1] or prev_close > final_upper.iloc[i - 1]):
            final_upper.iloc[i] = final_upper.iloc[i - 1]
        if pd.notna(final_lower.iloc[i - 1]) and not (lower.iloc[i] > final_lower.iloc[i - 1] or prev_close < final_lower.iloc[i - 1]):
            final_lower.iloc[i] = final_lower.iloc[i - 1]
        previous_direction = direction.iloc[i - 1]
        if pd.isna(previous_direction):
            previous_direction = 1.0
        if previous_direction < 0 and data["Close"].iloc[i] > final_upper.iloc[i]:
            direction.iloc[i] = 1.0
        elif previous_direction > 0 and data["Close"].iloc[i] < final_lower.iloc[i]:
            direction.iloc[i] = -1.0
        else:
            direction.iloc[i] = previous_direction
        trend.iloc[i] = final_lower.iloc[i] if direction.iloc[i] > 0 else final_upper.iloc[i]
    return trend, direction


def parabolic_sar(data: pd.DataFrame, start: float = 0.02, increment: float = 0.02, maximum: float = 0.20) -> pd.Series:
    high = data["High"].to_numpy(dtype=float)
    low = data["Low"].to_numpy(dtype=float)
    close = data["Close"].to_numpy(dtype=float)
    result = np.full(len(data), np.nan)
    if len(data) < 2:
        return pd.Series(result, index=data.index)
    bullish = close[1] >= close[0]
    extreme = high[0] if bullish else low[0]
    sar = low[0] if bullish else high[0]
    acceleration = start
    for i in range(1, len(data)):
        sar = sar + acceleration * (extreme - sar)
        if bullish:
            sar = min(sar, low[i - 1], low[i - 2] if i > 1 else low[i - 1])
            if low[i] < sar:
                bullish = False
                sar = extreme
                extreme = low[i]
                acceleration = start
            elif high[i] > extreme:
                extreme = high[i]
                acceleration = min(acceleration + increment, maximum)
        else:
            sar = max(sar, high[i - 1], high[i - 2] if i > 1 else high[i - 1])
            if high[i] > sar:
                bullish = True
                sar = extreme
                extreme = high[i]
                acceleration = start
            elif low[i] < extreme:
                extreme = low[i]
                acceleration = min(acceleration + increment, maximum)
        result[i] = sar
    return pd.Series(result, index=data.index)


def percentile_rank(series: pd.Series, length: int) -> pd.Series:
    def rank(values: np.ndarray) -> float:
        valid = values[~np.isnan(values)]
        if len(valid) == 0:
            return np.nan
        return float(np.count_nonzero(valid <= valid[-1]) / len(valid) * 100)

    return series.rolling(length, min_periods=max(10, length // 3)).apply(rank, raw=True)


def calculate_indicators(data: pd.DataFrame) -> pd.DataFrame:
    out = data.copy()
    close = out["Close"]
    for length in MA_PERIODS:
        out[f"SMA_{length}"] = close.rolling(length).mean()
        out[f"EMA_{length}"] = ema(close, length)
    ema_columns = [f"EMA_{length}" for length in MA_PERIODS]
    out["MA_SPREAD_PCT"] = 100 * (out[ema_columns].max(axis=1) - out[ema_columns].min(axis=1)) / close
    out["MA_SPREAD_RANK"] = percentile_rank(out["MA_SPREAD_PCT"], 252)

    out["RSI"] = rsi(close, 14)
    out["RSI_MA"] = out["RSI"].rolling(14).mean()

    out["MACD"] = ema(close, 12) - ema(close, 26)
    out["MACD_SIGNAL"] = ema(out["MACD"], 9)
    out["MACD_HIST"] = out["MACD"] - out["MACD_SIGNAL"]
    out["MACD_HIST_RANK"] = percentile_rank(out["MACD_HIST"].abs(), 252)

    stoch_rsi = rsi(close, 14)
    stoch_low = stoch_rsi.rolling(14).min()
    stoch_high = stoch_rsi.rolling(14).max()
    out["STOCH_RSI_RAW"] = 100 * (stoch_rsi - stoch_low) / (stoch_high - stoch_low).replace(0, np.nan)
    out["STOCH_K"] = out["STOCH_RSI_RAW"].rolling(3).mean()
    out["STOCH_D"] = out["STOCH_K"].rolling(3).mean()

    highest = out["High"].rolling(10).max()
    lowest = out["Low"].rolling(10).min()
    relative = close - (highest + lowest) / 2
    price_range = highest - lowest
    double_relative = ema(ema(relative, 3), 3)
    double_range = ema(ema(price_range, 3), 3)
    out["SMI"] = 200 * double_relative / double_range.replace(0, np.nan)
    out["SMI_EMA"] = ema(out["SMI"], 3)

    out["BB_MID"] = close.rolling(20).mean()
    bb_std = close.rolling(20).std(ddof=0)
    out["BB_UPPER"] = out["BB_MID"] + 2 * bb_std
    out["BB_LOWER"] = out["BB_MID"] - 2 * bb_std
    out["BB_WIDTH"] = 100 * (out["BB_UPPER"] - out["BB_LOWER"]) / out["BB_MID"]
    out["BB_WIDTH_RANK"] = percentile_rank(out["BB_WIDTH"], 100)

    out["ATR"] = rma(true_range(out), 14)
    out["ATR_PCT"] = 100 * out["ATR"] / close
    out["ATR_RANK"] = percentile_rank(out["ATR_PCT"], 252)
    out["PLUS_DI"], out["MINUS_DI"], out["ADX"] = adx_dmi(out, 14)
    out["ADX_RANK"] = percentile_rank(out["ADX"], 252)
    out["SUPERTREND"], out["SUPERTREND_DIR"] = supertrend(out, 10, 3.0)

    typical = (out["High"] + out["Low"] + close) / 3
    raw_money = typical * out["Volume"]
    positive_money = raw_money.where(typical.diff() > 0, 0.0).rolling(14).sum()
    negative_money = raw_money.where(typical.diff() < 0, 0.0).rolling(14).sum()
    money_ratio = positive_money / negative_money.replace(0, np.nan)
    out["MFI"] = (100 - 100 / (1 + money_ratio)).where(negative_money != 0, 100.0)
    out["MFI_MA"] = out["MFI"].rolling(14).mean()

    out["OBV"] = (np.sign(close.diff()).fillna(0) * out["Volume"]).cumsum()
    out["OBV_EMA"] = ema(out["OBV"], 20)
    mean_typical = typical.rolling(20).mean()
    mean_deviation = typical.rolling(20).apply(lambda values: np.mean(np.abs(values - values.mean())), raw=True)
    out["CCI"] = (typical - mean_typical) / (0.015 * mean_deviation.replace(0, np.nan))
    out["CCI_MA"] = out["CCI"].rolling(20).mean()

    cumulative_volume = out["Volume"].cumsum().replace(0, np.nan)
    out["VWAP"] = (typical * out["Volume"]).cumsum() / cumulative_volume
    out["VOLUME_MA"] = out["Volume"].rolling(20).mean()
    out["VOLUME_RATIO"] = out["Volume"] / out["VOLUME_MA"].replace(0, np.nan)
    out["VOLUME_RANK"] = percentile_rank(out["Volume"], 252)

    out["TENKAN"] = (out["High"].rolling(9).max() + out["Low"].rolling(9).min()) / 2
    out["KIJUN"] = (out["High"].rolling(26).max() + out["Low"].rolling(26).min()) / 2
    span_a = (out["TENKAN"] + out["KIJUN"]) / 2
    span_b = (out["High"].rolling(52).max() + out["Low"].rolling(52).min()) / 2
    out["VISIBLE_SPAN_A"] = span_a.shift(26)
    out["VISIBLE_SPAN_B"] = span_b.shift(26)
    out["FUTURE_SPAN_A"] = span_a
    out["FUTURE_SPAN_B"] = span_b
    out["PSAR"] = parabolic_sar(out)
    return out


def crossed_up(main: pd.Series, signal: pd.Series | float) -> bool:
    if isinstance(signal, (float, int)):
        return bool(main.iloc[-1] > signal and main.iloc[-2] <= signal)
    return bool(main.iloc[-1] > signal.iloc[-1] and main.iloc[-2] <= signal.iloc[-2])


def crossed_down(main: pd.Series, signal: pd.Series | float) -> bool:
    if isinstance(signal, (float, int)):
        return bool(main.iloc[-1] < signal and main.iloc[-2] >= signal)
    return bool(main.iloc[-1] < signal.iloc[-1] and main.iloc[-2] >= signal.iloc[-2])


def relation_color(price: float, average: float, tolerance_pct: float) -> tuple[str, str]:
    if not math.isfinite(average):
        return "—", GRAY
    distance_pct = abs(price - average) / abs(average) * 100 if average else math.inf
    if distance_pct <= tolerance_pct:
        return "Eşit/Yakın", YELLOW
    if price > average:
        return "Fiyat üstünde", GREEN
    return "Fiyat altında", RED


def gap_state(main: pd.Series, signal: pd.Series) -> str:
    gap = main - signal
    current = float(gap.iloc[-1])
    narrowing = abs(current) < abs(float(gap.iloc[-2]))
    if current > 0:
        return "Pozitif fark daralıyor" if narrowing else "Pozitif fark açılıyor"
    if current < 0:
        return "Negatif fark daralıyor" if narrowing else "Negatif fark açılıyor"
    return "Çizgiler eşit"


def cross_text(main: pd.Series, signal: pd.Series, zone: str) -> str:
    if crossed_up(main, signal):
        return f"↑ Yukarı kesişim ({zone})"
    if crossed_down(main, signal):
        return f"↓ Aşağı kesişim ({zone})"
    return "Ana > Sinyal" if main.iloc[-1] > signal.iloc[-1] else "Ana < Sinyal"


def fmt(value: Any, digits: int = 2) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "—"
    return "—" if not math.isfinite(numeric) else f"{numeric:,.{digits}f}"


def tone_color(tone: str) -> str:
    return {
        "positive": GREEN,
        "negative": RED,
        "warning": YELLOW,
        "purple": PURPLE,
        "blue": BLUE,
        "neutral": GRAY,
    }.get(tone, GRAY)


def diagnostic_text(series: pd.Series) -> str:
    values = diagnostics(series)
    return f"Δ1 {fmt(values['delta_1'])} | Δ3 {fmt(values['delta_3'])} | Eğim5 {fmt(values['slope_5'])}"


def build_status(data: pd.DataFrame, config: ScanConfig, symbol: str) -> dict[str, Any]:
    row = data.iloc[-1]
    previous = data.iloc[-2]
    price = float(row["Close"])
    ma_rows = []
    for length in MA_PERIODS:
        sma_value = float(row[f"SMA_{length}"])
        ema_value = float(row[f"EMA_{length}"])
        sma_relation, sma_color = relation_color(price, sma_value, config.equality_tolerance_pct)
        ema_relation, ema_color = relation_color(price, ema_value, config.equality_tolerance_pct)
        ma_rows.append(
            {
                "period": length,
                "sma": sma_value,
                "sma_relation": sma_relation,
                "sma_color": sma_color,
                "ema": ema_value,
                "ema_relation": ema_relation,
                "ema_color": ema_color,
            }
        )

    hist = float(row["MACD_HIST"])
    prev_hist = float(previous["MACD_HIST"])
    if hist < 0:
        hist_status = "Düşüş momentumu azalıyor" if hist > prev_hist else "Düşüş momentumu artıyor"
        hist_color = YELLOW if hist > prev_hist else RED
    elif hist > 0:
        hist_status = "Yükseliş momentumu artıyor" if hist > prev_hist else "Yükseliş momentumu azalıyor"
        hist_color = GREEN if hist > prev_hist else LIGHT_GREEN
    else:
        hist_status = "Histogram sıfırda"
        hist_color = GRAY

    rsi_value = float(row["RSI"])
    rsi_zone = "Aşırı alım" if rsi_value >= 70 else "Aşırı satım" if rsi_value <= 30 else "50 üzeri" if rsi_value >= 50 else "50 altı"
    stoch_value = float(row["STOCH_K"])
    stoch_zone = "80 üzeri" if stoch_value >= 80 else "20 altı" if stoch_value <= 20 else "Orta bölge"
    smi_value = float(row["SMI"])
    smi_zone = "+40 üzeri" if smi_value > 40 else "-40 altı" if smi_value < -40 else "0 üzeri" if smi_value > 0 else "0 altı"
    mfi_value = float(row["MFI"])
    cci_value = float(row["CCI"])

    macd_relation = "MACD > Signal" if row["MACD"] > row["MACD_SIGNAL"] else "MACD < Signal"
    macd_zero = "MACD > 0" if row["MACD"] > 0 else "MACD < 0"
    momentum = [
        ["MACD", f"M {fmt(row['MACD'])} | S {fmt(row['MACD_SIGNAL'])} | H {fmt(hist)}\n{diagnostic_text(data['MACD'])}", f"{hist_status}\n{macd_relation} | {macd_zero} | Hist perc %{fmt(row['MACD_HIST_RANK'], 0)} | {normalized_gap_state(data['MACD'], data['MACD_SIGNAL'])}", hist_color],
        ["RSI", f"RSI {fmt(rsi_value)} | MA14 {fmt(row['RSI_MA'])}\n{diagnostic_text(data['RSI'])}", f"{cross_text(data['RSI'], data['RSI_MA'], rsi_zone)}\n{normalized_gap_state(data['RSI'], data['RSI_MA'])}", GREEN if rsi_value > row["RSI_MA"] else RED],
        ["Stoch RSI", f"K {fmt(row['STOCH_K'])} | D {fmt(row['STOCH_D'])}\n{diagnostic_text(data['STOCH_K'])}", f"{cross_text(data['STOCH_K'], data['STOCH_D'], stoch_zone)}\n{normalized_gap_state(data['STOCH_K'], data['STOCH_D'])}", GREEN if row["STOCH_K"] > row["STOCH_D"] else RED],
        ["SMI", f"SMI {fmt(smi_value)} | EMA3 {fmt(row['SMI_EMA'])}\n{diagnostic_text(data['SMI'])}", f"{cross_text(data['SMI'], data['SMI_EMA'], smi_zone)}\n{normalized_gap_state(data['SMI'], data['SMI_EMA'])}", GREEN if smi_value > row["SMI_EMA"] else RED],
        ["MFI", f"MFI {fmt(mfi_value)} | MA14 {fmt(row['MFI_MA'])}\n{diagnostic_text(data['MFI'])}", f"{cross_text(data['MFI'], data['MFI_MA'], '20/50/80')}\n{normalized_gap_state(data['MFI'], data['MFI_MA'])}", GREEN if mfi_value > row["MFI_MA"] else RED],
        ["CCI", f"CCI {fmt(cci_value)} | MA20 {fmt(row['CCI_MA'])}\n{diagnostic_text(data['CCI'])}", f"{cross_text(data['CCI'], data['CCI_MA'], '-100/0/+100')}\n{normalized_gap_state(data['CCI'], data['CCI_MA'])}", GREEN if cci_value > row["CCI_MA"] else RED],
    ]

    bb_rank = float(row["BB_WIDTH_RANK"])
    bb_state = "Aşırı dar" if bb_rank <= 10 else "Dar" if bb_rank <= 20 else "Çok geniş" if bb_rank >= 90 else "Geniş" if bb_rank >= 80 else "Normal"
    bb_direction = "Genişliyor" if row["BB_WIDTH"] > previous["BB_WIDTH"] else "Daralıyor"
    bb_position = "Üst bant üzerinde" if price > row["BB_UPPER"] else "Alt bant altında" if price < row["BB_LOWER"] else "Orta bant üzerinde" if price > row["BB_MID"] else "Orta bant altında"
    cloud_top = max(float(row["VISIBLE_SPAN_A"]), float(row["VISIBLE_SPAN_B"]))
    cloud_bottom = min(float(row["VISIBLE_SPAN_A"]), float(row["VISIBLE_SPAN_B"]))
    cloud_state = "Bulut üstü" if price > cloud_top else "Bulut altı" if price < cloud_bottom else "Bulut içi"
    trend = [
        ["ADX/DMI", f"ADX {fmt(row['ADX'])} | +DI {fmt(row['PLUS_DI'])} | -DI {fmt(row['MINUS_DI'])}", f"{'+DI üstün' if row['PLUS_DI'] > row['MINUS_DI'] else '-DI üstün'} | ADX perc %{fmt(row['ADX_RANK'], 0)} | {diagnostic_text(data['ADX'])}", GREEN if row["PLUS_DI"] > row["MINUS_DI"] else RED],
        ["Supertrend", fmt(row["SUPERTREND"]), "Fiyat üstünde" if price > row["SUPERTREND"] else "Fiyat altında", GREEN if price > row["SUPERTREND"] else RED],
        ["Kümülatif VWAP", fmt(row["VWAP"]), f"Fiyat {'üstünde' if price > row['VWAP'] else 'altında'} | Mesafe {(price / row['VWAP'] - 1) * 100:+.2f}%", GREEN if price > row["VWAP"] else RED],
        ["Ichimoku", f"Tenkan {fmt(row['TENKAN'])} | Kijun {fmt(row['KIJUN'])}", cloud_state, GREEN if cloud_state == "Bulut üstü" else RED if cloud_state == "Bulut altı" else YELLOW],
        ["Parabolic SAR", fmt(row["PSAR"]), "SAR fiyat altında" if row["PSAR"] < price else "SAR fiyat üzerinde", GREEN if row["PSAR"] < price else RED],
        ["Bollinger", f"Alt {fmt(row['BB_LOWER'])} | Orta {fmt(row['BB_MID'])} | Üst {fmt(row['BB_UPPER'])}", f"{bb_position} | {bb_state} / {bb_direction} | Perc %{fmt(bb_rank, 0)}", BLUE if bb_rank <= 20 else PURPLE if bb_rank >= 80 else GRAY],
        ["ATR", f"ATR {fmt(row['ATR'])} | ATR% {fmt(row['ATR_PCT'])}", f"Percentile %{fmt(row['ATR_RANK'], 0)} | {diagnostic_text(data['ATR_PCT'])}", PURPLE],
        ["Hacim", f"{fmt(row['Volume'], 0)} | Ort. {fmt(row['VOLUME_MA'], 0)}", f"{fmt(row['VOLUME_RATIO'])}x | Perc %{fmt(row['VOLUME_RANK'], 0)}", PURPLE if row["VOLUME_RATIO"] >= 1.2 else GRAY],
        ["OBV", f"{fmt(row['OBV'], 0)} | EMA20 {fmt(row['OBV_EMA'], 0)}", f"{normalized_gap_state(data['OBV'], data['OBV_EMA'])} | {diagnostic_text(data['OBV'])}", GREEN if row["OBV"] > row["OBV_EMA"] else RED],
    ]

    context = build_market_context(data, MA_PERIODS, config.anchor_date)
    executive = [[item[0], item[1], item[2], tone_color(item[3])] for item in context["families"]]
    location = [[item[0], item[1], item[2], tone_color(item[3])] for item in context["location_rows"]]
    participation = [[item[0], item[1], item[2], tone_color(item[3])] for item in context["participation_rows"]]
    events = []
    for item in context["events"]:
        age_text = "Bu bar" if item["age"] == 0 else f"{item['age']} bar önce"
        event_color = GREEN if "↑" in item["event"] or "High üzeri" in item["event"] else RED if "↓" in item["event"] or "Low altı" in item["event"] else GRAY
        events.append([item["event"], age_text, item["state"], event_color])

    return {
        "data_provider": data.attrs.get("provider", config.provider),
        "symbol": symbol,
        "requested_ticker": config.ticker,
        "timestamp": data.index[-1].isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "price": price,
        "change_pct": (price / float(previous["Close"]) - 1) * 100,
        "period": config.period,
        "interval": config.interval,
        "anchor_date": config.anchor_date,
        "equality_tolerance_pct": config.equality_tolerance_pct,
        "ma": ma_rows,
        "momentum": momentum,
        "trend_volatility_volume": trend,
        "executive": executive,
        "location": location,
        "participation": participation,
        "events": events,
        "market_context": context,
    }


def draw_table(ax: plt.Axes, title: str, columns: list[str], rows: list[list[str]], colors: list[list[str]] | None = None, font_size: int = 10, col_widths: list[float] | None = None) -> None:
    ax.set_facecolor(PANEL)
    ax.axis("off")
    ax.set_title(title, color=WHITE, fontsize=15, fontweight="bold", loc="left", pad=10)
    table = ax.table(cellText=rows, colLabels=columns, colWidths=col_widths, loc="center", cellLoc="left", colLoc="center", bbox=[0, 0, 1, 0.94])
    table.auto_set_font_size(False)
    table.set_fontsize(font_size)
    for (row_index, column_index), cell in table.get_celld().items():
        cell.set_edgecolor("#334155")
        cell.get_text().set_color(WHITE)
        cell.get_text().set_wrap(True)
        if row_index == 0:
            cell.set_facecolor(HEADER)
            cell.get_text().set_fontweight("bold")
        else:
            cell.set_facecolor(PANEL)
            if colors and row_index - 1 < len(colors) and column_index < len(colors[row_index - 1]):
                chosen = colors[row_index - 1][column_index]
                if chosen:
                    cell.set_facecolor(chosen)
    if col_widths is None:
        table.auto_set_column_width(col=list(range(len(columns))))


def render_report(data: pd.DataFrame, status: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    plt.rcParams["font.family"] = "DejaVu Sans"
    figure = plt.figure(figsize=(18, 34), dpi=120, facecolor=BG)
    grid = figure.add_gridspec(6, 2, height_ratios=[0.7, 2.2, 3.0, 4.4, 4.2, 5.0], hspace=0.28, wspace=0.14)

    header = figure.add_subplot(grid[0, :])
    header.set_facecolor(BG)
    header.axis("off")
    change_color = LIGHT_GREEN if status["change_pct"] >= 0 else LIGHT_RED
    header.text(0.0, 0.72, f"{status['symbol']} — Technical Market State", color=WHITE, fontsize=27, fontweight="bold")
    header.text(0.0, 0.25, f"Fiyat: {fmt(status['price'])}", color=WHITE, fontsize=20, fontweight="bold")
    header.text(0.20, 0.25, f"Değişim: {status['change_pct']:+.2f}%", color=change_color, fontsize=18, fontweight="bold")
    header.text(0.46, 0.30, f"Bar: {status['timestamp']} | {status['interval']}", color=MUTED, fontsize=11)
    header.text(0.46, 0.08, f"Kaynak: {status['data_provider']}", color=MUTED, fontsize=10)
    header.text(0.0, -0.02, "Durum raporudur; otomatik AL/SAT puanı değildir. Yatırım tavsiyesi değildir.", color="#94a3b8", fontsize=10)

    executive_ax = figure.add_subplot(grid[1, :])
    executive_rows = [[item[0], item[1], item[2]] for item in status["executive"]]
    executive_colors = [[HEADER, item[3], PANEL] for item in status["executive"]]
    draw_table(executive_ax, "Piyasa Durum Haritası", ["Aile", "Durum", "Bağlam"], executive_rows, executive_colors, font_size=11, col_widths=[0.16, 0.38, 0.46])

    chart = figure.add_subplot(grid[2, :])
    chart.set_facecolor(PANEL)
    recent = data.tail(120)
    chart.plot(recent.index, recent["Close"], color=WHITE, linewidth=2.0, label="Kapanış")
    chart.plot(recent.index, recent["EMA_21"], color="#38bdf8", linewidth=1.3, label="EMA21")
    chart.plot(recent.index, recent["EMA_50"], color="#f59e0b", linewidth=1.3, label="EMA50")
    chart.plot(recent.index, recent["EMA_200"], color="#f43f5e", linewidth=1.3, label="EMA200")
    chart.fill_between(recent.index, recent["BB_LOWER"], recent["BB_UPPER"], color="#3b82f6", alpha=0.10, label="Bollinger")
    profile = status["market_context"]["profile"]
    for label, level, color in [("VAH", profile["vah"], "#22c55e"), ("POC", profile["poc"], "#f59e0b"), ("VAL", profile["val"], "#ef4444")]:
        chart.axhline(level, color=color, linewidth=1.0, linestyle="--", alpha=0.8, label=label)
    chart.grid(color="#334155", alpha=0.45)
    chart.tick_params(colors=MUTED)
    chart.spines[:].set_color("#334155")
    chart.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m.%y"))
    chart.legend(facecolor=HEADER, labelcolor=WHITE, loc="upper left", ncol=8)
    chart.set_title("Fiyat • Ortalamalar • Bollinger • Yaklaşık Hacim Profili — Son 120 Bar", color=WHITE, fontsize=15, fontweight="bold", loc="left")

    ma_ax = figure.add_subplot(grid[3, 0])
    ma_rows = [[str(item["period"]), fmt(item["sma"]), fmt(item["ema"])] for item in status["ma"]]
    ma_colors = [[HEADER, item["sma_color"], item["ema_color"]] for item in status["ma"]]
    draw_table(ma_ax, "SMA / EMA Değerleri", ["Periyot", "SMA", "EMA"], ma_rows, ma_colors, font_size=10, col_widths=[0.20, 0.40, 0.40])
    ma_ax.text(0, -0.035, f"Yeşil: fiyat üstünde | Sarı: ±%{status['equality_tolerance_pct']:.2f} yakın | Kırmızı: fiyat altında", transform=ma_ax.transAxes, color=MUTED, fontsize=8)

    momentum_ax = figure.add_subplot(grid[3, 1])
    momentum_rows = [[item[0], item[1], item[2]] for item in status["momentum"]]
    momentum_colors = [[HEADER, PANEL, item[3]] for item in status["momentum"]]
    draw_table(momentum_ax, "Momentum • Kesişim • Eğim", ["Gösterge", "Değerler", "Durum"], momentum_rows, momentum_colors, font_size=7, col_widths=[0.12, 0.38, 0.50])

    trend_ax = figure.add_subplot(grid[4, 0])
    trend_rows = [[item[0], item[1], item[2]] for item in status["trend_volatility_volume"]]
    trend_colors = [[HEADER, PANEL, item[3]] for item in status["trend_volatility_volume"]]
    draw_table(trend_ax, "Trend • Volatilite • Hacim", ["Gösterge", "Değerler", "Durum"], trend_rows, trend_colors, font_size=7, col_widths=[0.14, 0.31, 0.55])

    location_ax = figure.add_subplot(grid[4, 1])
    location_rows = [[item[0], item[1], item[2]] for item in status["location"]]
    location_colors = [[HEADER, PANEL, item[3]] for item in status["location"]]
    draw_table(location_ax, "Konum • AVWAP • POC/VA • Yapı Seviyeleri", ["Alan", "Değerler", "Durum"], location_rows, location_colors, font_size=7, col_widths=[0.14, 0.52, 0.34])

    participation_ax = figure.add_subplot(grid[5, 0])
    participation_rows = [[item[0], item[1], item[2]] for item in status["participation"]]
    participation_colors = [[HEADER, PANEL, item[3]] for item in status["participation"]]
    draw_table(participation_ax, "Katılım • RVOL • Delta/CVD Tahmini", ["Alan", "Değerler", "Durum"], participation_rows, participation_colors, font_size=7, col_widths=[0.14, 0.34, 0.52])

    events_ax = figure.add_subplot(grid[5, 1])
    event_rows = [[item[0], item[1], item[2]] for item in status["events"]]
    event_colors = [[item[3], PANEL, HEADER] for item in status["events"]]
    draw_table(events_ax, "Son 12 Teyitli Olay", ["Olay", "Zaman", "Tür"], event_rows, event_colors, font_size=7, col_widths=[0.50, 0.24, 0.26])

    figure.savefig(output, facecolor=figure.get_facecolor(), bbox_inches="tight")
    plt.close(figure)


def telegram_caption(status: dict[str, Any]) -> str:
    context = status["market_context"]
    return (
        f"📊 {status['symbol']} Teknik Piyasa Durumu\n"
        f"Fiyat: {fmt(status['price'])} ({status['change_pct']:+.2f}%)\n"
        f"Rejim: {context['regime']['state']}\n"
        f"Yapı: {context['structure']['state']} — {context['structure']['event']}\n"
        f"Konum: {context['profile']['position']}\n"
        f"POC: {fmt(context['profile']['poc'])} | VAH: {fmt(context['profile']['vah'])} | VAL: {fmt(context['profile']['val'])}\n"
        f"RVOL: {context['relative_volume']:.2f}x\n"
        f"Kaynak: {status['data_provider']}\n"
        f"Bar: {status['timestamp']}\n\n"
        "Durum raporudur; otomatik AL/SAT puanı değildir. Yatırım tavsiyesi değildir."
    )


def send_telegram(image_path: Path, status: dict[str, Any]) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID GitHub Actions Secrets olarak tanımlanmalıdır.")
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    with image_path.open("rb") as image:
        response = requests.post(
            url,
            data={"chat_id": chat_id, "caption": telegram_caption(status)},
            files={"photo": (image_path.name, image, "image/png")},
            timeout=60,
        )
    if not response.ok:
        raise RuntimeError(f"Telegram gönderimi başarısız: HTTP {response.status_code} — {response.text[:300]}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hisse teknik durum görseli üretir ve isteğe bağlı Telegram'a gönderir.")
    parser.add_argument("--ticker", required=True, help="Örnek: THYAO veya AAPL")
    parser.add_argument("--market", default="BIST", choices=["BIST", "US", "AUTO"])
    parser.add_argument("--provider", default="AUTO", choices=["AUTO", "BORSAPY", "YFINANCE"])
    parser.add_argument("--anchor-date", default="", help="Manuel AVWAP başlangıcı, ör. 2026-01-02")
    parser.add_argument("--period", default="2y")
    parser.add_argument("--interval", default="1d")
    parser.add_argument("--output", default="reports/technical_report.png")
    parser.add_argument("--json-output", default="reports/technical_report.json")
    parser.add_argument("--send-telegram", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = ScanConfig(
        ticker=args.ticker,
        market=args.market,
        period=args.period,
        interval=args.interval,
        provider=args.provider,
        anchor_date=args.anchor_date,
    )
    symbol, prices = download_prices(config)
    calculated = calculate_indicators(prices)
    status = build_status(calculated, config, symbol)
    image_path = Path(args.output)
    json_path = Path(args.json_output)
    render_report(calculated, status, image_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Rapor oluşturuldu: {image_path}")
    print(f"JSON oluşturuldu: {json_path}")
    if args.send_telegram:
        send_telegram(image_path, status)
        print("Telegram raporu gönderildi.")


if __name__ == "__main__":
    main()

