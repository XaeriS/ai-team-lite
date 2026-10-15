"""Approve from the phone (L17): a private Telegram bot sends the cards that wait for the user, with buttons.

- Only outgoing connections: the app asks Telegram for new button presses (long polling). No port is opened on the PC.
- Only the owner's own chat is accepted. It is linked once: the user sends a one-time code to the bot.
- The bot key is kept in Windows Credential Manager (secrets_store "telegram_bot_token"), never shown to the AI.
- Messages never carry secrets or file contents: the title, Fred's short explanation, the risk and the buttons.
- High-risk approvals (installing software, outside the project folder) can be kept for the PC only (setting).
"""
from __future__ import annotations

import json
import logging
import secrets as _secrets
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from . import projects, secrets_store, settings

API = "https://api.telegram.org/bot{token}/{method}"
LINK_SECONDS = 600        # a linking code is valid for 10 minutes
POLL_SECONDS = 25         # long-poll wait at Telegram
TOKEN_NAME = "telegram_bot_token"
log = logging.getLogger("telegram")


class TelegramError(Exception):
    pass


def call(token: str, method: str, params: dict | None = None, timeout: float = 15) -> Any:
    """One Bot API call. Raises TelegramError with a plain reason."""
    req = urllib.request.Request(API.format(token=token, method=method), data=json.dumps(params or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 404):
            raise TelegramError("Telegram refused this bot key. Copy the whole key from @BotFather again.") from e
        raise TelegramError(f"Telegram answered with error {e.code}.") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TelegramError(f"Could not reach Telegram ({getattr(e, 'reason', e)}).") from e
    if not data.get("ok"):
        raise TelegramError(str(data.get("description") or "Telegram refused the request."))
    return data.get("result")


def _short(text: Any, limit: int) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


# ---------------------------------------------------------------------------- what a card looks like on the phone
RISK_WORD = {"low": "🟢 low risk", "medium": "🟠 medium risk", "high": "🔴 high risk"}


def card_message(kind: str, payload: dict, project: str, high_risk_on_phone: bool) -> tuple[str, list[list[dict]]]:
    """(text, buttons) for a card waiting for the user. Buttons carry a short code (b0, b1...) per choice."""
    title = _short(payload.get("title") or {"plan": "Plan of action", "architecture": "Architecture"}.get(kind, ""), 120)
    head = f"⏳ {project}: Fred is waiting for you\n\n"
    if kind == "approval":
        who = payload.get("who") or "Fred"
        lines = [f"🔐 {who} wants to: {title}"]
        if payload.get("what"):
            lines.append(_short(payload["what"], 300))
        elif payload.get("explanation"):
            lines.append(_short(payload["explanation"], 300))
        if payload.get("risk"):
            lines.append(RISK_WORD.get(payload["risk"], payload["risk"]) +
                         (f": {_short(payload.get('risk_reason'), 200)}" if payload.get("risk_reason") else ""))
        if payload.get("risk") == "high" and not high_risk_on_phone:
            return head + "\n".join(lines) + "\n\nThis one needs you at the PC (high risk).", []
        return head + "\n".join(lines), [[{"text": "✅ Approve", "data": "b0"}, {"text": "✋ Decline", "data": "b1"}]]
    if kind in ("plan", "architecture"):
        what = "the plan" if kind == "plan" else "Mat's architecture"
        return (head + f"🗺 Approve {what}? {title}\n\nTo ask for changes, answer at the PC.",
                [[{"text": "✅ Approve", "data": "b0"}]])
    if kind == "change":
        return (head + f"🔁 Change request: {title}\n{_short(payload.get('description'), 300)}",
                [[{"text": "✅ Approve", "data": "b0"}, {"text": "✋ Reject", "data": "b1"}]])
    if kind == "choice":
        opts = [str(o) for o in (payload.get("options") or [])][:5]
        return (head + f"❓ {title}" + ("" if opts else "\n\nAnswer at the PC."),
                [[{"text": _short(o, 40), "data": f"b{i}"}] for i, o in enumerate(opts)])
    return head + f"{title or 'A question'}\n\nThis one needs you at the PC.", []


def answer_payload(kind: str, payload: dict, button: str) -> dict | None:
    """What a button press means for the card, in the same shape as an answer from the screen."""
    i = int(button[1:]) if button[:1] == "b" and button[1:].isdigit() else -1
    if kind == "approval" and i in (0, 1):
        return {"approved": i == 0, "note": "", "via": "phone"}
    if kind == "plan" and i == 0:
        return {"approved": True, "after": {}, "via": "phone"}
    if kind == "architecture" and i == 0:
        return {"approved": True, "via": "phone"}
    if kind == "change" and i in (0, 1):
        return {"approved": i == 0, "note": "", "via": "phone"}
    opts = [str(o) for o in (payload.get("options") or [])][:5]
    if kind == "choice" and 0 <= i < len(opts):
        return {"value": opts[i], "via": "phone"}
    return None


# ---------------------------------------------------------------------------- the bridge
class Bridge:
    """Runs in its own thread while a bot is linked. `engine` is the app's Engine; `api` can be replaced in tests."""

    def __init__(self, engine, api: Callable[..., Any] = call):
        self.engine = engine
        self.api = api
        self.cards: dict[str, dict] = {}     # item id -> {slug, kind, payload, message_id}
        self.alerts: dict[str, int] = {}     # stuck card id -> message id
        self.link: dict | None = None        # {"code", "until"} while the user links their phone
        self.offset = 0
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        engine.listeners.append(self.on_engine)

    # ------------------------------------------------------------------ state
    @staticmethod
    def token() -> str:
        return secrets_store.get_secret(TOKEN_NAME) or ""

    @staticmethod
    def chat_id() -> int | None:
        return settings.load().get("telegram_chat_id") or None

    def connected(self) -> bool:
        return bool(self.token() and self.chat_id())

    def status(self) -> dict:
        s = settings.load()
        return {"connected": self.connected(), "bot": s.get("telegram_bot", ""), "key_saved": bool(self.token()),
                "high_risk_on_phone": bool(s.get("telegram_high_risk_on_phone")),
                "linking": bool(self.link and self.link["until"] > time.time())}

    def start(self) -> None:
        if self.thread is None or not self.thread.is_alive():
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._run, name="telegram", daemon=True)
            self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def send(self, text: str, buttons: list[list[dict]] | None = None, prefix: str = "") -> int | None:
        """Send to the linked chat. Button data is prefixed with what it answers (e.g. a card id). Never raises."""
        chat, token = self.chat_id(), self.token()
        if not chat or not token:
            return None
        params = {"chat_id": chat, "text": text[:3900], "disable_web_page_preview": True}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": [[{"text": b["text"], "callback_data": f"{prefix}:{b['data']}"}
                                                           for b in row] for row in buttons]}
        try:
            return (self.api(token, "sendMessage", params) or {}).get("message_id")
        except TelegramError as e:
            log.warning("send failed: %s", e)
            return None

    def _edit(self, message_id: int | None, text: str) -> None:
        if message_id and self.connected():
            try:
                self.api(self.token(), "editMessageText", {"chat_id": self.chat_id(), "message_id": message_id,
                                                           "text": text[:3900]})
            except TelegramError as e:
                log.info("edit failed: %s", e)

    # ------------------------------------------------------------------ linking the phone (setup)
    def check_key(self) -> dict:
        """Ask Telegram who the bot is (also proves the key works). Returns {username, name}."""
        me = self.api(self.token(), "getMe", {})
        settings.save({"telegram_bot": me.get("username", "")})
        return {"username": me.get("username", ""), "name": me.get("first_name", "")}

    def begin_link(self) -> dict:
        me = self.check_key()
        code = f"{_secrets.randbelow(900000) + 100000}"
        with self.lock:
            self.link = {"code": code, "until": time.time() + LINK_SECONDS}
        self.start()
        return {"code": code, "bot": me["username"], "url": f"https://t.me/{me['username']}"}

    def disconnect(self) -> None:
        settings.save({"telegram_chat_id": None, "telegram_bot": ""})
        secrets_store.delete_secret(TOKEN_NAME)
        with self.lock:
            self.link = None
            self.cards.clear()
        self.stop()

    # ------------------------------------------------------------------ engine events -> phone
    def on_engine(self, event: str, **kw) -> None:
        if not self.connected():
            return
        threading.Thread(target=self._on_engine, args=(event, kw), daemon=True).start()

    def _project(self, slug: str) -> str:
        return (projects.load(slug) or {}).get("name") or ("Setup" if slug == projects.SETUP_SLUG else slug)

    def _on_engine(self, event: str, kw: dict) -> None:
        slug = kw.get("slug", "")
        if event == "card":
            text, buttons = card_message(kw["kind"], kw["payload"], self._project(slug),
                                         bool(settings.load().get("telegram_high_risk_on_phone")))
            mid = self.send(text, buttons, prefix=f"c:{kw['item_id']}")
            with self.lock:
                self.cards[kw["item_id"]] = {"slug": slug, "kind": kw["kind"], "payload": kw["payload"],
                                             "message_id": mid, "text": text}
        elif event == "answered":
            with self.lock:
                card = self.cards.pop(kw["item_id"], None)
            if card and kw.get("via") != "phone":
                self._edit(card["message_id"], card["text"] + "\n\n✔ Answered at the PC.")
        elif event == "stuck":
            mid = self.send(f"⏱ {self._project(slug)}: {kw['text']}",
                            [[{"text": "⏰ Wake Fred up", "data": "wake"}, {"text": "↻ Restart this step", "data": "restart"}]],
                            prefix=f"s:{slug}")
            if mid:
                self.alerts[kw.get("card_id", "")] = mid
        elif event == "limit":
            self.send(f"⏸ {self._project(slug)}: {kw['text']}")
        elif event == "resumed":
            self.send(f"▶ {self._project(slug)}: {kw['text']}")

    # ------------------------------------------------------------------ phone -> app
    def _run(self) -> None:
        backoff = 5
        while not self.stop_event.is_set():
            token = self.token()
            linking = self.link and self.link["until"] > time.time()
            if not token or not (self.chat_id() or linking):
                if self.stop_event.wait(5):
                    return
                continue
            try:
                updates = self.api(token, "getUpdates", {"offset": self.offset, "timeout": POLL_SECONDS,
                                                         "allowed_updates": ["message", "callback_query"]},
                                   timeout=POLL_SECONDS + 10) or []
                backoff = 5
            except TelegramError as e:
                log.warning("polling failed: %s", e)
                if self.stop_event.wait(backoff):
                    return
                backoff = min(backoff * 2, 120)
                continue
            for u in updates:
                self.offset = max(self.offset, int(u.get("update_id", 0)) + 1)
                try:
                    self.handle_update(u)
                except Exception:  # noqa: BLE001  (one odd update must not stop the bridge)
                    log.exception("update failed")

    def handle_update(self, u: dict) -> None:
        msg = u.get("message")
        if msg:
            self._message(msg)
            return
        cq = u.get("callback_query")
        if cq:
            self._button(cq)

    def _message(self, msg: dict) -> None:
        chat = (msg.get("chat") or {})
        text = str(msg.get("text") or "").strip()
        with self.lock:
            link = self.link
        if link and link["until"] > time.time() and chat.get("type") == "private" and link["code"] in text.split():
            settings.save({"telegram_chat_id": chat["id"]})
            with self.lock:
                self.link = None
            self.send("✅ Your phone is linked to AI Team Lite. When Fred waits for your approval, you get a message "
                      "here with buttons.")
            self.engine.hub.publish(projects.SETUP_SLUG, {"type": "telegram", "state": "linked", "transient": True})
            return
        if chat.get("id") == self.chat_id():
            self.send("I only send approvals and alerts. To talk to Fred, use AI Team Lite on your PC.")

    def _button(self, cq: dict) -> None:
        token, owner = self.token(), self.chat_id()
        chat = ((cq.get("message") or {}).get("chat") or {}).get("id")
        if not owner or chat != owner or (cq.get("from") or {}).get("id") != owner:
            return   # not the owner's chat: ignored, never answered
        data = str(cq.get("data") or "")
        reply = "Done."
        kind, _, rest = data.partition(":")
        if kind == "c":
            item_id, _, button = rest.partition(":")
            with self.lock:
                card = self.cards.get(item_id)
            pay = answer_payload(card["kind"], card["payload"], button) if card else None
            if not card or not pay:
                reply = "This request is no longer waiting."
            elif not self.engine.respond(item_id, pay):
                reply = "This request was already answered."
            else:
                with self.lock:
                    self.cards.pop(item_id, None)
                word = ("approved" if pay.get("approved") else "declined") if "approved" in pay else f"answered: {pay.get('value')}"
                self._edit(card["message_id"], card["text"] + f"\n\n✔ You {word} from your phone.")
                reply = "Sent to Fred."
        elif kind == "s":
            slug, _, action = rest.partition(":")
            if projects.load(slug):
                if action == "wake":
                    self.engine.wake(slug)
                    reply = "Fred was woken up."
                elif action == "restart":
                    self.engine.restart_step(slug)
                    reply = "The step is starting again."
        try:
            self.api(token, "answerCallbackQuery", {"callback_query_id": cq.get("id"), "text": reply})
        except TelegramError:
            pass
