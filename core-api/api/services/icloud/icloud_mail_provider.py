"""
iCloud Mail adapter using IMAP for sync and SMTP for send.
"""
from __future__ import annotations

import codecs
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.header import decode_header
from email.message import EmailMessage
from email.utils import getaddresses, make_msgid, parsedate_to_datetime
import email.policy
import imaplib
import logging
import smtplib
from typing import Any, Dict, Iterable, Optional

from lib.supabase_client import get_service_role_client

logger = logging.getLogger(__name__)

DEFAULT_ICLOUD_IMAP_HOST = "imap.mail.me.com"
DEFAULT_ICLOUD_IMAP_PORT = 993
DEFAULT_ICLOUD_SMTP_HOST = "smtp.mail.me.com"
DEFAULT_ICLOUD_SMTP_PORT = 587

DEFAULT_FOLDERS = {
    "inbox": "INBOX",
    "sent": "Sent Messages",
    "drafts": "Drafts",
    "trash": "Deleted Messages",
}

THAI_CHARSET_ALIASES = {
    "windows874": "cp874",
    "windows-874": "cp874",
    "x-windows-874": "cp874",
    "cp-874": "cp874",
    "cp874": "cp874",
    "tis620": "tis-620",
    "tis-620": "tis-620",
    "x-tis-620": "tis-620",
    "tis_620-0": "tis-620",
    "iso8859-11": "iso-8859-11",
    "iso-8859-11": "iso-8859-11",
    "iso_8859-11": "iso-8859-11",
}

THAI_FALLBACK_CHARSETS = ("cp874", "tis-620", "iso-8859-11")
GENERIC_FALLBACK_CHARSETS = ("utf-8", "windows-1252", "latin-1")


def _candidate_usernames(send_as_email: str, metadata: Dict[str, Any]) -> list[str]:
    candidates = [
        metadata.get("imap_username"),
        metadata.get("smtp_username"),
        metadata.get("auth_email"),
        send_as_email,
    ]
    seen: set[str] = set()
    result: list[str] = []
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        result.append(candidate)
    return result


