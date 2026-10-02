"""core/ai.py: Bedrock Converse wrapper, per-clinic daily cap, prompt templates.
The boto3 client is replaced entirely -- no AWS calls, no credentials needed."""

from datetime import date

import pytest
from botocore.exceptions import ClientError, NoCredentialsError

from clinically_anchored_api.core import ai
from clinically_anchored_api.core.config import get_settings

CLINIC = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
OTHER = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


class _FakeBedrock:
    def __init__(self):
        self.calls: list[dict] = []
        self.response = {
            "output": {"message": {"role": "assistant", "content": [{"text": "Hello"}]}},
            "usage": {"inputTokens": 12, "outputTokens": 34, "totalTokens": 46},
            "stopReason": "end_turn",
        }
        self.error: Exception | None = None

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


@pytest.fixture
def bedrock(monkeypatch):
    fake = _FakeBedrock()
    made: list[dict] = []

    def fake_client(service, **kwargs):
        made.append({"service": service, **kwargs})
        return fake

    monkeypatch.setattr(ai.boto3, "client", fake_client)
    ai._client.cache_clear()
    fake.clients_made = made
    yield fake
    ai._client.cache_clear()


@pytest.fixture(autouse=True)
def _fresh_ai_state():
    ai.reset()
    yield
    ai.reset()


def _call(clinic=CLINIC, version="draft_reply_v1", **kw):
    return ai.generate(clinic_id=clinic, prompt_version=version, system="sys", prompt="hi", **kw)


# --- model, region, tagging, usage ---------------------------------------------


def test_uses_bedrock_runtime_in_ca_central_1_whatever_aws_region_says(bedrock, monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    _call()
    assert len(bedrock.clients_made) == 1
    made = bedrock.clients_made[0]
    assert made["service"] == "bedrock-runtime"
    assert made["region_name"] == "ca-central-1"


def test_client_is_created_once(bedrock):
    _call()
    _call()
    assert len(bedrock.clients_made) == 1


def test_default_model_id_and_override(bedrock, monkeypatch):
    assert get_settings().bedrock_model_id == "anthropic.claude-haiku-4-5-20251001-v1:0"
    result = _call()
    assert bedrock.calls[0]["modelId"] == "anthropic.claude-haiku-4-5-20251001-v1:0"
    assert result.model_id == "anthropic.claude-haiku-4-5-20251001-v1:0"

    monkeypatch.setattr(get_settings(), "bedrock_model_id", "other.model-v1:0")
    result = _call()
    assert bedrock.calls[1]["modelId"] == "other.model-v1:0"
    assert result.model_id == "other.model-v1:0"


def test_env_vars_configure_model_and_cap(monkeypatch):
    from clinically_anchored_api.core.config import Settings

    monkeypatch.setenv("BEDROCK_MODEL_ID", "x.model")
    monkeypatch.setenv("AI_DAILY_CALL_CAP_PER_CLINIC", "7")
    s = Settings(_env_file=None)
    assert (s.bedrock_model_id, s.ai_daily_call_cap_per_clinic) == ("x.model", 7)
    monkeypatch.delenv("AI_DAILY_CALL_CAP_PER_CLINIC")
    assert Settings(_env_file=None).ai_daily_call_cap_per_clinic == 200


def test_prompt_version_is_sent_to_bedrock_and_returned_with_model_id(bedrock):
    result = _call(version="summary_v3")
    assert bedrock.calls[0]["requestMetadata"] == {
        "prompt_version": "summary_v3",
        "model_id": "anthropic.claude-haiku-4-5-20251001-v1:0",
    }
    assert result.prompt_version == "summary_v3"
    assert result.model_id == bedrock.calls[0]["modelId"]


def test_request_shape(bedrock):
    _call(max_tokens=256, temperature=0.0)
    call = bedrock.calls[0]
    assert call["system"] == [{"text": "sys"}]
    assert call["messages"] == [{"role": "user", "content": [{"text": "hi"}]}]
    assert call["inferenceConfig"] == {"maxTokens": 256, "temperature": 0.0}


def test_token_usage_and_text_parsed(bedrock):
    bedrock.response["output"]["message"]["content"] = [{"text": "Hello"}, {"text": " there"}]
    result = _call()
    assert result.usage == ai.TokenUsage(input_tokens=12, output_tokens=34)
    assert result.text == "Hello there"
    assert result.stop_reason == "end_turn"


@pytest.mark.parametrize("bad", ["", "has space", "semi;colon", "x" * 129])
def test_invalid_prompt_version_is_rejected_before_anything_happens(bedrock, bad):
    with pytest.raises(ValueError):
        _call(version=bad)
    assert bedrock.calls == [] and bedrock.clients_made == []
    assert ai._calls == {}  # and it didn't spend any of the cap


# --- failures -------------------------------------------------------------------


def test_bedrock_errors_are_wrapped(bedrock):
    bedrock.error = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "slow"}}, "Converse"
    )
    with pytest.raises(ai.AIInvocationError, match="Bedrock call failed"):
        _call()
    bedrock.error = NoCredentialsError()
    with pytest.raises(ai.AIInvocationError):
        _call()


def test_malformed_response_is_an_invocation_error(bedrock):
    del bedrock.response["usage"]
    with pytest.raises(ai.AIInvocationError, match="Unexpected Bedrock response"):
        _call()


