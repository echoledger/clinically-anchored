"""Link tokens are scoped: a check-in link can't be used as a message link
(or vice versa), and message links expire sooner."""

import pytest
from fastapi.testclient import TestClient

from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.core.security import (
    InvalidCheckinToken,
    create_link_token,
    verify_link_token,
)
from clinically_anchored_api.main import app

IDS = {"clinic_id": "c", "patient_id": "p"}


def test_token_round_trips_in_its_own_scope():
    for scope in ("checkin", "messages"):
        assert verify_link_token(create_link_token(**IDS, scope=scope), scope) == IDS


def test_token_is_rejected_in_the_other_scope():
    with pytest.raises(InvalidCheckinToken):
        verify_link_token(create_link_token(**IDS, scope="checkin"), "messages")
    with pytest.raises(InvalidCheckinToken):
        verify_link_token(create_link_token(**IDS, scope="messages"), "checkin")


def test_message_links_have_their_own_shorter_expiry(monkeypatch):
    settings = get_settings()
    assert settings.message_link_max_age_seconds < settings.checkin_link_max_age_seconds
    monkeypatch.setattr(settings, "message_link_max_age_seconds", -1)  # expire immediately
    msg = create_link_token(**IDS, scope="messages")
    checkin = create_link_token(**IDS, scope="checkin")
    with pytest.raises(InvalidCheckinToken, match="expired"):
        verify_link_token(msg, "messages")
    assert verify_link_token(checkin, "checkin") == IDS  # unaffected


def test_messages_token_cannot_submit_or_read_check_in_context():
    client = TestClient(app)
    tok = create_link_token(**IDS, scope="messages")
    assert client.get("/check-ins/context", params={"token": tok}).status_code == 401
    assert client.post("/check-ins", params={"token": tok}, json={"answers": {}}).status_code == 401
