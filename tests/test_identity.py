from datetime import UTC, datetime

from ambient_ai.identity import lookup_sender, set_token, upsert_sender
from ambient_ai.identity.magic_link import sign, verify

PHONE = "+15551234567"


def test_unknown_sender_lookup_returns_none():
    assert lookup_sender(PHONE) is None


def test_upsert_then_lookup_returns_profile_keyed_by_number():
    profile = upsert_sender(PHONE)
    assert profile.phone == PHONE
    assert profile.verified_at is None
    assert profile.github_token is None
    assert profile.created_at is not None
    assert lookup_sender(PHONE) == profile


def test_upsert_marks_verified_and_keeps_created_at():
    first = upsert_sender(PHONE)
    now = datetime.now(UTC)
    second = upsert_sender(PHONE, verified_at=now)
    assert second.created_at == first.created_at
    assert second.verified_at == now


def test_set_token_stores_it_and_lookup_returns_it():
    upsert_sender(PHONE)
    set_token(PHONE, "ghp_testtoken")
    assert lookup_sender(PHONE).github_token == "ghp_testtoken"


def test_magic_link_token_round_trips():
    token = sign(PHONE)
    assert verify(token) == PHONE


def test_tampered_or_expired_token_does_not_verify():
    token = sign(PHONE)
    assert verify(token[:-1] + ("a" if token[-1] != "a" else "b")) is None
    assert verify(sign(PHONE, now=datetime.now(UTC).timestamp() - 16 * 60)) is None


def test_token_does_not_carry_the_number_in_the_clear():
    import base64

    token = sign(PHONE)
    encoded = token.split(".", 1)[0]
    decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    assert b"5551234567" not in decoded
    assert PHONE.encode() not in decoded


# --- the Telegram user id maps onto the number (ADR-003) ----------------------------------

TG_ID = 123456789
PROVISIONAL = f"tg:{TG_ID}"


def test_an_unmapped_telegram_user_resolves_to_a_provisional_key():
    from ambient_ai.identity.senders import resolve_telegram_sender

    assert resolve_telegram_sender(TG_ID) == PROVISIONAL


def test_adopt_contact_moves_token_and_verified_at_onto_the_phone_record():
    from ambient_ai.identity.senders import adopt_telegram_contact, resolve_telegram_sender

    now = datetime.now(UTC)
    upsert_sender(PROVISIONAL, verified_at=now)
    set_token(PROVISIONAL, "ghp_provisional")
    profile, merged = adopt_telegram_contact(TG_ID, PHONE)
    assert merged == PROVISIONAL
    assert profile.phone == PHONE
    assert profile.github_token == "ghp_provisional"
    assert profile.verified_at == now
    assert profile.telegram_user_id == str(TG_ID)
    assert lookup_sender(PROVISIONAL) is None
    assert resolve_telegram_sender(TG_ID) == PHONE


def test_adopt_contact_without_a_provisional_record_just_maps_the_id():
    from ambient_ai.identity.senders import adopt_telegram_contact

    profile, merged = adopt_telegram_contact(TG_ID, PHONE)
    assert merged is None
    assert profile.phone == PHONE and profile.telegram_user_id == str(TG_ID)


def test_adopt_contact_keeps_a_token_already_on_the_phone_record():
    from ambient_ai.identity.senders import adopt_telegram_contact

    upsert_sender(PHONE)
    set_token(PHONE, "ghp_realtoken")
    upsert_sender(PROVISIONAL)
    set_token(PROVISIONAL, "ghp_provisional")
    profile, _ = adopt_telegram_contact(TG_ID, PHONE)
    assert profile.github_token == "ghp_realtoken"


def test_a_telegram_id_maps_to_exactly_one_number():
    from ambient_ai.identity.senders import adopt_telegram_contact, lookup_by_telegram_id

    adopt_telegram_contact(TG_ID, PHONE)
    adopt_telegram_contact(TG_ID, "+15559998888")
    assert lookup_by_telegram_id(TG_ID).phone == "+15559998888"
    assert lookup_sender(PHONE).telegram_user_id is None


def test_a_store_created_before_the_column_opens_and_keeps_its_records(monkeypatch, tmp_path):
    """The live store predates telegram_user_id. Writing the column as `ADD COLUMN ... UNIQUE`
    passes on a fresh file and raises `sqlite3.OperationalError: no such column:
    telegram_user_id` on that one, because SQLite refuses a UNIQUE column on ALTER TABLE.
    """
    import sqlite3

    from ambient_ai.identity.senders import adopt_telegram_contact, resolve_telegram_sender

    old = tmp_path / "pre-column.db"
    conn = sqlite3.connect(old)
    conn.execute(
        "CREATE TABLE senders (phone TEXT PRIMARY KEY, verified_at TEXT, "
        "github_token TEXT, created_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO senders VALUES (?, ?, ?, ?)",
        (PROVISIONAL, datetime.now(UTC).isoformat(), "ghp_live", datetime.now(UTC).isoformat()),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("DB_PATH", str(old))

    assert resolve_telegram_sender(TG_ID) == PROVISIONAL
    profile, merged = adopt_telegram_contact(TG_ID, PHONE)
    assert merged == PROVISIONAL and profile.github_token == "ghp_live"