def test_failed_calls_still_count_against_the_cap(bedrock, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_daily_call_cap_per_clinic", 2)
    bedrock.error = NoCredentialsError()
    for _ in range(2):
        with pytest.raises(ai.AIInvocationError):
            _call()
    with pytest.raises(ai.AIDailyCapExceeded):
        _call()
    assert len(bedrock.calls) == 2


# --- daily cap ------------------------------------------------------------------


def test_cap_is_enforced_and_bedrock_is_not_called_once_exceeded(bedrock, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_daily_call_cap_per_clinic", 3)
    for _ in range(3):
        _call()
    assert len(bedrock.calls) == 3
    with pytest.raises(ai.AIDailyCapExceeded) as exc:
        _call()
    assert len(bedrock.calls) == 3  # the over-cap request never reached Bedrock
    assert exc.value.clinic_id == CLINIC and exc.value.cap == 3
    assert "cap" in str(exc.value) and "No call was made" in str(exc.value)


def test_default_cap_is_200(bedrock):
    assert get_settings().ai_daily_call_cap_per_clinic == 200
    for _ in range(200):
        _call()
    with pytest.raises(ai.AIDailyCapExceeded):
        _call()
    assert len(bedrock.calls) == 200


def test_cap_is_per_clinic(bedrock, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_daily_call_cap_per_clinic", 1)
    _call(clinic=CLINIC)
    _call(clinic=OTHER)
    with pytest.raises(ai.AIDailyCapExceeded):
        _call(clinic=CLINIC)
    with pytest.raises(ai.AIDailyCapExceeded):
        _call(clinic=OTHER)


def test_cap_resets_each_day_and_old_days_are_dropped(bedrock, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_daily_call_cap_per_clinic", 1)
    today = {"d": date(2026, 10, 2)}
    monkeypatch.setattr(ai, "_today", lambda: today["d"])
    _call()
    with pytest.raises(ai.AIDailyCapExceeded):
        _call()
    today["d"] = date(2026, 10, 3)
    _call()  # a new day: allowed again
    assert len(bedrock.calls) == 2
    assert list(ai._calls) == [(CLINIC, date(2026, 10, 3))]


@pytest.mark.parametrize("cap", [0, -5])
def test_zero_or_negative_cap_blocks_every_call(bedrock, monkeypatch, cap):
    monkeypatch.setattr(get_settings(), "ai_daily_call_cap_per_clinic", cap)
    with pytest.raises(ai.AIDailyCapExceeded):
        _call()
    assert bedrock.calls == [] and bedrock.clients_made == []


def test_cap_exceeded_never_even_builds_a_client(bedrock, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_daily_call_cap_per_clinic", 0)
    with pytest.raises(ai.AIDailyCapExceeded):
        _call()
    assert bedrock.clients_made == []


# --- templates ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "placeholders"),
    [
        ("draft_reply", {"protocol_notes", "conversation", "latest_patient_message"}),
        ("summary", {"messages"}),
    ],
)
def test_shipped_templates_load_with_structure_and_version(name, placeholders):
    t = ai.load_template(name, "v1")
    assert t.prompt_version == f"{name}_v1"  # derived from the filename
    assert t.system and t.user
    assert t.placeholders == placeholders
    assert "<!--" not in t.system + t.user  # authoring comments are stripped
    assert "PLACEHOLDER" in t.system  # wording is flagged as pending clinical review


def test_render_fills_placeholders_and_is_strict():
    t = ai.load_template("summary", "v1")
    system, user = t.render(messages="[m1] patient: ouch")
    assert "[m1] patient: ouch" in user and "{{" not in system + user
    with pytest.raises(ai.TemplateError, match="missing"):
        t.render()
    with pytest.raises(ai.TemplateError, match="unknown"):
        t.render(messages="x", extra="y")


def test_rendered_values_are_not_re_expanded():
    t = ai.load_template("summary", "v1")
    _, user = t.render(messages="{{messages}} stays literal")
    assert "{{messages}} stays literal" in user


def test_rendered_template_feeds_generate(bedrock):
    t = ai.load_template("summary", "v1")
    system, prompt = t.render(messages="[m1] patient: hi")
    result = ai.generate(
        clinic_id=CLINIC, prompt_version=t.prompt_version, system=system, prompt=prompt
    )
    assert bedrock.calls[0]["requestMetadata"]["prompt_version"] == "summary_v1"
    assert result.prompt_version == "summary_v1"


@pytest.mark.parametrize(
    ("name", "version"),
    [("../secrets", "v1"), ("summary", "../v1"), ("Summary", "v1"), ("summary", "1"), ("", "v1")],
)
def test_loader_rejects_path_tricks(name, version):
    with pytest.raises(ai.TemplateNotFound):
        ai.load_template(name, version)


def test_unknown_template_or_version_is_not_found():
    with pytest.raises(ai.TemplateNotFound):
        ai.load_template("summary", "v99")
    with pytest.raises(ai.TemplateNotFound):
        ai.load_template("nope", "v1")


def test_malformed_template_is_a_template_error(tmp_path, monkeypatch):
    (tmp_path / "broken_v1.md").write_text("## User\nonly a user section\n")
    monkeypatch.setattr(ai, "TEMPLATES_DIR", tmp_path)
    with pytest.raises(ai.TemplateError, match="System"):
        ai.load_template("broken", "v1")
