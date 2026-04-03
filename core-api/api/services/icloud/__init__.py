"""
iCloud mail provider helpers.
"""

from .icloud_mail_provider import (
    DEFAULT_ICLOUD_IMAP_HOST,
    DEFAULT_ICLOUD_IMAP_PORT,
    DEFAULT_ICLOUD_SMTP_HOST,
    DEFAULT_ICLOUD_SMTP_PORT,
    ICloudEmailSyncProvider,
    build_icloud_metadata,
    get_icloud_credentials,
    send_icloud_email,
    sync_icloud_connection,
    test_icloud_connection,
    update_icloud_message_flags,
)

__all__ = [
    "DEFAULT_ICLOUD_IMAP_HOST",
    "DEFAULT_ICLOUD_IMAP_PORT",
    "DEFAULT_ICLOUD_SMTP_HOST",
    "DEFAULT_ICLOUD_SMTP_PORT",
    "ICloudEmailSyncProvider",
    "build_icloud_metadata",
    "get_icloud_credentials",
    "send_icloud_email",
    "sync_icloud_connection",
    "test_icloud_connection",
    "update_icloud_message_flags",
]
