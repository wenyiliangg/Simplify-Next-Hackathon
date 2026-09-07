"""Authenticated asynchronous API for the Internship Orchestrator frontend.

API Gateway requests return quickly.  The same Lambda invokes itself
asynchronously for the potentially multi-minute AgentCore job and stores only
the result (never resume text) in a short-lived, per-user DynamoDB record.
"""

from __future__ import annotations

import hashlib
import base64
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3


REGION = os.getenv("AWS_REGION", "us-east-1")
HARNESS_ARN = os.environ["HARNESS_ARN"]
HARNESS_QUALIFIER = os.getenv("HARNESS_QUALIFIER", "DEFAULT")
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "*")
JOB_TABLE = os.environ["JOB_TABLE"]
FUNCTION_NAME = os.environ["AWS_LAMBDA_FUNCTION_NAME"]
EMAIL_TOOLS_FUNCTION = os.getenv("EMAIL_TOOLS_FUNCTION", "InternshipEmailTools")
EMAIL_CLASSIFIER_MODEL_ID = os.getenv(
    "EMAIL_CLASSIFIER_MODEL_ID",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
)
MAX_MESSAGE_CHARS = int(os.getenv("MAX_MESSAGE_CHARS", "12000"))
MAX_RESUME_CHARS = int(os.getenv("MAX_RESUME_CHARS", "60000"))
RESULT_TTL_SECONDS = int(os.getenv("RESULT_TTL_SECONDS", "86400"))

_agentcore = boto3.client("bedrock-agentcore", region_name=REGION)
_bedrock = boto3.client("bedrock-runtime", region_name=REGION)
_lambda = boto3.client("lambda", region_name=REGION)
_table = boto3.resource("dynamodb", region_name=REGION).Table(JOB_TABLE)


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json; charset=utf-8",
            "cache-control": "no-store",
            "access-control-allow-origin": ALLOWED_ORIGIN,
            "access-control-allow-headers": "authorization,content-type",
            "access-control-allow-methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(body, ensure_ascii=False),
    }


def _claims(event: dict[str, Any]) -> dict[str, Any]:
    return event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})


def _authenticated_user_id(event: dict[str, Any]) -> str:
    sub = str(_claims(event).get("sub") or "").strip()
    if not sub:
        raise PermissionError("A valid signed-in user is required")
    return sub


def _runtime_session_id(user_id: str, conversation_id: str) -> str:
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "", conversation_id)[:80]
    if len(safe_id) < 8:
        raise ValueError("conversationId must contain at least 8 safe characters")
    return "web-" + hashlib.sha256(f"{user_id}:{safe_id}".encode()).hexdigest()


