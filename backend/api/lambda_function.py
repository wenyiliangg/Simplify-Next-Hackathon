"""Authenticated asynchronous API for the Internship Orchestrator frontend.

API Gateway requests return quickly.  The same Lambda invokes itself
asynchronously for the potentially multi-minute AgentCore job and stores only
the result (never resume text) in a short-lived, per-user DynamoDB record.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from typing import Any

import boto3


REGION = os.getenv("AWS_REGION", "us-east-1")
HARNESS_ARN = os.environ["HARNESS_ARN"]
HARNESS_QUALIFIER = os.getenv("HARNESS_QUALIFIER", "DEFAULT")
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "*")
JOB_TABLE = os.environ["JOB_TABLE"]
FUNCTION_NAME = os.environ["AWS_LAMBDA_FUNCTION_NAME"]
MAX_MESSAGE_CHARS = int(os.getenv("MAX_MESSAGE_CHARS", "12000"))
MAX_RESUME_CHARS = int(os.getenv("MAX_RESUME_CHARS", "60000"))
RESULT_TTL_SECONDS = int(os.getenv("RESULT_TTL_SECONDS", "86400"))

_agentcore = boto3.client("bedrock-agentcore", region_name=REGION)
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


def _job_key(user_id: str, request_id: str) -> dict[str, str]:
    return {"pk": f"USER#{user_id}", "sk": f"REQUEST#{request_id}"}


def _run_job(job: dict[str, Any]) -> None:
    user_id, request_id = job["userId"], job["requestId"]
    key = _job_key(user_id, request_id)
    _table.update_item(Key=key, UpdateExpression="SET #s=:s", ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":s": "RUNNING"})
    try:
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
    payload = {"job": {"userId": user_id, "requestId": request_id, "conversationId": conversation_id, "message": message, "resumeText": resume_text}}
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
