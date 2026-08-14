from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import requests


DEFAULT_CHAT_ID = "-1003502567927"
DEFAULT_THREAD_ID = ""


def caption(status: dict) -> str:
    context = status["market_context"]
    profile = context["profile"]
    structure = context["structure"]
    return (
        f"📊 {status['symbol']} Teknik Piyasa Durumu\n"
        f"Fiyat: {status['price']:,.2f} ({status['change_pct']:+.2f}%)\n"
        f"Rejim: {context['regime']['state']}\n"
        f"Yapı: {structure['state']} — {structure['event']}\n"
        f"Konum: {profile['position']}\n"
        f"POC: {profile['poc']:,.2f} | VAH: {profile['vah']:,.2f} | VAL: {profile['val']:,.2f}\n"
        f"RVOL: {context['relative_volume']:.2f}x\n"
        f"Kaynak: {status.get('data_provider', 'bilinmiyor')}\n"
        f"Bar: {status['timestamp']}\n\n"
        "Durum raporudur; otomatik AL/SAT puanı değildir. Yatırım tavsiyesi değildir."
    )


def send(image_path: Path, json_path: Path) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", DEFAULT_CHAT_ID).strip()
    thread_id = os.getenv("TELEGRAM_MESSAGE_THREAD_ID", DEFAULT_THREAD_ID).strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN GitHub Actions Secret olarak tanımlanmalıdır.")
    status = json.loads(json_path.read_text(encoding="utf-8"))
    payload = {"chat_id": chat_id, "caption": caption(status)}
    if thread_id:
        payload["message_thread_id"] = thread_id
    with image_path.open("rb") as image:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data=payload,
            files={"photo": (image_path.name, image, "image/png")},
            timeout=60,
        )
    if not response.ok:
        raise RuntimeError(f"Telegram gönderimi başarısız: HTTP {response.status_code} — {response.text[:300]}")
    destination = f"konu {thread_id}" if thread_id else "Genel konu"
    print(f"Telegram raporu gönderildi: chat_id={chat_id}, hedef={destination}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="reports/technical_report.png")
    parser.add_argument("--json", default="reports/technical_report.json")
    args = parser.parse_args()
    send(Path(args.image), Path(args.json))


if __name__ == "__main__":
    main()
