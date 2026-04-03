import base64
import email.policy
from email import message_from_bytes

from api.services.icloud.icloud_mail_provider import (
    _build_db_email,
    _decode_header_value,
    _extract_body,
)


def _encode_encoded_word(value: str, charset: str = "cp874", label: str = "x-windows-874") -> str:
    encoded = base64.b64encode(value.encode(charset)).decode("ascii")
    return f"=?{label}?B?{encoded}?="


def test_decode_header_value_handles_thai_windows_874_alias():
    raw_header = _encode_encoded_word("สวัสดีครับ")

    assert _decode_header_value(raw_header) == "สวัสดีครับ"


def test_extract_body_decodes_thai_body_without_charset():
    raw_message = (
        b"Content-Type: text/plain\r\n"
        b"Content-Transfer-Encoding: 8bit\r\n"
        b"\r\n"
        + "สวัสดีครับ".encode("cp874")
    )

    message_obj = message_from_bytes(raw_message, policy=email.policy.default)
    text_body, html_body = _extract_body(message_obj)

    assert text_body == "สวัสดีครับ"
    assert html_body == ""


def test_build_db_email_uses_raw_headers_for_unknown_thai_charset_alias():
    subject = "ยินดีที่ได้รู้จัก"
    sender_name = "หมอเมธ"
    body = "สวัสดีครับ"
    raw_message = (
        f"Subject: {_encode_encoded_word(subject)}\r\n"
        f"From: {_encode_encoded_word(sender_name)} <doctor@example.com>\r\n"
        "To: patient@example.com\r\n"
        "Message-ID: <icloud-message-1>\r\n"
        "Date: Tue, 15 Apr 2025 10:00:00 +0700\r\n"
        "Content-Type: text/plain\r\n"
        "Content-Transfer-Encoding: 8bit\r\n"
        "\r\n"
    ).encode("ascii") + body.encode("cp874")

    message_obj = message_from_bytes(raw_message, policy=email.policy.default)
    email_row = _build_db_email(
        user_id="user-1",
        connection_id="conn-1",
        folder_name="INBOX",
        uid="123",
        flags=[],
        message_obj=message_obj,
    )

    assert email_row["subject"] == subject
    assert email_row["from"] == f"{sender_name} <doctor@example.com>"
    assert email_row["body"] == body
    assert email_row["snippet"] == body
