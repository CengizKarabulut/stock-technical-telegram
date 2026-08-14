import io
import json
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.send_telegram import send


STATUS = {
    "symbol": "THYAO",
    "price": 305.25,
    "change_pct": -0.89,
    "timestamp": "2026-08-14T09:00:00+03:00",
    "data_provider": "borsapy/TradingView",
    "momentum": [
        ["MACD", "değer", "Pozitif", "renk"],
        ["RSI", "değer", "50 üzeri", "renk"],
    ],
    "trend_volatility_volume": [["ADX/DMI", "değer", "+DI üstün", "renk"]],
}


class TelegramTests(unittest.TestCase):
    def _send_and_payload(self, thread_id: str | None) -> dict:
        environment = {
            "TELEGRAM_BOT_TOKEN": "test-token",
            "TELEGRAM_CHAT_ID": "-1003502567927",
        }
        if thread_id is not None:
            environment["TELEGRAM_MESSAGE_THREAD_ID"] = thread_id
        response = Mock(ok=True, status_code=200, text='{"ok":true}')
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(Path, "read_text", return_value=json.dumps(STATUS)),
            patch.object(Path, "open", return_value=io.BytesIO(b"png")),
            patch("src.send_telegram.requests.post", return_value=response) as post,
        ):
            send(Path("report.png"), Path("report.json"))
        return post.call_args.kwargs["data"]

    def test_general_topic_omits_message_thread_id(self) -> None:
        payload = self._send_and_payload(None)
        self.assertNotIn("message_thread_id", payload)

    def test_explicit_topic_adds_message_thread_id(self) -> None:
        payload = self._send_and_payload("99")
        self.assertEqual(payload["message_thread_id"], "99")


if __name__ == "__main__":
    unittest.main()
