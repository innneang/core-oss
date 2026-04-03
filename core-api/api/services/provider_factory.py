"""
Provider Factory

Central factory for obtaining provider-specific implementations.
Routes to the correct provider based on the provider name (google, microsoft, icloud).

Usage:
    from api.services.provider_factory import ProviderFactory

    # Get OAuth provider for token operations
    oauth = ProviderFactory.get_oauth_provider("microsoft")
    tokens = oauth.exchange_auth_code(code)

    # Get email sync provider
    email_sync = ProviderFactory.get_email_sync_provider("google")
    result = email_sync.sync_emails(connection_data)
"""
from typing import Dict, Any, Optional
import logging

from api.services.auth_protocols import (
    OAuthProvider,
    EmailSyncProvider,
    CalendarSyncProvider,
    WebhookProvider
)

logger = logging.getLogger(__name__)

# Supported providers
SUPPORTED_PROVIDERS = ["google", "microsoft", "icloud"]


class ProviderNotFoundError(Exception):
    """Raised when an unsupported provider is requested"""
    pass


class ProviderNotImplementedError(Exception):
    """Raised when a provider exists but specific functionality isn't implemented yet"""
    pass


class ProviderFactory:
    """
    Factory class for obtaining provider-specific implementations.

    All methods are static - no instantiation needed.
    """

    @staticmethod
    def get_oauth_provider(provider: str) -> OAuthProvider:
        """
        Get OAuth provider implementation.

        Args:
            provider: Provider name ('google', 'microsoft', or 'icloud')

        Returns:
            OAuthProvider implementation

        Raises:
            ProviderNotFoundError: If provider is not supported
        """
        provider = provider.lower()

        if provider == "google":
            from api.services.google.google_oauth_provider import GoogleOAuthProvider
            return GoogleOAuthProvider()

        elif provider == "microsoft":
            from api.services.microsoft.microsoft_oauth_provider import MicrosoftOAuthProvider
            return MicrosoftOAuthProvider()

        else:
            raise ProviderNotFoundError(
                f"Unsupported OAuth provider: {provider}. "
                f"Supported providers: {SUPPORTED_PROVIDERS}"
            )

    @staticmethod
    def get_email_sync_provider(provider: str) -> EmailSyncProvider:
        """
        Get email sync provider implementation.

        Args:
            provider: Provider name ('google' or 'microsoft')

        Returns:
            EmailSyncProvider implementation

        Raises:
            ProviderNotFoundError: If provider is not supported
            ProviderNotImplementedError: If provider exists but email sync isn't implemented
        """
        provider = provider.lower()

        if provider == "google":
            from api.services.google.google_email_sync_provider import GoogleEmailSyncProvider
            return GoogleEmailSyncProvider()

        elif provider == "microsoft":
            from api.services.microsoft.microsoft_email_sync_provider import MicrosoftEmailSyncProvider
            return MicrosoftEmailSyncProvider()

        elif provider == "icloud":
            from api.services.icloud.icloud_mail_provider import ICloudEmailSyncProvider
            return ICloudEmailSyncProvider()

        else:
            raise ProviderNotFoundError(
                f"Unsupported email sync provider: {provider}. "
                f"Supported providers: {SUPPORTED_PROVIDERS}"
            )

    @staticmethod
    def get_calendar_sync_provider(provider: str) -> CalendarSyncProvider:
        """
        Get calendar sync provider implementation.

        Args:
            provider: Provider name ('google' or 'microsoft')

        Returns:
            CalendarSyncProvider implementation

        Raises:
            ProviderNotFoundError: If provider is not supported
            ProviderNotImplementedError: If provider exists but calendar sync isn't implemented
        """
        provider = provider.lower()

        if provider == "google":
            from api.services.google.google_calendar_sync_provider import GoogleCalendarSyncProvider
            return GoogleCalendarSyncProvider()

        elif provider == "microsoft":
            from api.services.microsoft.microsoft_calendar_sync_provider import MicrosoftCalendarSyncProvider
            return MicrosoftCalendarSyncProvider()

        else:
            raise ProviderNotFoundError(
                f"Unsupported calendar sync provider: {provider}. "
                f"Supported providers: {SUPPORTED_PROVIDERS}"
            )

    @staticmethod
    def get_webhook_provider(provider: str) -> WebhookProvider:
        """
        Get webhook provider implementation.

        Args:
            provider: Provider name ('google' or 'microsoft')

        Returns:
            WebhookProvider implementation

        Raises:
            ProviderNotFoundError: If provider is not supported
            ProviderNotImplementedError: If provider exists but webhooks aren't implemented
        """
        provider = provider.lower()

        if provider == "google":
            from api.services.google.google_webhook_provider import GoogleWebhookProvider
            return GoogleWebhookProvider()

        elif provider == "microsoft":
            from api.services.microsoft.microsoft_webhook_provider import MicrosoftWebhookProvider
            return MicrosoftWebhookProvider()

        else:
            raise ProviderNotFoundError(
                f"Unsupported webhook provider: {provider}. "
                f"Supported providers: {SUPPORTED_PROVIDERS}"
            )

    @staticmethod
    def is_supported(provider: str) -> bool:
        """Check if a provider is supported."""
        return provider.lower() in SUPPORTED_PROVIDERS

    @staticmethod
    def get_supported_providers() -> list:
        """Get list of supported provider names."""
        return SUPPORTED_PROVIDERS.copy()


# Convenience functions for common operations

def get_provider_for_connection(connection_data: Dict[str, Any]) -> str:
    """
    Extract provider name from connection data.

    Args:
        connection_data: Dict containing 'provider' key

    Returns:
        Provider name string

    Raises:
        ValueError: If provider not in connection data
    """
    provider = connection_data.get("provider")
    if not provider:
        raise ValueError("Connection data missing 'provider' field")
    return provider.lower()


def exchange_auth_code(
    provider: str,
    auth_code: str,
    redirect_uri: Optional[str] = None
) -> Dict[str, Any]:
    """
    Convenience function to exchange auth code for tokens.

    Args:
        provider: Provider name ('google' or 'microsoft')
        auth_code: Authorization code from OAuth flow
        redirect_uri: Redirect URI used in OAuth flow

    Returns:
        Token data dict
    """
    oauth_provider = ProviderFactory.get_oauth_provider(provider)
    return oauth_provider.exchange_auth_code(auth_code, redirect_uri)


def get_user_info(provider: str, access_token: str) -> Dict[str, Any]:
    """
    Convenience function to get user info.

    Args:
        provider: Provider name
        access_token: Valid access token

    Returns:
        User info dict
    """
    oauth_provider = ProviderFactory.get_oauth_provider(provider)
    return oauth_provider.get_user_info(access_token)
