"""Run the Telegram bot by long polling, for local development.

    python -m app.channels.telegram_poll

Polling and a webhook cannot run at the same time. This runner never sets or deletes a webhook;
if one is registered, Telegram refuses getUpdates and the runner says so.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from functools import partial

from app.channels.dispatch import Dispatcher
from app.channels.telegram import (
    POLL_TIMEOUT,
    TelegramAPI,
    TelegramError,
    TelegramSender,
    parse_update,
    register_commands,
)
from app.config import settings
from app.intake import Intake
from app.jobs import start_purger
from app.store import Store

log = logging.getLogger("trinetra.telegram_poll")


def run_once(
    api: TelegramAPI, intake: Intake, offset: int | None, timeout: int = POLL_TIMEOUT,
    dispatcher: Dispatcher | None = None,
) -> int | None:
    """Fetch one batch of updates, hand each to the dispatcher (or handle it here), and return the next offset."""
    for update in api.get_updates(offset, timeout=timeout):
        offset = update["update_id"] + 1
        msg = parse_update(update, api)
        if msg is None:
            continue
        sender = TelegramSender(api, msg)
        if sender.acks_on_arrival():
            sender.ack()  # at once, before the press waits behind this chat's other work or runs a check
        job = partial(_handle, intake, msg, sender, update["update_id"])
        if dispatcher is None:
            job()
        else:
            dispatcher.submit(msg.chat_id, job)
    return offset


def _handle(intake: Intake, msg, send, update_id: int) -> None:
    try:
        intake.handle(msg, send)
    except Exception:
        log.exception("failed to handle update %s", update_id)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not settings.telegram_bot_token:
        sys.exit("TELEGRAM_BOT_TOKEN is not set. Load .env first.")
    if not settings.user_id_salt:
        sys.exit("USER_ID_SALT is not set. Load .env first.")

    api = TelegramAPI(settings.telegram_bot_token)
    store = Store(settings.data_dir / "trinetra.db")
    intake = Intake(store, settings.user_id_salt, data_dir=settings.data_dir, media_ttl_hours=settings.media_ttl_hours)
    stop = threading.Event()
    start_purger(store, intake.media, stop)
    try:
        register_commands(api)
    except TelegramError as e:
        log.warning("could not register the command menu: %s", e)
    dispatcher = Dispatcher()
    log.info("polling Telegram with %d workers; press Ctrl+C to stop", dispatcher.workers)
    offset = None
    try:
        while True:
            try:
                offset = run_once(api, intake, offset, dispatcher=dispatcher)
            except TelegramError as e:
                if e.status == 409:
                    log.error("Telegram refused polling because a webhook is registered. Remove it before polling.")
                else:
                    log.warning("%s; retrying in 5 s", e)
                time.sleep(5)
    except KeyboardInterrupt:
        stop.set()
        dispatcher.shutdown()
        log.info("stopped")


if __name__ == "__main__":
    main()
