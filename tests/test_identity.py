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
