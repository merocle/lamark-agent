"""
Test Module 9 — redaction pipeline.

Two stages:
  1) Deterministic secrets layer. Verified secret (AWS key, GitHub PAT, JWT,
     OpenAI key, etc.) HALTS the pipeline — non-overridable. This is the
     non-negotiable safety gate from v3 §7 / Kreuzhofer doc Part 3.
  2) PII layer (Phase 1: regex for email/phone/CC; Phase 2 adds Presidio +
     local LLM type-preserving substitution).

Audit log records every match — provenance for later review.

RED until src/lamark/redaction/* exists.
"""

from __future__ import annotations

import pytest


# ---- secrets layer — non-negotiable halt ---------------------------------


def test_aws_access_key_blocks_pipeline() -> None:
    """A verified AWS access key in input → pipeline raises SecretFound."""
    from lamark.redaction import RedactionPipeline
    from lamark.redaction.errors import SecretFound

    pipe = RedactionPipeline()
    text = "Here's my AWS creds: AKIAIOSFODNN7EXAMPLE for the test."
    with pytest.raises(SecretFound, match=r"AWS|aws_access_key"):
        pipe.process(text)


def test_github_pat_blocks_pipeline() -> None:
    """GitHub fine-grained PAT pattern → blocked."""
    from lamark.redaction import RedactionPipeline
    from lamark.redaction.errors import SecretFound

    pipe = RedactionPipeline()
    text = "use ghp_aBcD1234EfGh5678IjKlMnOpQrStUvWxYzAB to authenticate"
    with pytest.raises(SecretFound, match=r"GitHub|ghp_"):
        pipe.process(text)


def test_openai_api_key_blocks_pipeline() -> None:
    """OpenAI sk-... key → blocked."""
    from lamark.redaction import RedactionPipeline
    from lamark.redaction.errors import SecretFound

    pipe = RedactionPipeline()
    text = "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWX"
    with pytest.raises(SecretFound, match=r"OpenAI|sk-"):
        pipe.process(text)


def test_jwt_blocks_pipeline() -> None:
    """A JWT-shaped token blocks (RS256 header.payload.sig)."""
    from lamark.redaction import RedactionPipeline
    from lamark.redaction.errors import SecretFound

    pipe = RedactionPipeline()
    jwt = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1c2VyMTIzIn0.abcdefghIJKLMNOPqrstuvwxyz0123456"
    with pytest.raises(SecretFound, match=r"JWT|jwt"):
        pipe.process(f"token: {jwt}")


def test_user_denylist_blocks_internal_project_names() -> None:
    """Caller-supplied denylist (e.g. customer names, internal codenames) blocks."""
    from lamark.redaction import RedactionPipeline
    from lamark.redaction.errors import SecretFound

    pipe = RedactionPipeline(deny_phrases=["ProjectBlackbird", "AcmeCorp internal"])
    with pytest.raises(SecretFound, match=r"deny|ProjectBlackbird"):
        pipe.process("Notes about ProjectBlackbird quarterly review")


# ---- PII layer — substitute, not block ------------------------------------


def test_email_redacted_to_placeholder() -> None:
    """Email replaced with <email> token; original gone."""
    from lamark.redaction import RedactionPipeline

    pipe = RedactionPipeline()
    result = pipe.process("contact me at anna@example.com please")
    assert "anna@example.com" not in result.text
    assert "<email>" in result.text or "[EMAIL]" in result.text


def test_phone_number_redacted() -> None:
    from lamark.redaction import RedactionPipeline

    pipe = RedactionPipeline()
    result = pipe.process("call +7 (495) 123-45-67 tomorrow")
    assert "123-45-67" not in result.text
    assert "<phone>" in result.text or "[PHONE]" in result.text


def test_credit_card_redacted() -> None:
    from lamark.redaction import RedactionPipeline

    pipe = RedactionPipeline()
    result = pipe.process("visa: 4111 1111 1111 1111 expires 12/27")
    assert "4111 1111 1111 1111" not in result.text


def test_safe_text_passes_unchanged() -> None:
    """Plain text with no secrets or PII → identity."""
    from lamark.redaction import RedactionPipeline

    pipe = RedactionPipeline()
    safe = "I had soup for lunch and read a book about evolution."
    result = pipe.process(safe)
    assert result.text == safe
    assert result.redactions == ()


# ---- audit log ------------------------------------------------------------


def test_audit_log_records_each_redaction() -> None:
    """Each non-blocking redaction is recorded with category + offset."""
    from lamark.redaction import RedactionPipeline

    pipe = RedactionPipeline()
    result = pipe.process("email me at x@y.com or call +1 555 0100")
    cats = {r.category for r in result.redactions}
    assert "email" in cats or "EMAIL" in cats
    assert "phone" in cats or "PHONE" in cats
    # offsets must be valid into the ORIGINAL text
    for r in result.redactions:
        assert 0 <= r.start <= len(result.original_text)
        assert r.start < r.end <= len(result.original_text)


def test_secret_match_records_to_audit_even_though_pipeline_halts() -> None:
    """The audit log captures the verified secret hit before raising SecretFound."""
    from lamark.redaction import RedactionPipeline
    from lamark.redaction.errors import SecretFound

    pipe = RedactionPipeline()
    audit_buffer: list = []
    pipe.set_audit_sink(audit_buffer.append)
    with pytest.raises(SecretFound):
        pipe.process("ghp_aBcD1234EfGh5678IjKlMnOpQrStUvWxYzAB")
    assert audit_buffer, "audit sink must receive the verified-secret event before raise"
    assert any("ghp_" in str(e) or "GitHub" in str(e) for e in audit_buffer)
