#!/usr/bin/env python3
"""Static site server with a Telegram-backed contact form."""

from __future__ import annotations

import html
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
ADMIN_FILE = DATA_DIR / "telegram-admin.json"
LEADS_FILE = DATA_DIR / "leads.jsonl"


def load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_dotenv()
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
SETUP_KEY = os.environ.get("TELEGRAM_SETUP_KEY", "").strip()
PORT = int(os.environ.get("PORT", "8000"))
CODEX_MODEL = os.environ.get("CODEX_MODEL", "gpt-5.6-luna").strip()
CODEX_BIN = os.environ.get("CODEX_BIN", "codex").strip()
SUMMARY_TIMEZONE = os.environ.get("SUMMARY_TIMEZONE", "Europe/Moscow").strip()
CODEX_TIMEOUT_SECONDS = int(os.environ.get("CODEX_TIMEOUT_SECONDS", "120"))
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
SUMMARY_COMMAND = "/summary"


class TelegramError(RuntimeError):
    pass


def telegram_call(
    method: str, payload: dict[str, Any] | None = None, timeout: int = 15
) -> Any:
    if not BOT_TOKEN:
        raise TelegramError("TELEGRAM_BOT_TOKEN is not configured")
    request = urllib.request.Request(
        f"{TELEGRAM_API}/{method}",
        data=urllib.parse.urlencode(payload or {}).encode("utf-8"),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise TelegramError(f"Telegram request failed: {exc}") from exc
    if not result.get("ok"):
        raise TelegramError(result.get("description", "Telegram API error"))
    return result.get("result")


def load_admin_chat_id() -> str | None:
    configured = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "").strip()
    if configured:
        return configured
    try:
        return str(json.loads(ADMIN_FILE.read_text(encoding="utf-8"))["chat_id"])
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def save_admin_chat_id(chat_id: int) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    ADMIN_FILE.write_text(
        json.dumps({"chat_id": chat_id}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


admin_lock = threading.Lock()
admin_chat_id = load_admin_chat_id()


def send_message(chat_id: str | int, text: str, *, html_mode: bool = True) -> None:
    payload = {"chat_id": str(chat_id), "text": text}
    if html_mode:
        payload["parse_mode"] = "HTML"
    telegram_call("sendMessage", payload)


def register_summary_command(chat_id: str | int) -> None:
    telegram_call(
        "setMyCommands",
        {
            "commands": json.dumps(
                [{"command": "summary", "description": "Сводка за сегодня"}],
                ensure_ascii=False,
            ),
            "scope": json.dumps({"type": "chat", "chat_id": int(chat_id)}),
        },
    )


leads_lock = threading.Lock()
summary_lock = threading.Lock()


def save_lead(
    payload: dict[str, str], *, received_at: datetime | None = None
) -> dict[str, str]:
    lead = {
        "id": str(uuid.uuid4()),
        "received_at": (received_at or datetime.now(timezone.utc))
        .astimezone(timezone.utc)
        .isoformat(),
        "name": payload["name"],
        "contact": payload["contact"],
        "location": payload.get("location", ""),
        "objectType": payload.get("objectType", ""),
        "message": payload["message"],
    }
    DATA_DIR.mkdir(exist_ok=True)
    with leads_lock:
        with LEADS_FILE.open("a", encoding="utf-8") as file:
            file.write(json.dumps(lead, ensure_ascii=False) + "\n")
    return lead


def load_today_leads(
    *,
    now: datetime | None = None,
    path: Path | None = None,
    timezone_name: str | None = None,
) -> list[dict[str, str]]:
    tz = ZoneInfo(timezone_name or SUMMARY_TIMEZONE)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    target_date = current.astimezone(tz).date()
    leads_path = path or LEADS_FILE
    try:
        lines = leads_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []

    result: list[dict[str, str]] = []
    for line in lines:
        try:
            lead = json.loads(line)
            received_at = datetime.fromisoformat(lead["received_at"])
            if received_at.tzinfo is None:
                received_at = received_at.replace(tzinfo=timezone.utc)
            if received_at.astimezone(tz).date() == target_date:
                result.append(lead)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return result


def build_summary_prompt(leads: list[dict[str, str]]) -> str:
    return (
        "Составь краткую управленческую сводку по заявкам архитектурной студии за сегодня. "
        "Ответь на русском языке обычным текстом, максимум 1200 символов. Укажи точное количество "
        "заявок, клиентов и их контакты, основные запросы, географию, типы объектов, признаки "
        "срочности и рекомендуемые следующие действия. Не придумывай факты и не используй внешние "
        "источники. Если данных для вывода нет, прямо скажи об этом.\n\n"
        "Заявки JSON:\n" + json.dumps(leads, ensure_ascii=False, indent=2)
    )


def run_codex_summary(leads: list[dict[str, str]]) -> str:
    command = [
        CODEX_BIN,
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--color",
        "never",
        "--model",
        CODEX_MODEL,
        "-C",
        str(ROOT),
        "-",
    ]
    completed = subprocess.run(
        command,
        input=build_summary_prompt(leads),
        text=True,
        capture_output=True,
        timeout=CODEX_TIMEOUT_SECONDS,
        check=False,
    )
    summary = completed.stdout.strip()
    if completed.returncode != 0 or not summary:
        raise RuntimeError("codex exec failed")
    return summary[:3900]


def generate_and_send_summary(chat_id: str | int) -> None:
    try:
        leads = load_today_leads()
        if not leads:
            send_message(chat_id, "Сегодня новых заявок пока нет.", html_mode=False)
            return
        summary = run_codex_summary(leads)
        send_message(chat_id, f"📊 Сводка за сегодня\n\n{summary}", html_mode=False)
    except subprocess.TimeoutExpired:
        send_message(
            chat_id,
            "Не удалось подготовить сводку вовремя. Попробуйте ещё раз.",
            html_mode=False,
        )
    except (OSError, RuntimeError, TelegramError) as exc:
        print(f"Summary generation error: {exc}", flush=True)
        try:
            send_message(
                chat_id,
                "Не удалось подготовить ИИ-сводку. Попробуйте позже.",
                html_mode=False,
            )
        except TelegramError:
            pass
    finally:
        summary_lock.release()


def request_summary(chat_id: str | int) -> None:
    if not summary_lock.acquire(blocking=False):
        send_message(
            chat_id, "Сводка уже готовится. Подождите немного.", html_mode=False
        )
        return
    try:
        send_message(chat_id, "Готовлю сводку за сегодня…", html_mode=False)
        threading.Thread(
            target=generate_and_send_summary, args=(chat_id,), daemon=True
        ).start()
    except Exception:
        summary_lock.release()
        raise


def bot_polling_loop(stop_event: threading.Event) -> None:
    global admin_chat_id
    offset = 0
    while not stop_event.is_set():
        try:
            updates = telegram_call(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 25,
                    "allowed_updates": json.dumps(["message"]),
                },
                timeout=35,
            )
            for update in updates:
                offset = max(offset, int(update["update_id"]) + 1)
                message = update.get("message") or {}
                chat_id = (message.get("chat") or {}).get("id")
                text = (message.get("text") or "").strip()
                if not chat_id:
                    continue
                if text.startswith("/start"):
                    start_payload = text.partition(" ")[2].strip()
                    should_register_commands = False
                    with admin_lock:
                        if admin_chat_id is None:
                            if not SETUP_KEY or start_payload != SETUP_KEY:
                                reply = "🔐 Для подключения используйте персональную ссылку настройки."
                            else:
                                admin_chat_id = str(chat_id)
                                save_admin_chat_id(chat_id)
                                should_register_commands = True
                                reply = "✅ <b>Бот подключён</b>\n\nНовые заявки с сайта будут приходить в этот чат."
                        elif admin_chat_id == str(chat_id):
                            should_register_commands = True
                            reply = "✅ Этот чат уже получает заявки с сайта."
                        else:
                            reply = "⛔ Получатель заявок уже настроен."
                    if should_register_commands:
                        register_summary_command(chat_id)
                    send_message(chat_id, reply)
                    continue

                command = text.split("@", 1)[0].split(" ", 1)[0]
                if command == SUMMARY_COMMAND:
                    with admin_lock:
                        is_admin = admin_chat_id == str(chat_id)
                    if is_admin:
                        request_summary(chat_id)
                    else:
                        send_message(
                            chat_id,
                            "⛔ Команда доступна только владельцу бота.",
                            html_mode=False,
                        )
        except TelegramError as exc:
            print(f"Telegram polling error: {exc}", flush=True)
            stop_event.wait(5)


def clean_text(value: Any, max_length: int) -> str:
    return (
        " ".join(value.strip().split())[:max_length] if isinstance(value, str) else ""
    )


def format_lead(payload: dict[str, str]) -> str:
    fields = [
        ("Имя", payload["name"]),
        ("Контакт", payload["contact"]),
        ("Объект", payload.get("objectType") or "Не указан"),
        ("Локация", payload.get("location") or "Не указана"),
        ("О проекте", payload["message"]),
    ]
    return "\n".join(
        ["🏡 <b>Новая заявка с сайта</b>", ""]
        + [f"<b>{label}:</b> {html.escape(value)}" for label, value in fields]
    )


request_log: dict[str, deque[float]] = defaultdict(deque)
rate_lock = threading.Lock()


def is_rate_limited(ip: str) -> bool:
    now = time.monotonic()
    with rate_lock:
        entries = request_log[ip]
        while entries and now - entries[0] > 60:
            entries.popleft()
        if len(entries) >= 5:
            return True
        entries.append(now)
        return False


class SiteHandler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/api/contact":
            self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
            return
        if is_rate_limited(self.client_address[0]):
            self.send_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {
                    "ok": False,
                    "error": "Слишком много попыток. Попробуйте через минуту.",
                },
            )
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 16_384:
            self.send_json(
                HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Некорректный запрос."}
            )
            return
        try:
            raw = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(
                HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Некорректный JSON."}
            )
            return

        payload = {
            "name": clean_text(raw.get("name"), 100),
            "contact": clean_text(raw.get("contact"), 150),
            "location": clean_text(raw.get("location"), 150),
            "objectType": clean_text(raw.get("objectType"), 100),
            "message": clean_text(raw.get("message"), 1500),
        }
        if raw.get("website"):
            self.send_json(HTTPStatus.OK, {"ok": True})
            return
        if (
            len(payload["name"]) < 2
            or len(payload["contact"]) < 5
            or len(payload["message"]) < 12
        ):
            self.send_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"ok": False, "error": "Проверьте поля формы."},
            )
            return
        with admin_lock:
            recipient = admin_chat_id
        if not recipient:
            self.send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"ok": False, "error": "Бот ещё не настроен. Напишите нам по email."},
            )
            return
        try:
            send_message(recipient, format_lead(payload))
        except TelegramError as exc:
            print(f"Lead delivery error: {exc}", flush=True)
            self.send_json(
                HTTPStatus.BAD_GATEWAY,
                {
                    "ok": False,
                    "error": "Не удалось отправить заявку. Попробуйте ещё раз.",
                },
            )
            return
        try:
            save_lead(payload)
        except OSError as exc:
            print(f"Lead persistence error: {exc}", flush=True)
        self.send_json(HTTPStatus.OK, {"ok": True})

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        super().end_headers()


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Add TELEGRAM_BOT_TOKEN to .env before starting the server")
    bot = telegram_call("getMe")
    print(f"Telegram bot: @{bot['username']}", flush=True)
    if admin_chat_id:
        print("Telegram recipient: configured", flush=True)
        register_summary_command(admin_chat_id)
    else:
        print(
            f"Telegram recipient: open https://t.me/{bot['username']}?start={SETUP_KEY}",
            flush=True,
        )
    stop_event = threading.Event()
    threading.Thread(target=bot_polling_loop, args=(stop_event,), daemon=True).start()
    server = ThreadingHTTPServer(("", PORT), SiteHandler)
    try:
        print(f"Site: http://localhost:{PORT}", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        server.server_close()


if __name__ == "__main__":
    main()
