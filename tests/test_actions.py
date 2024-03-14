import pytest

from tracecat.runner.actions import (
    run_send_email_action,
)


@pytest.mark.webtest
@pytest.mark.asyncio
async def test_send_email_action_all_success():
    test_sender = "Mewtwo <test@tracecat.com>"
    recipients = ["chris+test@tracecat.com", "daryl+test@tracecat.com"]
    subject = "[TEST] Hello from Tracecat!"
    body = "Meow! 🐱"
    email_response = await run_send_email_action(
        sender=test_sender,
        recipients=recipients,
        subject=subject,
        body=body,
    )
    assert email_response == {
        "status": "ok",
        "message": "Successfully sent email",
        "provider": "resend",
        "sender": test_sender,
        "recipients": recipients,
        "subject": subject,
        "body": body,
    }


@pytest.mark.webtest
@pytest.mark.asyncio
async def test_send_email_action_unrecognized_provider():
    test_sender = "Mewtwo <test@tracecat.com>"
    recipients = ["chris+test@tracecat.com", "daryl+test@tracecat.com"]
    subject = "[TEST] Hello from Tracecat!"
    body = "Meow! 🐱"
    provider = "e-meow"
    email_response = await run_send_email_action(
        sender=test_sender,
        recipients=recipients,
        subject=subject,
        body=body,
        provider=provider,
    )
    assert email_response == {
        "status": "error",
        "message": "Email provider not recognized",
        "provider": provider,
        "sender": test_sender,
        "recipients": recipients,
        "subject": subject,
        "body": body,
    }


@pytest.mark.webtest
@pytest.mark.asyncio
async def test_send_email_action_bounced():
    test_sender = "Mewtwo <test@tracecat.com>"
    recipients = ["doesnotexist@tracecat.com"]
    subject = "[TEST] Hello from Tracecat!"
    body = "Meow! 🐱"
    email_response = await run_send_email_action(
        sender=test_sender,
        recipients=recipients,
        subject=subject,
        body=body,
    )
    assert email_response == {
        "status": "warning",
        "message": "Email bounced",
        "provider": "resend",
        "sender": test_sender,
        "recipients": recipients,
        "subject": subject,
        "body": body,
    }
