"""Run the Telegram bot by long polling, for local development.

    python -m app.channels.telegram_poll

Polling and a webhook cannot run at the same time. This runner never sets or deletes a webhook;
if one is registered, Telegram refuses getUpdates and the runner says so.
"""

from __future__ import annotations

import logging
import sys
import time
from functools import partial

from app.channels.telegram import TelegramAPI, TelegramError, parse_update
from app.config import settings
from app.intake import Intake
from app.store import Store

log = logging.getLogger("trinetra.telegram_poll")


def run_once(api: TelegramAPI, intake: Intake, offset: int | None, timeout: int = 30) -> int | None:
    """Fetch one batch of updates, handle each, and return the next offset."""
    for update in api.get_updates(offset, timeout=timeout):
        offset = update["update_id"] + 1
        msg = parse_update(update)
        if msg is None:
            continue
        try:
            intake.handle(msg, partial(api.send_message, msg.chat_id))
        except Exception:
            log.exception("failed to handle update %s", update["update_id"])
    return offset


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not settings.telegram_bot_token:
        sys.exit("TELEGRAM_BOT_TOKEN is not set. Load .env first.")
    if not settings.user_id_salt:
        sys.exit("USER_ID_SALT is not set. Load .env first.")

    api = TelegramAPI(settings.telegram_bot_token)
    intake = Intake(Store(settings.data_dir / "trinetra.db"), settings.user_id_salt)
    log.info("polling Telegram; press Ctrl+C to stop")
    offset = None
    try:
        while True:
            try:
                offset = run_once(api, intake, offset)
            except TelegramError as e:
                if e.status == 409:
                    log.error("Telegram refused polling because a webhook is registered. Remove it before polling.")
                else:
                    log.warning("%s; retrying in 5 s", e)
                time.sleep(5)
    except KeyboardInterrupt:
        log.info("stopped")


if __name__ == "__main__":
    main()
