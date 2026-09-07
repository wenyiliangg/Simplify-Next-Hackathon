"""State, Gmail read-only, and Excel tools for the email tracking agent.

AgentCore Gateways using AWS_IAM inbound authorization cannot attach a 3LO
target. Gmail therefore uses a small OAuth callback in this Lambda and keeps the
refresh token in Secrets Manager. The Gateway still exposes every operation as
an MCP tool. Attachments and message bodies are never persisted.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import re
import secrets as pysecrets
from datetime import datetime, timezone
from html import unescape
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

try:  # boto3 is present in Lambda; local unit tests exercise pure functions.
    import boto3
    from botocore.exceptions import ClientError
except ImportError:  # pragma: no cover - used only by local unit tests
    boto3 = None

    class ClientError(Exception):
        pass

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.table import Table, TableStyleInfo


TABLE_NAME = os.getenv("APPLICATION_TABLE_NAME", "InternshipEmailTracker")
REPORT_BUCKET = os.getenv("REPORT_BUCKET_NAME", "")
ALLOW_DEMO_USER_ID = os.getenv("ALLOW_DEMO_USER_ID", "false").lower() == "true"
DEFAULT_DEMO_USER_ID = os.getenv("DEFAULT_DEMO_USER_ID", "demo-user")
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "Asia/Singapore")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_OAUTH_SECRET_ID = os.getenv(
    "GOOGLE_OAUTH_SECRET_ID", "internship-email/google-oauth"
)
GOOGLE_OAUTH_REDIRECT_URI = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "")
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_DEFAULT_QUERY = (
    'newer_than:365d {intern internship application interview offer rejection '
    'assessment "coding challenge" "next steps" "regret to inform"}'
)
GMAIL_MAX_BODY_CHARS = 6000
MAX_EVIDENCE_LENGTH = 320

CANONICAL_STATUSES = {
    "APPLIED",
    "ASSESSMENT",
    "INTERVIEW",
    "FINAL_INTERVIEW",
    "OFFER",
    "REJECTED",
    "WITHDRAWN",
    "CLOSED",
    "NEEDS_REVIEW",
}

STATUS_SORT_ORDER = {
    "OFFER": 0,
    "FINAL_INTERVIEW": 1,
    "INTERVIEW": 2,
    "ASSESSMENT": 3,
    "APPLIED": 4,
    "NEEDS_REVIEW": 5,
    "REJECTED": 6,
    "CLOSED": 7,
    "WITHDRAWN": 8,
}

if boto3:
    _dynamodb = boto3.resource("dynamodb")
    _table = _dynamodb.Table(TABLE_NAME)
    _s3 = boto3.client("s3")
    _secretsmanager = boto3.client("secretsmanager")
else:  # pragma: no cover
    _table = None
    _s3 = None
    _secretsmanager = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clean_text(value: Any, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _excel_safe(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    value = _clean_text(value, 1000)
    if value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:48] or "unknown"


def derive_application_key(
    company: str, role: str, application_id: str | None = None
) -> str:
    """Create a stable non-secret application key.

    Separate roles at the same company remain separate. An explicit application
    or requisition ID is preferred when available.
    """

    company_clean = _clean_text(company, 120)
    role_clean = _clean_text(role, 160)
    application_id_clean = _clean_text(application_id, 100)
    identity = application_id_clean or f"{company_clean}|{role_clean}"
    digest = hashlib.sha256(identity.lower().encode("utf-8")).hexdigest()[:14]
    return f"{_slug(company_clean)}:{_slug(role_clean)}:{digest}"


def normalize_event(raw: dict[str, Any]) -> dict[str, Any]:
    company = _clean_text(raw.get("company"), 120)
    role = _clean_text(raw.get("role"), 160)
    status = _clean_text(raw.get("status"), 40).upper()
    provider = _clean_text(raw.get("provider"), 20).lower()
    status_at = _parse_iso(raw.get("status_at")).isoformat().replace("+00:00", "Z")
    message_id = _clean_text(raw.get("message_id"), 500)

    if status not in CANONICAL_STATUSES:
        raise ValueError(f"Unsupported status: {status}")
    if provider not in {"gmail", "outlook"}:
        raise ValueError("provider must be gmail or outlook")
    if not message_id:
        raise ValueError("message_id is required for idempotency")
    if status != "NEEDS_REVIEW" and (not company or not role):
        raise ValueError("company and role are required unless status is NEEDS_REVIEW")

    application_id = _clean_text(raw.get("application_id"), 100) or None
    application_key = _clean_text(raw.get("application_key"), 220) or derive_application_key(
        company or "unknown", role or "unknown", application_id
    )
    return {
        "application_key": application_key,
        "company": company or "Unknown",
        "role": role or "Unknown",
        "application_id": application_id,
        "status": status,
        "status_at": status_at,
        "provider": provider,
        "message_id_hash": hashlib.sha256(message_id.encode("utf-8")).hexdigest(),
        "location": _clean_text(raw.get("location"), 160) or None,
        "confidence": _clean_text(raw.get("confidence"), 20).upper() or "LOW",
        "evidence_summary": _clean_text(
            raw.get("evidence_summary"), MAX_EVIDENCE_LENGTH
        ),
    }


def reduce_events(
    events: Iterable[dict[str, Any]], as_of: str | None = None
) -> list[dict[str, Any]]:
    """Return the latest event per application at or before ``as_of``."""

    cutoff = _parse_iso(as_of) if as_of else None
    latest: dict[str, dict[str, Any]] = {}
    for raw in events:
        event = dict(raw)
        when = _parse_iso(event.get("status_at"))
        if cutoff and when > cutoff:
            continue
        key = event["application_key"]
        previous = latest.get(key)
        if not previous or when > _parse_iso(previous.get("status_at")):
            latest[key] = event

    rows = list(latest.values())
    rows.sort(
        key=lambda item: (
            STATUS_SORT_ORDER.get(item.get("status", "NEEDS_REVIEW"), 99),
            -_parse_iso(item.get("status_at")).timestamp(),
            item.get("company", "").lower(),
            item.get("role", "").lower(),
        )
    )
    return rows


def changes_for_local_date(
    events: Iterable[dict[str, Any]], report_date: str, timezone_name: str
) -> list[dict[str, Any]]:
    tz = ZoneInfo(timezone_name)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        grouped.setdefault(event["application_key"], []).append(dict(event))

    changes: list[dict[str, Any]] = []
    for app_events in grouped.values():
        app_events.sort(key=lambda item: _parse_iso(item.get("status_at")))
        previous_status: str | None = None
        for event in app_events:
            local_date = _parse_iso(event["status_at"]).astimezone(tz).date().isoformat()
            if local_date == report_date and event["status"] != previous_status:
                changes.append(
                    {
                        "company": event.get("company"),
                        "role": event.get("role"),
                        "previous_status": previous_status,
                        "new_status": event.get("status"),
                        "changed_at": event.get("status_at"),
                        "provider": event.get("provider"),
                    }
                )
            previous_status = event["status"]
    changes.sort(key=lambda item: item["changed_at"], reverse=True)
    return changes


def _add_table(ws, name: str) -> None:
    if ws.max_row < 2 or ws.max_column < 1:
        return
    table = Table(displayName=name, ref=ws.dimensions)
    table.tableStyleInfo = TableStyleInfo(
        name="TableStyleMedium2",
        showFirstColumn=False,
        showLastColumn=False,
        showRowStripes=True,
        showColumnStripes=False,
    )
    ws.add_table(table)


def _format_sheet(ws, table_name: str | None = None) -> None:
    ws.freeze_panes = "A2"
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(vertical="center")
    for column_cells in ws.columns:
        values = [str(cell.value or "") for cell in column_cells]
        width = min(max(max(map(len, values)) + 2, 12), 45)
        ws.column_dimensions[column_cells[0].column_letter].width = width
    if table_name:
        _add_table(ws, table_name)


def build_workbook(
    applications: list[dict[str, Any]],
    events: list[dict[str, Any]],
    *,
    generated_at: str,
    timezone_name: str,
    provider_status: dict[str, Any] | None = None,
) -> bytes:
    generated = _parse_iso(generated_at)
    report_date = generated.astimezone(ZoneInfo(timezone_name)).date().isoformat()
    provider_status = provider_status or {}
    changes = changes_for_local_date(events, report_date, timezone_name)

    workbook = Workbook()
    summary = workbook.active
    summary.title = "Summary"
    summary.append(["Field", "Value"])
    summary_rows = [
        ("Report date", report_date),
        ("Generated at", generated_at),
        ("Timezone", timezone_name),
        ("Gmail connection", provider_status.get("gmail", "UNKNOWN")),
        ("Outlook connection", provider_status.get("outlook", "UNKNOWN")),
        ("Total applications", len(applications)),
        ("Needs review", sum(a.get("status") == "NEEDS_REVIEW" for a in applications)),
    ]
    for status in sorted(CANONICAL_STATUSES, key=lambda s: STATUS_SORT_ORDER.get(s, 99)):
        summary_rows.append(
            (f"Status: {status}", sum(a.get("status") == status for a in applications))
        )
    for row in summary_rows:
        summary.append([_excel_safe(value) for value in row])
    _format_sheet(summary, "SummaryTable")

    application_sheet = workbook.create_sheet("Applications")
    application_headers = [
        "Company",
        "Role",
        "Requisition/Application ID",
        "Current Status",
        "Status Date",
        "First Applied Date",
        "Last Relevant Email",
        "Provider",
        "Location",
        "Confidence",
        "Evidence Summary",
        "Application Key",
    ]
    application_sheet.append(application_headers)
    for app in applications:
        application_sheet.append(
            [
                _excel_safe(app.get("company")),
                _excel_safe(app.get("role")),
                _excel_safe(app.get("application_id")),
                _excel_safe(app.get("status")),
                _excel_safe(app.get("status_at")),
                _excel_safe(app.get("first_applied_at")),
                _excel_safe(app.get("last_relevant_email_at") or app.get("status_at")),
                _excel_safe(app.get("provider")),
                _excel_safe(app.get("location")),
                _excel_safe(app.get("confidence")),
                _excel_safe(app.get("evidence_summary")),
                _excel_safe(app.get("application_key")),
            ]
        )
    _format_sheet(application_sheet, "ApplicationsTable")

    changes_sheet = workbook.create_sheet("Changes_Today")
    changes_sheet.append(
        ["Company", "Role", "Previous Status", "New Status", "Change Timestamp", "Provider"]
    )
    for change in changes:
        changes_sheet.append(
            [
                _excel_safe(change.get("company")),
                _excel_safe(change.get("role")),
                _excel_safe(change.get("previous_status") or "NONE"),
                _excel_safe(change.get("new_status")),
                _excel_safe(change.get("changed_at")),
                _excel_safe(change.get("provider")),
            ]
        )
    _format_sheet(changes_sheet, "ChangesTodayTable")

    review_sheet = workbook.create_sheet("Needs_Review")
    review_sheet.append(
        ["Company", "Role", "Status Date", "Provider", "Confidence", "Review Reason"]
    )
    for app in applications:
        if app.get("status") == "NEEDS_REVIEW":
            review_sheet.append(
                [
                    _excel_safe(app.get("company")),
                    _excel_safe(app.get("role")),
                    _excel_safe(app.get("status_at")),
                    _excel_safe(app.get("provider")),
                    _excel_safe(app.get("confidence")),
                    _excel_safe(app.get("evidence_summary")),
                ]
            )
    _format_sheet(review_sheet, "NeedsReviewTable")

    buffer = io.BytesIO()
    workbook.save(buffer)
    data = buffer.getvalue()
    # Fail before upload if the generated artifact cannot be reopened.
    load_workbook(io.BytesIO(data), read_only=True)
    return data


def _context_custom(context: Any) -> dict[str, Any]:
    try:
        return dict(context.client_context.custom or {})
    except (AttributeError, TypeError):
        return {}


def _resolve_user_id(event: dict[str, Any], context: Any) -> str:
    custom = _context_custom(context)
    for key in ("authenticatedUserId", "bedrockAgentCoreUserId", "userId", "sub"):
        if custom.get(key):
            return _clean_text(custom[key], 180)
    if ALLOW_DEMO_USER_ID:
        return _clean_text(event.get("demo_user_id") or DEFAULT_DEMO_USER_ID, 180)
    raise PermissionError("No authenticated user identity is available")


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _gmail_secret() -> dict[str, Any]:
    if _secretsmanager is None:  # pragma: no cover
        raise RuntimeError("Secrets Manager is not configured")
    response = _secretsmanager.get_secret_value(SecretId=GOOGLE_OAUTH_SECRET_ID)
    try:
        value = json.loads(response.get("SecretString") or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("The Google OAuth secret is not valid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("The Google OAuth secret must contain a JSON object")
    return value


def _put_gmail_secret(value: dict[str, Any]) -> None:
    if _secretsmanager is None:  # pragma: no cover
        raise RuntimeError("Secrets Manager is not configured")
    _secretsmanager.put_secret_value(
        SecretId=GOOGLE_OAUTH_SECRET_ID,
        SecretString=json.dumps(value, separators=(",", ":")),
    )


def _state_token(user_id: str, secret: dict[str, Any]) -> str:
    state_secret = secret.get("state_secret")
    if not state_secret:
        state_secret = pysecrets.token_urlsafe(32)
        secret["state_secret"] = state_secret
        _put_gmail_secret(secret)
    payload = _b64url_encode(
        json.dumps(
            {
                "user_id": user_id,
                "exp": int(datetime.now(timezone.utc).timestamp()) + 900,
                "nonce": pysecrets.token_urlsafe(12),
            },
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signature = _b64url_encode(
        hmac.new(state_secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{payload}.{signature}"


def _verify_state(state: str, secret: dict[str, Any]) -> str:
    try:
        payload, supplied_signature = state.split(".", 1)
        state_secret = secret["state_secret"]
        expected_signature = _b64url_encode(
            hmac.new(
                state_secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256
            ).digest()
        )
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise ValueError("Invalid OAuth state signature")
        decoded = json.loads(_b64url_decode(payload))
        if int(decoded["exp"]) < int(datetime.now(timezone.utc).timestamp()):
            raise ValueError("OAuth state has expired")
        return _clean_text(decoded["user_id"], 180)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PermissionError("Invalid or expired OAuth state") from exc


def _http_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    form: dict[str, str] | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    body = urlencode(form).encode("utf-8") if form is not None else None
    request_headers = dict(headers or {})
    if form is not None:
        request_headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = Request(url, data=body, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        # Do not return upstream response bodies because they may contain OAuth data.
        raise RuntimeError(f"OAuth or Gmail API request failed with HTTP {exc.code}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError("OAuth or Gmail API request could not be completed") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("OAuth or Gmail API returned an unexpected response")
    return payload


def gmail_begin_authorization(event: dict[str, Any], context: Any) -> dict[str, Any]:
    user_id = _resolve_user_id(event, context)
    if not GOOGLE_CLIENT_ID or not GOOGLE_OAUTH_REDIRECT_URI:
        raise RuntimeError("Google OAuth is not configured")
    secret = _gmail_secret()
    state = _state_token(user_id, secret)
    authorization_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
        {
            "client_id": GOOGLE_CLIENT_ID,
            "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
            "response_type": "code",
            "scope": GMAIL_READONLY_SCOPE,
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
            "state": state,
        }
    )
    return {
        "connected": False,
        "authorization_required": True,
        "authorization_url": authorization_url,
        "expires_in_seconds": 900,
        "scope": GMAIL_READONLY_SCOPE,
        "instructions": "Open the URL and approve read-only Gmail access, then retry the sync.",
    }


def gmail_connection_status(event: dict[str, Any], context: Any) -> dict[str, Any]:
    user_id = _resolve_user_id(event, context)
    token_item = _gmail_token_item(user_id)
    secret = _gmail_secret()
    # Legacy secret-map fallback preserves the already connected demo account
    # during migration. New authorizations are stored per user in DynamoDB.
    connected = bool(token_item.get("refresh_token") or (secret.get("refresh_tokens") or {}).get(user_id))
    return {
        "provider": "gmail",
        "connected": connected,
        "scope": GMAIL_READONLY_SCOPE,
        "email": token_item.get("email") or (secret.get("emails") or {}).get(user_id),
    }


def _gmail_token_item(user_id: str) -> dict[str, Any]:
    if _table is None:  # pragma: no cover
        raise RuntimeError("DynamoDB is not configured")
    return _table.get_item(
        Key={"pk": f"USER#{user_id}", "sk": "OAUTH#gmail"},
        ConsistentRead=True,
    ).get("Item") or {}


def _gmail_access_token(user_id: str) -> str:
    secret = _gmail_secret()
    refresh_token = _gmail_token_item(user_id).get("refresh_token")
    if not refresh_token:  # One-time compatibility for the existing owner account.
        refresh_token = (secret.get("refresh_tokens") or {}).get(user_id)
    if not refresh_token:
        raise PermissionError("Gmail is not connected. Run gmail_begin_authorization first.")
    token = _http_json(
        "https://oauth2.googleapis.com/token",
        method="POST",
        form={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": secret.get("client_secret", ""),
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    access_token = token.get("access_token")
    if not access_token:
        raise RuntimeError("Google did not return an access token")
    return str(access_token)


def _gmail_api(path: str, access_token: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"https://gmail.googleapis.com/gmail/v1/{path.lstrip('/')}"
    if params:
        url += "?" + urlencode({k: v for k, v in params.items() if v is not None})
    return _http_json(url, headers={"Authorization": f"Bearer {access_token}"})


def _gmail_body(payload: dict[str, Any]) -> str:
    plain: list[str] = []
    html: list[str] = []

    def visit(part: dict[str, Any]) -> None:
        # A filename or attachmentId identifies attachment content. Never fetch it.
        body = part.get("body") or {}
        if part.get("filename") or body.get("attachmentId"):
            return
        data = body.get("data")
        mime_type = part.get("mimeType") or ""
        if data and mime_type in {"text/plain", "text/html"}:
            try:
                decoded = _b64url_decode(str(data)).decode("utf-8", errors="replace")
            except (ValueError, UnicodeError):
                decoded = ""
            (plain if mime_type == "text/plain" else html).append(decoded)
        for child in part.get("parts") or []:
            if isinstance(child, dict):
                visit(child)

    visit(payload or {})
    text = "\n".join(plain)
    if not text and html:
        text = re.sub(r"<[^>]+>", " ", "\n".join(html))
        text = unescape(text)
    # Remove long quoted history and keep only a compact analysis excerpt.
    text = re.split(r"\nOn .{0,200} wrote:\s*\n|\nFrom:\s", text, maxsplit=1)[0]
    return _clean_text(text, GMAIL_MAX_BODY_CHARS)


def _compact_gmail_message(message: dict[str, Any]) -> dict[str, Any]:
    payload = message.get("payload") or {}
    headers = {
        str(item.get("name", "")).lower(): _clean_text(item.get("value"), 500)
        for item in payload.get("headers") or []
        if isinstance(item, dict)
    }
    internal_date = message.get("internalDate")
    received_at = None
    if internal_date:
        received_at = datetime.fromtimestamp(
            int(internal_date) / 1000, timezone.utc
        ).isoformat().replace("+00:00", "Z")
    return {
        "message_id": _clean_text(message.get("id"), 500),
        "thread_id": _clean_text(message.get("threadId"), 500),
        "received_at": received_at,
        "from": headers.get("from"),
        "to": headers.get("to"),
        "subject": headers.get("subject"),
        "snippet": _clean_text(message.get("snippet"), 500),
        "body_excerpt": _gmail_body(payload),
    }


def gmail_scan_application_emails(event: dict[str, Any], context: Any) -> dict[str, Any]:
    user_id = _resolve_user_id(event, context)
    query = _clean_text(event.get("query") or GMAIL_DEFAULT_QUERY, 1000)
    max_results = max(1, min(int(event.get("max_results") or 20), 30))
    access_token = _gmail_access_token(user_id)
    listing = _gmail_api(
        "users/me/messages",
        access_token,
        {"q": query, "maxResults": max_results, "includeSpamTrash": "false"},
    )
    messages: list[dict[str, Any]] = []
    for item in listing.get("messages") or []:
        message_id = item.get("id") if isinstance(item, dict) else None
        if not message_id:
            continue
        full = _gmail_api(
            f"users/me/messages/{message_id}",
            access_token,
            {"format": "full"},
        )
        messages.append(_compact_gmail_message(full))
    messages.sort(key=lambda item: item.get("received_at") or "")
    return {
        "provider": "gmail",
        "query": query,
        "messages_examined": len(messages),
        "messages": messages,
        "next_page_token": listing.get("nextPageToken"),
        "privacy_note": "Attachments were not fetched. Message bodies are not persisted by this tool.",
    }


def _oauth_callback(event: dict[str, Any]) -> dict[str, Any]:
    params = event.get("queryStringParameters") or {}
    if params.get("error"):
        message = "Gmail authorization was cancelled or denied. You may close this page."
        return _html_response(400, message)
    code = params.get("code")
    state = params.get("state")
    if not code or not state:
        return _html_response(400, "Missing OAuth response parameters. You may close this page.")
    secret = _gmail_secret()
    user_id = _verify_state(str(state), secret)
    token = _http_json(
        "https://oauth2.googleapis.com/token",
        method="POST",
        form={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": secret.get("client_secret", ""),
            "code": str(code),
            "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
            "grant_type": "authorization_code",
        },
    )
    refresh_token = token.get("refresh_token")
    access_token = token.get("access_token")
    if not refresh_token:
        return _html_response(
            400,
            "Google did not return an offline token. Return to the agent and start authorization again.",
        )
    connected_email = None
    if access_token:
        profile = _gmail_api("users/me/profile", str(access_token))
        connected_email = _clean_text(profile.get("emailAddress"), 320)
    if _table is not None:
        _table.put_item(
            Item={
                "pk": f"USER#{user_id}",
                "sk": "OAUTH#gmail",
                "record_type": "OAUTH_TOKEN",
                "provider": "gmail",
                "refresh_token": str(refresh_token),
                "email": connected_email,
                "scope": GMAIL_READONLY_SCOPE,
                "updated_at": _now_iso(),
            }
        )
        _table.put_item(
            Item={
                "pk": f"USER#{user_id}",
                "sk": "CHECKPOINT#gmail",
                "record_type": "CHECKPOINT",
                "provider": "gmail",
                "checkpoint": "authorized",
                "last_successful_sync": None,
                "connection_status": "CONNECTED",
            }
        )
    return _html_response(
        200,
        "Gmail is connected with read-only access. Return to the Internship Email Tracker and run the sync.",
    )


def _html_response(status_code: int, message: str) -> dict[str, Any]:
    safe_message = re.sub(r"[<>]", "", message)
    body = (
        "<!doctype html><html lang='en'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Internship Email Tracker</title>"
        "<body style='font-family:system-ui;max-width:640px;margin:80px auto;padding:24px'>"
        f"<h1>Internship Email Tracker</h1><p>{safe_message}</p></body></html>"
    )
    return {
        "statusCode": status_code,
        "headers": {
            "content-type": "text/html; charset=utf-8",
            "cache-control": "no-store",
            "x-content-type-options": "nosniff",
        },
        "body": body,
    }


def _query_user_items(user_id: str) -> list[dict[str, Any]]:
    if _table is None:  # pragma: no cover
        raise RuntimeError("DynamoDB is not configured")
    key = f"USER#{user_id}"
    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": "pk = :pk",
        "ExpressionAttributeValues": {":pk": key},
    }
    while True:
        response = _table.query(**kwargs)
        items.extend(response.get("Items", []))
        if not response.get("LastEvaluatedKey"):
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    return items


def upsert_application_events(event: dict[str, Any], context: Any) -> dict[str, Any]:
    user_id = _resolve_user_id(event, context)
    raw_events = event.get("events") or []
    if not isinstance(raw_events, list) or not raw_events:
        raise ValueError("events must be a non-empty array")

    inserted = updated = duplicates = rejected = older_events = 0
    errors: list[str] = []
    for raw in raw_events[:100]:
        try:
            normalized = normalize_event(raw)
            message_hash = normalized["message_id_hash"]
            event_item = {
                "pk": f"USER#{user_id}",
                "sk": f"EVENT#{normalized['provider']}#{message_hash}",
                "record_type": "EVENT",
                **normalized,
                "created_at": _now_iso(),
            }
            _table.put_item(
                Item=event_item,
                ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)",
            )
            inserted += 1
            app_item = {
                "pk": f"USER#{user_id}",
                "sk": f"APP#{normalized['application_key']}",
                "record_type": "APPLICATION",
                **{k: v for k, v in normalized.items() if k != "message_id_hash"},
                "last_relevant_email_at": normalized["status_at"],
                "updated_at": _now_iso(),
            }
            try:
                _table.put_item(
                    Item=app_item,
                    ConditionExpression="attribute_not_exists(status_at) OR status_at <= :incoming",
                    ExpressionAttributeValues={":incoming": normalized["status_at"]},
                )
                updated += 1
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                    older_events += 1
                else:
                    raise
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                duplicates += 1
            else:
                rejected += 1
                errors.append(_clean_text(exc, 200))
        except (TypeError, ValueError) as exc:
            rejected += 1
            errors.append(_clean_text(exc, 200))

    return {
        "user_id": user_id,
        "inserted": inserted,
        "current_status_updated": updated,
        "duplicates": duplicates,
        "older_events_preserved": older_events,
        "rejected": rejected,
        "errors": errors[:10],
    }


def get_application_statuses(event: dict[str, Any], context: Any) -> dict[str, Any]:
    user_id = _resolve_user_id(event, context)
    as_of = event.get("as_of") or _now_iso()
    items = _query_user_items(user_id)
    events = [item for item in items if item.get("record_type") == "EVENT"]
    applications = reduce_events(events, as_of)
    return {
        "user_id": user_id,
        "as_of": as_of,
        "application_count": len(applications),
        "applications": applications,
    }


def export_daily_application_excel(event: dict[str, Any], context: Any) -> dict[str, Any]:
    user_id = _resolve_user_id(event, context)
    as_of = event.get("as_of") or _now_iso()
    timezone_name = event.get("timezone") or DEFAULT_TIMEZONE
    ZoneInfo(timezone_name)  # Validate before reading or writing data.
    items = _query_user_items(user_id)
    events = [item for item in items if item.get("record_type") == "EVENT"]
    applications = reduce_events(events, as_of)
    provider_status = {
        item.get("provider"): item.get("connection_status", "UNKNOWN")
        for item in items
        if item.get("record_type") == "CHECKPOINT"
    }
    workbook_bytes = build_workbook(
        applications,
        events,
        generated_at=as_of,
        timezone_name=timezone_name,
        provider_status=provider_status,
    )
    report_date = _parse_iso(as_of).astimezone(ZoneInfo(timezone_name)).date().isoformat()
    checksum = hashlib.sha256(workbook_bytes).hexdigest()
    object_key = (
        f"reports/{_slug(user_id)}/{report_date}/"
        f"internship-application-status-{report_date}.xlsx"
    )
    _s3.put_object(
        Bucket=REPORT_BUCKET,
        Key=object_key,
        Body=workbook_bytes,
        ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ServerSideEncryption="AES256",
        Metadata={"sha256": checksum, "report-date": report_date},
    )
    download_url = _s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": REPORT_BUCKET, "Key": object_key},
        ExpiresIn=900,
    )
    expires_at = datetime.fromtimestamp(
        datetime.now(timezone.utc).timestamp() + 900, timezone.utc
    ).isoformat().replace("+00:00", "Z")
    _table.put_item(
        Item={
            "pk": f"USER#{user_id}",
            "sk": f"REPORT#{report_date}",
            "record_type": "REPORT",
            "report_date": report_date,
            "generated_at": as_of,
            "row_count": len(applications),
            "s3_uri": f"s3://{REPORT_BUCKET}/{object_key}",
            "checksum_sha256": checksum,
        }
    )
    return {
        "generated": True,
        "report_date": report_date,
        "row_count": len(applications),
        "s3_uri": f"s3://{REPORT_BUCKET}/{object_key}",
        "download_url": download_url,
        "expires_at": expires_at,
        "checksum_sha256": checksum,
    }


def get_sync_checkpoint(event: dict[str, Any], context: Any) -> dict[str, Any]:
    user_id = _resolve_user_id(event, context)
    provider = _clean_text(event.get("provider"), 20).lower()
    if provider not in {"gmail", "outlook"}:
        raise ValueError("provider must be gmail or outlook")
    response = _table.get_item(
        Key={"pk": f"USER#{user_id}", "sk": f"CHECKPOINT#{provider}"}
    )
    item = response.get("Item")
    return {
        "user_id": user_id,
        "provider": provider,
        "found": bool(item),
        "checkpoint": item.get("checkpoint") if item else None,
        "last_successful_sync": item.get("last_successful_sync") if item else None,
        "connection_status": item.get("connection_status", "UNKNOWN") if item else "UNKNOWN",
    }


def update_sync_checkpoint(event: dict[str, Any], context: Any) -> dict[str, Any]:
    user_id = _resolve_user_id(event, context)
    provider = _clean_text(event.get("provider"), 20).lower()
    if provider not in {"gmail", "outlook"}:
        raise ValueError("provider must be gmail or outlook")
    checkpoint = _clean_text(event.get("checkpoint"), 500)
    if not checkpoint:
        raise ValueError("checkpoint is required")
    synced_at = event.get("synced_at") or _now_iso()
    _parse_iso(synced_at)
    _table.put_item(
        Item={
            "pk": f"USER#{user_id}",
            "sk": f"CHECKPOINT#{provider}",
            "record_type": "CHECKPOINT",
            "provider": provider,
            "checkpoint": checkpoint,
            "last_successful_sync": synced_at,
            "connection_status": "CONNECTED",
        }
    )
    return {
        "updated": True,
        "provider": provider,
        "checkpoint": checkpoint,
        "last_successful_sync": synced_at,
    }


def _tool_name(event: dict[str, Any], context: Any) -> str:
    direct = event.get("_tool_name") or event.get("tool_name")
    if direct:
        return str(direct).split("___")[-1]
    original = _context_custom(context).get("bedrockAgentCoreToolName", "")
    return str(original).split("___")[-1]


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Dispatch both AgentCore Gateway calls and Scheduler invocations."""

    event = dict(event or {})
    raw_path = event.get("rawPath") or event.get("path")
    if raw_path == "/oauth/google/callback":
        try:
            return _oauth_callback(event)
        except Exception as exc:
            return _html_response(
                500,
                f"Gmail authorization could not be completed ({type(exc).__name__}). Return to the agent and try again.",
            )
    name = _tool_name(event, context)
    handlers = {
        "gmail_begin_authorization": gmail_begin_authorization,
        "gmail_connection_status": gmail_connection_status,
        "gmail_scan_application_emails": gmail_scan_application_emails,
        "upsert_application_events": upsert_application_events,
        "get_application_statuses": get_application_statuses,
        "export_daily_application_excel": export_daily_application_excel,
        "scheduled_daily_export": export_daily_application_excel,
        "get_sync_checkpoint": get_sync_checkpoint,
        "update_sync_checkpoint": update_sync_checkpoint,
    }
    if name not in handlers:
        return {"ok": False, "error": f"Unknown tool: {name or 'missing'}"}
    try:
        result = handlers[name](event, context)
        return {"ok": True, "tool": name, "result": result}
    except Exception as exc:  # Return a compact error; never serialize request data.
        return {
            "ok": False,
            "tool": name,
            "error_type": type(exc).__name__,
            "error": _clean_text(exc, 240),
        }


if __name__ == "__main__":  # Local smoke check; does not contact AWS.
    sample = normalize_event(
        {
            "company": "Example Corp",
            "role": "Software Engineer Intern",
            "status": "INTERVIEW",
            "status_at": "2026-09-06T04:00:00Z",
            "provider": "gmail",
            "message_id": "fixture-1",
            "confidence": "HIGH",
            "evidence_summary": "Interview invitation received.",
        }
    )
    print(json.dumps(sample, indent=2))
