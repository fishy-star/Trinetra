"""Create the two demo issuers and one portal account for each.

    python tools/seed_demo_issuers.py            # create; keeps existing passwords
    python tools/seed_demo_issuers.py --reset    # also set new passwords

Prints each new password once. It is not stored anywhere except as a salted hash.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.issuers import hash_password  # noqa: E402
from app.store import Store  # noqa: E402

DEMO_ISSUERS = [
    {
        "name": "Pune District Court (demo)",
        "type": "court",
        "domains": ["pune.dcourts.gov.in"],
        "contacts": ["Office of the Civil Judge (Senior Division), Pune District Court (demo)"],
        "payees": [],
        "user": "court-demo",
    },
    {
        "name": "Navdeep Bank (demo)",
        "type": "bank",
        "domains": ["navdeepbank.bank.in"],
        "contacts": ["1800-000-0000", "navdeepbank.bank.in"],
        "payees": ["loanrepay.navdeepbank@nvdp"],
        "user": "bank-demo",
    },
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="set new passwords for existing demo accounts")
    args = parser.parse_args()

    store = Store(settings.data_dir / "trinetra.db")
    for d in DEMO_ISSUERS:
        # Demo issuers match only their full name, "(demo)" included, so the synthetic test set's
        # fictional "Navdeep Bank" is never mistaken for them.
        issuer_id = store.upsert_issuer(
            d["name"], d["type"], d["domains"], d["contacts"], d["payees"], [d["name"]], requires_code=True
        )
        if store.issuer_user(d["user"]) and not args.reset:
            print(f"{d['name']}: account {d['user']} exists; password unchanged (use --reset for a new one)")
            continue
        password = secrets.token_urlsafe(12)
        store.set_issuer_user(d["user"], issuer_id, hash_password(password))
        store.audit("system", "issuer_account_set", f"user={d['user']} issuer={issuer_id}")
        print(f"{d['name']}: username {d['user']}  password {password}")


if __name__ == "__main__":
    main()