def build_icloud_metadata(metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    base = dict(metadata or {})
    base.setdefault("auth_type", "app_password")
    base.setdefault("imap_host", DEFAULT_ICLOUD_IMAP_HOST)
    base.setdefault("imap_port", DEFAULT_ICLOUD_IMAP_PORT)
    base.setdefault("smtp_host", DEFAULT_ICLOUD_SMTP_HOST)
    base.setdefault("smtp_port", DEFAULT_ICLOUD_SMTP_PORT)
    base.setdefault("folder_map", DEFAULT_FOLDERS.copy())
    return base


def get_icloud_credentials(connection_data: Dict[str, Any]) -> tuple[str, str, Dict[str, Any], str]:
    send_as_email = connection_data.get("provider_email")
    app_password = connection_data.get("refresh_token")
    metadata = build_icloud_metadata(connection_data.get("metadata"))
    auth_email = metadata.get("auth_email") or send_as_email

    if not send_as_email or not app_password or not auth_email:
        raise ValueError("Missing iCloud email or app-specific password")

    return auth_email, app_password, metadata, send_as_email


def _imap_login(auth_email: str, app_password: str, metadata: Dict[str, Any]) -> imaplib.IMAP4_SSL:
    host = metadata.get("imap_host", DEFAULT_ICLOUD_IMAP_HOST)
    port = int(metadata.get("imap_port", DEFAULT_ICLOUD_IMAP_PORT))
    imap_username = metadata.get("imap_username") or auth_email
    client = imaplib.IMAP4_SSL(host, port)
    client.login(imap_username, app_password)
    return client


def test_icloud_connection(
    email_address: str,
    app_password: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    base_metadata = build_icloud_metadata(metadata)
    candidates = _candidate_usernames(email_address, base_metadata)
    errors: list[str] = []

    for candidate in candidates:
        client = None
        try:
            client = _imap_login(candidate, app_password, {**base_metadata, "imap_username": candidate})
            status, _ = client.noop()
            if status != "OK":
                raise ValueError("NOOP failed")
            return candidate
        except imaplib.IMAP4.error as exc:
            errors.append(f"{candidate}: {exc!s}")
        except Exception as exc:
            errors.append(f"{candidate}: {exc!s}")
        finally:
            if client is not None:
                try:
                    client.logout()
                except Exception:
                    pass

    attempted = ", ".join(candidates)
    detail = " | ".join(errors) if errors else "no username candidates"
    raise ValueError(f"iCloud IMAP authentication failed. attempted=[{attempted}] details=[{detail}]")


def _decode_header_value(value: Optional[str]) -> str:
    if not value:
        return ""
    parts: list[str] = []
    for fragment, charset in decode_header(value):
        if isinstance(fragment, bytes):
            parts.append(_decode_payload_bytes(fragment, charset))
        else:
            parts.append(fragment)
    return "".join(parts)


def _normalize_charset(charset: Optional[str]) -> Optional[str]:
    if not charset:
        return None

    normalized = charset.strip().strip('"').lower().replace("_", "-")
    return THAI_CHARSET_ALIASES.get(normalized, normalized)


def _looks_like_thai_text(value: str) -> bool:
    thai_chars = sum(1 for char in value if "\u0E00" <= char <= "\u0E7F")
    alpha_chars = sum(1 for char in value if char.isalpha())
    return thai_chars >= 3 and thai_chars >= max(3, int(alpha_chars * 0.3))


def _try_decode_bytes(payload: bytes, charset: Optional[str]) -> Optional[str]:
    normalized = _normalize_charset(charset)
    if not normalized:
        return None

    try:
        codecs.lookup(normalized)
    except LookupError:
        return None

    try:
        return payload.decode(normalized)
    except UnicodeDecodeError:
        return None


def _decode_payload_bytes(payload: bytes, declared_charset: Optional[str]) -> str:
    if not payload:
        return ""

    normalized = _normalize_charset(declared_charset)

    decoded = _try_decode_bytes(payload, normalized)
    if decoded is not None:
        return decoded

    if not normalized:
        decoded = _try_decode_bytes(payload, "utf-8")
        if decoded is not None:
            return decoded

    thai_candidates: list[str] = []
    for charset in THAI_FALLBACK_CHARSETS:
        decoded = _try_decode_bytes(payload, charset)
        if decoded is None:
            continue
        if _looks_like_thai_text(decoded):
            return decoded
        thai_candidates.append(decoded)

    for charset in GENERIC_FALLBACK_CHARSETS:
        if charset == normalized:
            continue
        decoded = _try_decode_bytes(payload, charset)
        if decoded is not None:
            return decoded

    if thai_candidates:
        return thai_candidates[0]

    fallback_charsets = []
    if normalized:
        fallback_charsets.append(normalized)
    fallback_charsets.extend(GENERIC_FALLBACK_CHARSETS)
    fallback_charsets.extend(THAI_FALLBACK_CHARSETS)

    for charset in fallback_charsets:
        normalized_charset = _normalize_charset(charset)
        if not normalized_charset:
            continue
        try:
            return payload.decode(normalized_charset, errors="replace")
        except LookupError:
            continue

    return payload.decode("utf-8", errors="replace")


def _get_raw_header_values(message_obj, name: str) -> list[str]:
    return [value for key, value in message_obj.raw_items() if key.lower() == name.lower()]


def _get_raw_header_value(message_obj, name: str) -> Optional[str]:
    values = _get_raw_header_values(message_obj, name)
    return values[-1] if values else None


def _extract_body(message_obj) -> tuple[str, str]:
    text_parts: list[str] = []
    html_parts: list[str] = []

    if message_obj.is_multipart():
        for part in message_obj.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get_filename():
                continue
            payload = part.get_payload(decode=True) or b""
            content = _decode_payload_bytes(payload, part.get_content_charset())
            if part.get_content_type() == "text/html":
                html_parts.append(content)
            elif part.get_content_type() == "text/plain":
                text_parts.append(content)
    else:
        payload = message_obj.get_payload(decode=True) or b""
        content = _decode_payload_bytes(payload, message_obj.get_content_charset())
        if message_obj.get_content_type() == "text/html":
            html_parts.append(content)
        else:
            text_parts.append(content)

    return "\n".join(text_parts).strip(), "\n".join(html_parts).strip()


def _extract_attachments(message_obj) -> list[Dict[str, Any]]:
    attachments: list[Dict[str, Any]] = []
    for part in message_obj.walk():
        filename = part.get_filename()
        if not filename:
            continue
        payload = part.get_payload(decode=True) or b""
        attachments.append(
            {
                "filename": _decode_header_value(filename),
                "mimeType": part.get_content_type(),
                "size": len(payload),
            }
        )
    return attachments


def _normalize_message_id(message_obj, folder_name: str, uid: str) -> str:
    message_id = _decode_header_value(_get_raw_header_value(message_obj, "Message-ID")).strip()
    if message_id:
        return message_id
    return f"icloud:{folder_name}:{uid}"


def _derive_thread_id(message_obj, external_id: str) -> str:
    references = _decode_header_value(_get_raw_header_value(message_obj, "References")).strip()
    if references:
        parts = references.split()
        if parts:
            return parts[0]
    in_reply_to = _decode_header_value(_get_raw_header_value(message_obj, "In-Reply-To")).strip()
    if in_reply_to:
        return in_reply_to
    return external_id


def _parse_received_at(message_obj) -> str:
    raw_date = _decode_header_value(_get_raw_header_value(message_obj, "Date")).strip()
    if not raw_date:
        return datetime.now(timezone.utc).isoformat()
    try:
        parsed = parsedate_to_datetime(raw_date)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def _extract_labels(folder_name: str, flags: Iterable[str]) -> list[str]:
    labels = [folder_name]
    normalized_flags = {flag.upper() for flag in flags}
    if "\\SEEN" not in normalized_flags:
        labels.append("UNREAD")
    if "\\FLAGGED" in normalized_flags:
        labels.append("FLAGGED")
    if folder_name.upper() == "INBOX":
        labels.append("INBOX")
    return sorted(set(labels))


def _build_db_email(
    *,
    user_id: str,
    connection_id: str,
    folder_name: str,
    uid: str,
    flags: Iterable[str],
    message_obj,
) -> Dict[str, Any]:
    external_id = _normalize_message_id(message_obj, folder_name, uid)
    text_body, html_body = _extract_body(message_obj)
    attachments = _extract_attachments(message_obj)
    from_header = _decode_header_value(_get_raw_header_value(message_obj, "From"))
    to_headers = [_decode_header_value(value) for value in _get_raw_header_values(message_obj, "To")]
    cc_headers = [_decode_header_value(value) for value in _get_raw_header_values(message_obj, "Cc")]
    bcc_headers = [_decode_header_value(value) for value in _get_raw_header_values(message_obj, "Bcc")]

    from_addrs = getaddresses([from_header])
    to_addrs = [addr for _, addr in getaddresses(to_headers)]
    cc_addrs = [addr for _, addr in getaddresses(cc_headers)]
    bcc_addrs = [addr for _, addr in getaddresses(bcc_headers)]
    labels = _extract_labels(folder_name, flags)

    provider_ids = {
        "icloud": {
            "folder": folder_name,
            "uid": uid,
        }
    }

    from_name, from_email = from_addrs[0] if from_addrs else ("", "")
    from_value = f"{from_name} <{from_email}>".strip() if from_email else from_header

    return {
        "user_id": user_id,
        "ext_connection_id": connection_id,
        "external_id": external_id,
        "thread_id": _derive_thread_id(message_obj, external_id),
        "subject": _decode_header_value(_get_raw_header_value(message_obj, "Subject")) or "(No Subject)",
        "from": from_value,
        "to": to_addrs or None,
        "cc": cc_addrs or None,
        "bcc": bcc_addrs or None,
        "body": html_body or text_body,
        "snippet": (text_body or html_body or "")[:180],
        "labels": labels,
        "is_read": "UNREAD" not in labels,
        "is_draft": folder_name.lower() == "drafts",
        "is_trashed": folder_name.lower() in {"trash", "deleted messages"},
        "is_starred": "FLAGGED" in labels,
        "received_at": _parse_received_at(message_obj),
        "has_attachments": len(attachments) > 0,
        "attachments": attachments,
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "raw_item": {
            "provider": "icloud",
            "folder": folder_name,
            "uid": uid,
            "flags": list(flags),
            "message_id": external_id,
        },
        "provider_ids": provider_ids,
    }


def sync_icloud_connection(
    *,
    user_id: str,
    connection_id: str,
    connection_data: Dict[str, Any],
    max_results: int = 50,
    days_back: int = 20,
) -> Dict[str, Any]:
    auth_email, app_password, metadata, send_as_email = get_icloud_credentials(connection_data)
    folder_map = metadata.get("folder_map") or DEFAULT_FOLDERS
    folder_state = dict((metadata.get("imap_sync_state") or {}))
    client = _imap_login(auth_email, app_password, metadata)
    supabase = get_service_role_client()
    new_count = 0

    try:
        for folder_name in dict.fromkeys(folder_map.values()).keys():
            status, _ = client.select(f'"{folder_name}"', readonly=True)
            if status != "OK":
                logger.warning("[iCloud] Unable to select folder %s for %s", folder_name, auth_email)
                continue

            last_uid = int(folder_state.get(folder_name, 0) or 0)
            if last_uid > 0:
                search_criteria = f"(UID {last_uid + 1}:*)"
            else:
                since = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%d-%b-%Y")
                search_criteria = f'(SINCE "{since}")'

            status, data = client.uid("SEARCH", None, search_criteria)
            if status != "OK":
                continue

            uids = [uid for uid in (data[0] or b"").decode().split() if uid]
            if max_results and len(uids) > max_results:
                uids = uids[-max_results:]

            highest_uid = last_uid
            for uid in uids:
                fetch_status, fetch_data = client.uid(
                    "FETCH",
                    uid,
                    "(BODY.PEEK[] FLAGS UID)"
                )
                if fetch_status != "OK" or not fetch_data:
                    continue

                raw_bytes = b""
                flags: list[str] = []
                for item in fetch_data:
                    if isinstance(item, tuple):
                        meta = item[0].decode(errors="ignore") if isinstance(item[0], bytes) else str(item[0])
                        raw_bytes = item[1]
                    else:
                        meta = item.decode(errors="ignore") if isinstance(item, bytes) else str(item)

                    if "FLAGS (" in meta:
                        flag_chunk = meta.split("FLAGS (", 1)[1].split(")", 1)[0]
                        flags = [flag for flag in flag_chunk.split() if flag]

                if not raw_bytes:
                    continue

                msg = message_from_bytes(raw_bytes, policy=email.policy.default)
                email_row = _build_db_email(
                    user_id=user_id,
                    connection_id=connection_id,
                    folder_name=folder_name,
                    uid=uid,
                    flags=flags,
                    message_obj=msg,
                )
                supabase.table("emails").upsert(email_row, on_conflict="user_id,external_id").execute()
                new_count += 1
                highest_uid = max(highest_uid, int(uid))

            if highest_uid > last_uid:
                folder_state[folder_name] = highest_uid

        metadata["imap_sync_state"] = folder_state
        metadata["last_sync_mode"] = "imap"
        supabase.table("ext_connections").update(
            {
                "metadata": metadata,
                "last_synced": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", connection_id).execute()

        return {
            "success": True,
            "provider": "icloud",
            "new_emails": new_count,
            "updated_emails": 0,
            "folders": list(folder_state.keys()),
        }
    finally:
        try:
            client.logout()
        except Exception:
            pass


def _build_smtp_message(
    *,
    from_address: str,
    to: str,
    subject: str,
    body: str,
    cc: Optional[list[str]] = None,
    bcc: Optional[list[str]] = None,
    html_body: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
    attachments: Optional[list[Dict[str, Any]]] = None,
) -> tuple[EmailMessage, str]:
    msg = EmailMessage()
    msg["From"] = from_address
    msg["To"] = to
    if cc:
        msg["Cc"] = ", ".join(cc)
    if subject:
        msg["Subject"] = subject
    message_id = make_msgid(domain=from_address.split("@")[-1])
    msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references

    if html_body:
        msg.set_content(body or "")
        msg.add_alternative(html_body, subtype="html")
    else:
        msg.set_content(body or "")

    for attachment in attachments or []:
        content = attachment.get("content")
        if isinstance(content, str):
            content_bytes = content.encode()
        else:
            content_bytes = content or b""
        mime_type = attachment.get("mimeType") or attachment.get("mime_type") or "application/octet-stream"
        maintype, _, subtype = mime_type.partition("/")
        if not subtype:
            maintype, subtype = "application", "octet-stream"
        msg.add_attachment(
            content_bytes,
            maintype=maintype,
            subtype=subtype,
            filename=attachment.get("filename") or attachment.get("name") or "attachment",
        )

    return msg, message_id


def send_icloud_email(
    *,
    user_id: str,
    connection_id: str,
    connection_data: Dict[str, Any],
    to: str,
    subject: str,
    body: str,
    cc: Optional[list[str]] = None,
    bcc: Optional[list[str]] = None,
    html_body: Optional[str] = None,
    thread_id: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
    attachments: Optional[list[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    auth_email, app_password, metadata, send_as_email = get_icloud_credentials(connection_data)
    smtp_host = metadata.get("smtp_host", DEFAULT_ICLOUD_SMTP_HOST)
    smtp_port = int(metadata.get("smtp_port", DEFAULT_ICLOUD_SMTP_PORT))
    smtp_username = metadata.get("smtp_username") or auth_email
    msg, message_id = _build_smtp_message(
        from_address=send_as_email,
        to=to,
        subject=subject,
        body=body,
        cc=cc,
        bcc=bcc,
        html_body=html_body,
        in_reply_to=in_reply_to,
        references=references,
        attachments=attachments,
    )

    recipients = [addr for addr in [to, *(cc or []), *(bcc or [])] if addr]
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(smtp_username, app_password)
            smtp.send_message(msg, from_addr=send_as_email, to_addrs=recipients)
    except smtplib.SMTPAuthenticationError as exc:
        raise ValueError("iCloud SMTP authentication failed") from exc
    except Exception as exc:
        raise ValueError(f"Failed to send via iCloud SMTP: {exc}") from exc

    supabase = get_service_role_client()
    sent_row = {
        "user_id": user_id,
        "ext_connection_id": connection_id,
        "external_id": message_id,
        "thread_id": thread_id or references or in_reply_to or message_id,
        "subject": subject,
        "from": send_as_email,
        "to": [to] if to else None,
        "cc": cc or None,
        "bcc": bcc or None,
        "body": html_body or body,
        "snippet": (body or html_body or "")[:180],
        "labels": ["Sent Messages", "SENT"],
        "is_read": True,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "has_attachments": bool(attachments),
        "attachments": attachments or [],
        "provider_ids": {"icloud": {"folder": "Sent Messages", "uid": None}},
        "raw_item": {"provider": "icloud", "message_id": message_id, "transport": "smtp"},
    }
    supabase.table("emails").upsert(sent_row, on_conflict="user_id,external_id").execute()

    return {
        "message": "Email sent successfully",
        "email": {
            "id": message_id,
            "thread_id": sent_row["thread_id"],
            "to": to,
            "subject": subject,
            "labels": sent_row["labels"],
        },
    }


def update_icloud_message_flags(
    connection_data: Dict[str, Any],
    provider_ids: Dict[str, Any],
    *,
    mark_read: Optional[bool] = None,
    mark_starred: Optional[bool] = None,
) -> None:
    auth_email, app_password, metadata, _ = get_icloud_credentials(connection_data)
    icloud_ids = (provider_ids or {}).get("icloud") or {}
    folder_name = icloud_ids.get("folder")
    uid = icloud_ids.get("uid")
    if not folder_name or not uid:
        raise ValueError("This iCloud message has not been synced from IMAP yet")

    client = _imap_login(auth_email, app_password, metadata)
    try:
        status, _ = client.select(f'"{folder_name}"')
        if status != "OK":
            raise ValueError(f"Could not open iCloud folder {folder_name}")
        if mark_read is not None:
            flag_op = "+FLAGS" if mark_read else "-FLAGS"
            flag_value = "\\Seen"
            client.uid("STORE", uid, flag_op, f"({flag_value})")
        if mark_starred is not None:
            flag_op = "+FLAGS" if mark_starred else "-FLAGS"
            flag_value = "\\Flagged"
            client.uid("STORE", uid, flag_op, f"({flag_value})")
    finally:
        try:
            client.logout()
        except Exception:
            pass


class ICloudEmailSyncProvider:
    @property
    def provider_name(self) -> str:
        return "icloud"

    def sync_emails(
        self,
        user_id: str,
        connection_id: str,
        connection_data: Dict[str, Any],
        max_results: int = 50,
        days_back: int = 20,
    ) -> Dict[str, Any]:
        return sync_icloud_connection(
            user_id=user_id,
            connection_id=connection_id,
            connection_data=connection_data,
            max_results=max_results,
            days_back=days_back,
        )

    def sync_incremental(
        self,
        user_id: str,
        connection_id: str,
        connection_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self.sync_emails(
            user_id=user_id,
            connection_id=connection_id,
            connection_data=connection_data,
            max_results=100,
            days_back=30,
        )

    def parse_email(self, raw_message: Dict[str, Any]) -> Dict[str, Any]:
        return raw_message