def _invoke(user_id: str, conversation_id: str, message: str, resume_text: str) -> dict[str, Any]:
    content = message.strip()
    if resume_text:
        content += (
            "\n\nPRIVATE CANDIDATE RESUME TEXT (use only for this user's fit analysis; "
            "do not persist or reveal beyond the requested comparison):\n" + resume_text.strip()
        )
    result = _agentcore.invoke_harness(
        harnessArn=HARNESS_ARN,
        qualifier=HARNESS_QUALIFIER,
        runtimeSessionId=_runtime_session_id(user_id, conversation_id),
        runtimeUserId=user_id,
        actorId=user_id,
        messages=[{"role": "user", "content": [{"text": content}]}],
        timeoutSeconds=300,
    )
    text_parts: list[str] = []
    usage: dict[str, Any] = {}
    metrics: dict[str, Any] = {}
    stop_reason: str | None = None
    for stream_event in result["stream"]:
        if "contentBlockDelta" in stream_event:
            delta = stream_event["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                text_parts.append(delta["text"])
        elif "messageStop" in stream_event:
            stop_reason = stream_event["messageStop"].get("stopReason")
        elif "metadata" in stream_event:
            usage = stream_event["metadata"].get("usage", {})
            metrics = stream_event["metadata"].get("metrics", {})
        elif "validationException" in stream_event:
            raise ValueError(stream_event["validationException"].get("message", "Invalid request"))
        elif "runtimeClientError" in stream_event:
            raise RuntimeError(stream_event["runtimeClientError"].get("message", "Agent runtime error"))
        elif "internalServerException" in stream_event:
            raise RuntimeError(stream_event["internalServerException"].get("message", "AgentCore error"))
    return {
        "message": "".join(text_parts).strip()[:280000],
        "conversationId": conversation_id,
        "stopReason": stop_reason,
        "usage": usage,
        "metrics": metrics,
    }


def _email_tool(user_id: str, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Invoke an email tool with identity supplied only by the trusted API.

    The user ID is deliberately absent from the event/tool arguments. The email
    Lambda reads it from Lambda ClientContext, which callers cannot set through
    the public HTTP API.
    """

    client_context = base64.b64encode(
        json.dumps(
            {
                "custom": {
                    "authenticatedUserId": user_id,
                    "bedrockAgentCoreToolName": f"web-api___{name}",
                }
            },
            separators=(",", ":"),
        ).encode()
    ).decode()
    response = _lambda.invoke(
        FunctionName=EMAIL_TOOLS_FUNCTION,
        InvocationType="RequestResponse",
        ClientContext=client_context,
        Payload=json.dumps({"_tool_name": name, **(payload or {})}).encode(),
    )
    raw = response["Payload"].read()
    result = json.loads(raw or b"{}")
    if response.get("FunctionError") or not result.get("ok"):
        raise RuntimeError(result.get("error") or "Email operation failed")
    return result["result"]


def _extract_json_object(text: str) -> dict[str, Any]:
    fenced = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip(), flags=re.I)
    start, end = fenced.find("{"), fenced.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("The email classifier did not return JSON")
    value = json.loads(fenced[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("The email classifier returned an invalid object")
    return value


def _classify_email_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact = []
    source_dates: dict[str, str] = {}
    for message in messages[:30]:
        message_id = str(message.get("message_id") or "")[:500]
        received_at = str(message.get("received_at") or "")
        if message_id and received_at:
            source_dates[message_id] = received_at
        compact.append(
            {
                "message_id": message_id,
                "received_at": received_at,
                "from": str(message.get("from") or "")[:500],
                "subject": str(message.get("subject") or "")[:500],
                "snippet": str(message.get("snippet") or "")[:700],
                "body_excerpt": str(message.get("body_excerpt") or "")[:2500],
            }
        )
    system = """You classify internship application email updates. Return JSON only as
{"events":[...]}. Include an event only for a genuine, candidate-specific application update;
exclude newsletters, job alerts, marketing, generic recruiter outreach, and unrelated mail.
Each event must contain company, role, application_id (empty if unknown), status, status_at,
provider="gmail", message_id, confidence (HIGH/MEDIUM/LOW), and a short evidence_summary.
Allowed statuses: APPLIED, ASSESSMENT, INTERVIEW, FINAL_INTERVIEW, OFFER, REJECTED,
WITHDRAWN, CLOSED, NEEDS_REVIEW. Never invent missing facts. Use NEEDS_REVIEW when the
message is application-specific but its status is ambiguous. Do not quote full email text."""
    response = _bedrock.converse(
        modelId=EMAIL_CLASSIFIER_MODEL_ID,
        system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": json.dumps(compact, ensure_ascii=False)}]}],
        inferenceConfig={"temperature": 0, "maxTokens": 4000},
    )
    output = "".join(
        block.get("text", "") for block in response.get("output", {}).get("message", {}).get("content", [])
    )
    events = _extract_json_object(output).get("events", [])
    if not isinstance(events, list):
        raise ValueError("The email classifier returned invalid events")
    allowed_statuses = {
        "APPLIED", "ASSESSMENT", "INTERVIEW", "FINAL_INTERVIEW", "OFFER",
        "REJECTED", "WITHDRAWN", "CLOSED", "NEEDS_REVIEW",
    }
    validated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in events[:100]:
        if not isinstance(candidate, dict):
            continue
        message_id = str(candidate.get("message_id") or "")[:500]
        status = str(candidate.get("status") or "").upper()
        if message_id in seen or message_id not in source_dates or status not in allowed_statuses:
            continue
        seen.add(message_id)
        event = dict(candidate)
        # Identity, provider, message ID, and timestamp are trusted source facts,
        # never values the model is permitted to choose.
        event.update(
            {
                "message_id": message_id,
                "status": status,
                "status_at": source_dates[message_id],
                "provider": "gmail",
            }
        )
        validated.append(event)
    return validated


def _orchestrator_envelope(
    task: str,
    summary: str,
    *,
    connected: bool = False,
    sync_status: str = "NOT_REQUESTED",
    messages_examined: int = 0,
    relevant_messages: int = 0,
    statuses: list[dict[str, Any]] | None = None,
    excel: dict[str, Any] | None = None,
) -> str:
    report = excel or {}
    result = {
        "schema_version": "1.0",
        "agent": "internship_orchestrator",
        "request_id": str(uuid.uuid4()),
        "task": task,
        "email_tracker": {
            "connected": connected,
            "sync_status": sync_status,
            "messages_examined": messages_examined,
            "relevant_messages": relevant_messages,
            "last_checkpoint": None,
            "application_statuses": statuses or [],
        },
        "fit_agent": {"candidate_ready": False, "ranked_jobs": [], "needs_verification": [], "ineligible_jobs": []},
        "excel_report": {
            "generated": bool(report.get("generated")),
            "s3_uri": report.get("s3_uri"),
            "download_url": report.get("download_url"),
            "checksum_sha256": report.get("checksum_sha256"),
        },
        "warnings": [],
    }
    return f"{summary}\n\nORCHESTRATOR_RESULT_V1\n{json.dumps(result, ensure_ascii=False)}"


def _run_email_task(user_id: str, task: str) -> dict[str, Any]:
    connection = _email_tool(user_id, "gmail_connection_status")
    connected = bool(connection.get("connected"))
    if task == "CONNECT_GMAIL" or not connected:
        authorization = _email_tool(user_id, "gmail_begin_authorization")
        return {
            "message": _orchestrator_envelope(
                "EMAIL_STATUS",
                "Gmail is not connected. Open this read-only authorization link:\n"
                + str(authorization.get("authorization_url") or ""),
                connected=False,
            )
        }
    if task == "EMAIL_STATUS":
        status_result = _email_tool(user_id, "get_application_statuses")
        statuses = status_result.get("applications", [])
        return {
            "message": _orchestrator_envelope(
                "EMAIL_STATUS",
                f"Loaded {len(statuses)} current application statuses without scanning Gmail.",
                connected=True,
                statuses=statuses,
            )
        }
    if task == "EXPORT_DAILY":
        report = _email_tool(user_id, "export_daily_application_excel", {"timezone": "Asia/Singapore"})
        status_result = _email_tool(user_id, "get_application_statuses")
        statuses = status_result.get("applications", [])
        return {
            "message": _orchestrator_envelope(
                "EXPORT_DAILY",
                f"Created an Excel report containing {len(statuses)} current applications.\n"
                + str(report.get("download_url") or ""),
                connected=True,
                statuses=statuses,
                excel=report,
            )
        }
    scan = _email_tool(user_id, "gmail_scan_application_emails", {"max_results": 30})
    messages = scan.get("messages", [])
    events = _classify_email_messages(messages) if messages else []
    if events:
        _email_tool(user_id, "upsert_application_events", {"events": events})
    synced_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    checkpoint = max((str(item.get("received_at") or "") for item in messages), default=synced_at)
    _email_tool(
        user_id,
        "update_sync_checkpoint",
        {"provider": "gmail", "checkpoint": checkpoint, "synced_at": synced_at},
    )
    status_result = _email_tool(user_id, "get_application_statuses")
    statuses = status_result.get("applications", [])
    return {
        "message": _orchestrator_envelope(
            "EMAIL_SYNC",
            f"Scanned {len(messages)} messages, saved {len(events)} application updates, and loaded {len(statuses)} current statuses.",
            connected=True,
            sync_status="SUCCESS",
            messages_examined=len(messages),
            relevant_messages=len(events),
            statuses=statuses,
        )
    }


def _task(body: dict[str, Any]) -> str:
    explicit = str(body.get("task") or "AUTO").upper()
    allowed = {"AUTO", "DISCOVER_AND_RANK", "EMAIL_STATUS", "EMAIL_SYNC", "EXPORT_DAILY", "CONNECT_GMAIL"}
    if explicit not in allowed:
        raise ValueError("task is invalid")
    if explicit != "AUTO":
        return explicit
    message = str(body.get("message") or "").lower()
    message = re.sub(r"\b(?:do not|don't|without)\s+(?:scan|sync)(?:ning)?\s+(?:my\s+)?(?:gmail|email)\b", "", message)
    if "connect" in message and "gmail" in message:
        return "CONNECT_GMAIL"
    if ("sync" in message or "scan" in message) and ("gmail" in message or "email" in message):
        return "EMAIL_SYNC"
    if "excel" in message or "export" in message:
        return "EXPORT_DAILY"
    if "pipeline" in message or "application status" in message:
        return "EMAIL_STATUS"
    return "DISCOVER_AND_RANK"


def _job_key(user_id: str, request_id: str) -> dict[str, str]:
    return {"pk": f"USER#{user_id}", "sk": f"REQUEST#{request_id}"}


def _run_job(job: dict[str, Any]) -> None:
    user_id, request_id = job["userId"], job["requestId"]
    key = _job_key(user_id, request_id)
    _table.update_item(Key=key, UpdateExpression="SET #s=:s", ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":s": "RUNNING"})
    try:
        if job["task"] in {"EMAIL_STATUS", "EMAIL_SYNC", "EXPORT_DAILY", "CONNECT_GMAIL"}:
            result = _run_email_task(user_id, job["task"])
            result["conversationId"] = job["conversationId"]
        else:
            result = _invoke(user_id, job["conversationId"], job["message"], job.get("resumeText", ""))
        _table.update_item(
            Key=key,
            UpdateExpression="SET #s=:s, result_data=:r, completed_at=:t REMOVE error_message",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "COMPLETED", ":r": result, ":t": int(time.time())},
        )
    except Exception as exc:  # Do not persist request data, credentials, or provider details.
        _table.update_item(
            Key=key,
            UpdateExpression="SET #s=:s, error_message=:e, completed_at=:t",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "FAILED", ":e": f"Agent request failed ({type(exc).__name__})", ":t": int(time.time())},
        )


def _create_job(user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    message = str(body.get("message") or "").strip()
    conversation_id = str(body.get("conversationId") or "").strip()
    resume_text = str(body.get("resumeText") or "").strip()
    task = _task(body)
    if not message:
        raise ValueError("message is required")
    if len(message) > MAX_MESSAGE_CHARS:
        raise ValueError("message is too long")
    if len(resume_text) > MAX_RESUME_CHARS:
        raise ValueError("resume text is too long")
    _runtime_session_id(user_id, conversation_id)
    request_id = str(uuid.uuid4())
    now = int(time.time())
    _table.put_item(Item={**_job_key(user_id, request_id), "request_id": request_id, "status": "QUEUED", "created_at": now, "expires_at": now + RESULT_TTL_SECONDS})
    payload = {"job": {"userId": user_id, "requestId": request_id, "conversationId": conversation_id, "message": message, "resumeText": resume_text, "task": task}}
    _lambda.invoke(FunctionName=FUNCTION_NAME, InvocationType="Event", Payload=json.dumps(payload).encode())
    return {"requestId": request_id, "status": "QUEUED"}


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any] | None:
    if "job" in event:
        _run_job(event["job"])
        return None
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "")
    if method == "OPTIONS":
        return _response(204, {})
    if method == "GET" and path.endswith("/health"):
        return _response(200, {"ok": True, "service": "internship-orchestrator-api"})
    try:
        user_id = _authenticated_user_id(event)
        if method == "POST" and path.endswith("/chat"):
            return _response(202, _create_job(user_id, json.loads(event.get("body") or "{}")))
        match = re.fullmatch(r"/requests/([0-9a-f-]{36})", path)
        if method == "GET" and match:
            item = _table.get_item(Key=_job_key(user_id, match.group(1)), ConsistentRead=True).get("Item")
            if not item:
                return _response(404, {"error": "Request not found"})
            body = {"requestId": item["request_id"], "status": item["status"]}
            if item.get("result_data") is not None:
                body["result"] = item["result_data"]
            if item.get("error_message"):
                body["error"] = item["error_message"]
            return _response(200, body)
        return _response(404, {"error": "Not found"})
    except PermissionError as exc:
        return _response(401, {"error": str(exc)})
    except (ValueError, json.JSONDecodeError) as exc:
        return _response(400, {"error": str(exc)})
    except Exception as exc:
        return _response(502, {"error": f"Request failed ({type(exc).__name__})"})
