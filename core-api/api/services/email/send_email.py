"""
Email service - Send email operations
"""
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from lib.supabase_client import get_authenticated_supabase_client
from lib.token_encryption import decrypt_ext_connection_tokens
import logging
from googleapiclient.errors import HttpError
from .google_api_helpers import (
    get_gmail_service,
    create_message,
    parse_email_headers,
    decode_email_body,
    get_attachment_info
)
from .get_email_details import get_email_attachment
from .analyze_email_ai import analyze_email_with_ai
from .draft_cleanup import cleanup_thread_drafts
from api.services.icloud import send_icloud_email

logger = logging.getLogger(__name__)


def _get_connection_for_send(user_id: str, user_jwt: str, from_account_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not from_account_id:
        return None

    auth_supabase = get_authenticated_supabase_client(user_jwt)
    result = auth_supabase.table('ext_connections')\
        .select('id, provider, provider_email, access_token, refresh_token, token_expires_at, metadata')\
        .eq('id', from_account_id)\
        .eq('user_id', user_id)\
        .eq('is_active', True)\
        .maybe_single()\
        .execute()

    if not result.data:
        raise ValueError("Selected email account was not found")

    return decrypt_ext_connection_tokens(result.data)


def send_email(
    user_id: str,
    user_jwt: str,
    to: str,
    subject: str,
    body: str,
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
    html_body: Optional[str] = None,
    thread_id: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
    from_account_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Send an email via Gmail

    Args:
        user_id: User's ID
        user_jwt: User's Supabase JWT for authenticated requests
        to: Recipient email address
        subject: Email subject
        body: Plain text email body
        cc: Optional list of CC recipients
        bcc: Optional list of BCC recipients
        html_body: Optional HTML version of email body
        thread_id: Optional thread ID to send as reply
        in_reply_to: Optional Message-ID of email being replied to (for threading)
        references: Optional chain of Message-IDs for the thread
        attachments: Optional list of attachments
        from_account_id: Optional account ID to send from (multi-account support).
                        If None, uses the primary account.

    Returns:
        Dict with sent email details
    """
    auth_supabase = get_authenticated_supabase_client(user_jwt)
    connection_data = _get_connection_for_send(user_id, user_jwt, from_account_id)

    if connection_data and connection_data.get('provider') == 'icloud':
        return send_icloud_email(
            user_id=user_id,
            connection_id=connection_data['id'],
            connection_data=connection_data,
            to=to,
            subject=subject,
            body=body,
            cc=cc,
            bcc=bcc,
            html_body=html_body,
            thread_id=thread_id,
            in_reply_to=in_reply_to,
            references=references,
            attachments=attachments,
        )

    # Get Gmail service (with optional specific account)
    service, connection_id = get_gmail_service(user_id, user_jwt, account_id=from_account_id)
    
    if not service or not connection_id:
        raise ValueError("No active Google connection found for user. Please sign in with Google first.")
    
    try:
        # If replying to a thread but missing threading headers, try to fetch them
        if thread_id and not in_reply_to:
            try:
                # Get the latest message in the thread to get its Message-ID
                thread_data = service.users().threads().get(
                    userId='me',
                    id=thread_id,
                    format='metadata',
                    metadataHeaders=['Message-ID', 'References']
                ).execute()
                
                messages = thread_data.get('messages', [])
                if messages:
                    # Get the last message in the thread
                    last_message = messages[-1]
                    headers = {h['name'].lower(): h['value'] 
                              for h in last_message.get('payload', {}).get('headers', [])}
                    in_reply_to = headers.get('message-id')
                    
                    # Build references chain: existing references + this message's ID
                    existing_refs = headers.get('references', '')
                    if existing_refs and in_reply_to:
                        references = f"{existing_refs} {in_reply_to}"
                    elif in_reply_to:
                        references = in_reply_to
                        
                    logger.info(f"📧 Auto-fetched threading headers: In-Reply-To={in_reply_to}")
            except Exception as e:
                logger.warning(f"⚠️ Could not fetch thread headers for threading: {e}")
        
        # Create MIME message with threading headers
        message = create_message(
            to=to,
            subject=subject,
            body=body,
            cc=cc,
            bcc=bcc,
            html_body=html_body,
            attachments=attachments,
            in_reply_to=in_reply_to,
            references=references
        )
        
        # Add thread ID if this is a reply
        send_params = {
            'userId': 'me',
            'body': message
        }
        
        if thread_id:
            send_params['body']['threadId'] = thread_id
        
        # Send the message
        sent_message = service.users().messages().send(**send_params).execute()
        
        sent_id = sent_message.get('id')
        sent_thread_id = sent_message.get('threadId')
        
        logger.info(f"✅ Sent email {sent_id} for user {user_id}")
        
        # Fetch the sent message to get the 'from' field
        try:
            detailed_message = service.users().messages().get(
                userId='me',
                id=sent_id,
                format='metadata',
                metadataHeaders=['From', 'To', 'Subject', 'Date']
            ).execute()
            
            headers = {h['name'].lower(): h['value'] for h in detailed_message['payload']['headers']}
            from_address = headers.get('from', '')
        except Exception as e:
            logger.warning(f"Could not fetch 'from' field for sent message: {e}")
            from_address = ''
        
        # Store in database
        to_addresses = [to] if to else []
        cc_addresses = cc if cc else []
        bcc_addresses = bcc if bcc else []
        
        # Use plain text body, or HTML if plain not available
        body_content = body or html_body or ''
        
        db_data = {
            'user_id': user_id,
            'ext_connection_id': connection_id,
            'external_id': sent_id,
            'thread_id': sent_thread_id,
            'subject': subject,
            'from': from_address,
            'to': to_addresses,
            'cc': cc_addresses if cc_addresses else None,
            'bcc': bcc_addresses if bcc_addresses else None,
            'body': body_content,
            'snippet': body[:100] if body else '',
            'labels': ['SENT'],
            'is_read': True,
            'received_at': datetime.now(timezone.utc).isoformat(),
            'raw_item': sent_message
        }
        
        # Use upsert to handle race condition where Gmail push sync
        # may have already inserted this email before we could
        result = auth_supabase.table('emails')\
            .upsert(db_data, on_conflict='user_id,external_id')\
            .execute()
        
        # Analyze sent email with AI
        if result.data and len(result.data) > 0:
            try:
                email_id = result.data[0]['id']
                analysis = analyze_email_with_ai(
                    subject=subject,
                    from_address=from_address,
                    body=body_content,
                    snippet=body[:100] if body else ''
                )
                auth_supabase.table('emails').update({
                    'ai_analyzed': True,
                    'ai_summary': analysis['summary'],
                    'ai_important': analysis['important']
                }).eq('id', email_id).execute()
                logger.info(f"🤖 AI analyzed sent email: {analysis['summary']}")
            except Exception as ai_err:
                logger.warning(f"⚠️ Failed to AI analyze sent email: {str(ai_err)}")

        # Clean up any draft rows in this thread (safety net)
        if sent_thread_id:
            try:
                cleanup_thread_drafts(
                    user_id=user_id,
                    user_jwt=user_jwt,
                    ext_connection_id=connection_id,
                    thread_id=sent_thread_id,
                    gmail_service=service,
                    exclude_external_id=None,  # delete ALL drafts in thread
                )
            except Exception as cleanup_err:
                logger.warning(f"Draft cleanup after send failed (non-fatal): {cleanup_err}")

        return {
            "message": "Email sent successfully",
            "email": {
                'id': sent_id,
                'thread_id': sent_thread_id,
                'to': to,
                'subject': subject,
                'labels': sent_message.get('labelIds', [])
            }
        }
        
    except HttpError as e:
        logger.error(f"Gmail API error: {str(e)}")
        raise ValueError(f"Failed to send email: {str(e)}")
    except Exception as e:
        logger.error(f"Error sending email: {str(e)}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise ValueError(f"Failed to send email: {str(e)}")


def reply_to_email(
    user_id: str,
    user_jwt: str,
    original_email_id: str,
    body: str,
    html_body: Optional[str] = None,
    reply_all: bool = False
) -> Dict[str, Any]:
    """
    Reply to an existing email.

    Auto-detects which account to use based on the original email's ext_connection_id.

    Args:
        user_id: User's ID
        user_jwt: User's Supabase JWT for authenticated requests
        original_email_id: ID of the email to reply to
        body: Reply body text
        html_body: Optional HTML version of reply body
        reply_all: Whether to reply to all recipients (default False)

    Returns:
        Dict with sent reply details
    """
    auth_supabase = get_authenticated_supabase_client(user_jwt)

    # Auto-detect account from original email
    from_account_id = None
    try:
        original_db = auth_supabase.table('emails')\
            .select('ext_connection_id')\
            .eq('external_id', original_email_id)\
            .eq('user_id', user_id)\
            .single()\
            .execute()
        if original_db.data:
            from_account_id = original_db.data.get('ext_connection_id')
            logger.info(f"📧 Auto-detected account for reply: {from_account_id[:8] if from_account_id else 'primary'}...")
    except Exception as e:
        logger.warning(f"Could not auto-detect account for reply: {e}")

    # Get Gmail service (using auto-detected account or default)
    service, connection_id = get_gmail_service(user_id, user_jwt, account_id=from_account_id)
    
    if not service:
        raise ValueError("No active Google connection found for user. Please sign in with Google first.")
    
    try:
        # Get original message to extract thread ID, recipients, and threading headers
        original = service.users().messages().get(
            userId='me',
            id=original_email_id,
            format='metadata',
            metadataHeaders=['From', 'To', 'Cc', 'Subject', 'Message-ID', 'References']
        ).execute()
        
        thread_id = original.get('threadId')
        headers = {h['name'].lower(): h['value'] for h in original['payload']['headers']}
        
        # Determine recipients
        original_from = headers.get('from', '')
        original_subject = headers.get('subject', '')
        message_id = headers.get('message-id', '')
        existing_references = headers.get('references', '')
        
        # Build References header: existing references + message being replied to
        if existing_references and message_id:
            references = f"{existing_references} {message_id}"
        elif message_id:
            references = message_id
        else:
            references = None
        
        # Parse "from" to get email address
        import re
        from_match = re.search(r'<(.+?)>', original_from)
        to_address = from_match.group(1) if from_match else original_from.strip()
        
        # Build reply subject
        if not original_subject.lower().startswith('re:'):
            reply_subject = f"Re: {original_subject}"
        else:
            reply_subject = original_subject
        
        # Get CC list if reply all
        cc_list = None
        if reply_all:
            original_to = headers.get('to', '')
            original_cc = headers.get('cc', '')
            # Parse and combine recipients (excluding self)
            # This is simplified - production would need better email parsing
            cc_addresses = []
            if original_to:
                cc_addresses.extend([a.strip() for a in original_to.split(',')])
            if original_cc:
                cc_addresses.extend([a.strip() for a in original_cc.split(',')])
            # TODO: Filter out user's own email address
            cc_list = cc_addresses if cc_addresses else None
        
        # Send the reply with proper threading headers (using same account as original)
        return send_email(
            user_id=user_id,
            user_jwt=user_jwt,
            to=to_address,
            subject=reply_subject,
            body=body,
            html_body=html_body,
            cc=cc_list,
            thread_id=thread_id,
            in_reply_to=message_id,
            references=references,
            from_account_id=from_account_id
        )

    except HttpError as e:
        logger.error(f"Gmail API error: {str(e)}")
        raise ValueError(f"Failed to reply to email: {str(e)}")
    except Exception as e:
        logger.error(f"Error replying to email: {str(e)}")
        raise ValueError(f"Failed to reply: {str(e)}")


def forward_email(
    user_id: str,
    user_jwt: str,
    original_email_id: str,
    to: str,
    additional_message: Optional[str] = None,
    cc: Optional[List[str]] = None,
    include_attachments: bool = True
) -> Dict[str, Any]:
    """
    Forward an existing email to new recipients.

    Auto-detects which account to use based on the original email's ext_connection_id.

    Args:
        user_id: User's ID
        user_jwt: User's Supabase JWT for authenticated requests
        original_email_id: ID of the email to forward
        to: Recipient email address
        additional_message: Optional message to prepend to forwarded content
        cc: Optional list of CC recipients
        include_attachments: Whether to include original attachments (default True)

    Returns:
        Dict with forwarded email details
    """
    auth_supabase = get_authenticated_supabase_client(user_jwt)

    # Auto-detect account from original email
    from_account_id = None
    try:
        original_db = auth_supabase.table('emails')\
            .select('ext_connection_id')\
            .eq('external_id', original_email_id)\
            .eq('user_id', user_id)\
            .single()\
            .execute()
        if original_db.data:
            from_account_id = original_db.data.get('ext_connection_id')
            logger.info(f"📧 Auto-detected account for forward: {from_account_id[:8] if from_account_id else 'primary'}...")
    except Exception as e:
        logger.warning(f"Could not auto-detect account for forward: {e}")

    # Get Gmail service (using auto-detected account or default)
    service, connection_id = get_gmail_service(user_id, user_jwt, account_id=from_account_id)
    
    if not service:
        raise ValueError("No active Google connection found for user. Please sign in with Google first.")
    
    try:
        # Get original message
        original = service.users().messages().get(
            userId='me',
            id=original_email_id,
            format='full'
        ).execute()

        headers = parse_email_headers(original['payload']['headers'])
        body = decode_email_body(original['payload'])

        # Build forwarded subject
        original_subject = headers.get('subject', '')
        if not original_subject.lower().startswith('fwd:'):
            forward_subject = f"Fwd: {original_subject}"
        else:
            forward_subject = original_subject

        # Build forwarded body
        forwarded_body = ""
        if additional_message:
            forwarded_body += f"{additional_message}\n\n"

        forwarded_body += "---------- Forwarded message ---------\n"
        forwarded_body += f"From: {headers.get('from', '')}\n"
        forwarded_body += f"Date: {headers.get('date', '')}\n"
        forwarded_body += f"Subject: {original_subject}\n"
        forwarded_body += f"To: {headers.get('to', '')}\n\n"
        forwarded_body += body.get('plain', '')

        # Build HTML body for forwarding (preserve original HTML formatting)
        forwarded_html_body = None
        original_html = body.get('html')
        if original_html:
            forwarded_html_body = ""
            if additional_message:
                # Prepend additional message as HTML paragraph
                escaped_message = additional_message.replace('\n', '<br>')
                forwarded_html_body += f"<p>{escaped_message}</p><br>"
            forwarded_html_body += "<hr><b>---------- Forwarded message ---------</b><br>"
            forwarded_html_body += f"<b>From:</b> {headers.get('from', '')}<br>"
            forwarded_html_body += f"<b>Date:</b> {headers.get('date', '')}<br>"
            forwarded_html_body += f"<b>Subject:</b> {original_subject}<br>"
            forwarded_html_body += f"<b>To:</b> {headers.get('to', '')}<br><br>"
            forwarded_html_body += original_html

        # Fetch original attachments if requested
        attachments = None
        if include_attachments:
            attachment_info = get_attachment_info(original.get('payload', {}))
            if attachment_info:
                attachments = []
                for att in attachment_info:
                    try:
                        att_result = get_email_attachment(
                            user_id=user_id,
                            user_jwt=user_jwt,
                            email_id=original_email_id,
                            attachment_id=att['attachmentId']
                        )
                        att_data = att_result.get('attachment', {})
                        # Convert base64url to standard base64
                        data = att_data.get('data', '')
                        data = data.replace('-', '+').replace('_', '/')
                        # Add padding if needed (base64url often omits padding)
                        padding = len(data) % 4
                        if padding:
                            data += '=' * (4 - padding)
                        attachments.append({
                            'filename': att['filename'],
                            'content': data,
                            'mime_type': att['mimeType']
                        })
                        logger.info(f"📎 Fetched attachment: {att['filename']}")
                    except Exception as att_err:
                        logger.warning(f"⚠️ Failed to fetch attachment {att['filename']}: {att_err}")

        # Send the forward (using same account as original)
        return send_email(
            user_id=user_id,
            user_jwt=user_jwt,
            to=to,
            subject=forward_subject,
            body=forwarded_body,
            html_body=forwarded_html_body,
            cc=cc,
            attachments=attachments,
            from_account_id=from_account_id
        )
        
    except HttpError as e:
        logger.error(f"Gmail API error: {str(e)}")
        raise ValueError(f"Failed to forward email: {str(e)}")
    except Exception as e:
        logger.error(f"Error forwarding email: {str(e)}")
        raise ValueError(f"Failed to forward: {str(e)}")
