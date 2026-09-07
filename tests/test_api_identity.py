import base64
import importlib.util
import json
import os
import sys
import types
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]


class FakePayload:
    def __init__(self, value):
        self.value = value

    def read(self):
        return json.dumps(self.value).encode()


class FakeLambda:
    def __init__(self):
        self.calls = []

    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        return {"Payload": FakePayload({"ok": True, "result": {"connected": True}})}


class FakeTable:
    pass


fake_lambda = FakeLambda()
fake_boto3 = types.ModuleType("boto3")
fake_boto3.client = lambda name, **_kwargs: fake_lambda if name == "lambda" else object()
fake_boto3.resource = lambda _name, **_kwargs: types.SimpleNamespace(Table=lambda _table: FakeTable())
sys.modules.setdefault("boto3", fake_boto3)
os.environ.update(
    {
        "HARNESS_ARN": "arn:aws:bedrock-agentcore:us-east-1:123456789012:harness/test",
        "JOB_TABLE": "jobs",
        "AWS_LAMBDA_FUNCTION_NAME": "api",
        "EMAIL_TOOLS_FUNCTION": "email-tools",
    }
)
spec = importlib.util.spec_from_file_location(
    "orchestrator_api", PROJECT_DIR / "backend" / "api" / "lambda_function.py"
)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class ApiIdentityTests(unittest.TestCase):
    def setUp(self):
        fake_lambda.calls.clear()

    def test_explicit_tasks_are_whitelisted(self):
        self.assertEqual(api._task({"task": "EMAIL_SYNC"}), "EMAIL_SYNC")
        with self.assertRaises(ValueError):
            api._task({"task": "DELETE_MAIL"})

    def test_auto_routing_does_not_treat_fit_request_as_email_sync(self):
        task = api._task({"message": "Rank SWE internships. Do not scan email."})
        self.assertEqual(task, "DISCOVER_AND_RANK")

    def test_email_user_id_is_in_trusted_context_not_payload(self):
        result = api._email_tool("cognito-sub-123", "gmail_connection_status")
        self.assertTrue(result["connected"])
        call = fake_lambda.calls[0]
        payload = json.loads(call["Payload"])
        context = json.loads(base64.b64decode(call["ClientContext"]))
        self.assertNotIn("user_id", payload)
        self.assertNotIn("demo_user_id", payload)
        self.assertEqual(context["custom"]["authenticatedUserId"], "cognito-sub-123")

    def test_classifier_json_parser_accepts_fenced_json(self):
        parsed = api._extract_json_object('```json\n{"events": []}\n```')
        self.assertEqual(parsed, {"events": []})

    def test_classifier_uses_compact_message_and_returns_events(self):
        class FakeBedrock:
            def converse(self, **kwargs):
                self.request = kwargs
                return {
                    "output": {
                        "message": {
                            "content": [
                                {
                                    "text": json.dumps(
                                        {
                                            "events": [
                                                {
                                                    "company": "Example",
                                                    "role": "Intern",
                                                    "application_id": "",
                                                    "status": "INTERVIEW",
                                                    "status_at": "2020-01-01T00:00:00Z",
                                                    "provider": "gmail",
                                                    "message_id": "m1",
                                                    "confidence": "HIGH",
                                                    "evidence_summary": "Interview invitation received.",
                                                }
                                            ]
                                        }
                                    )
                                }
                            ]
                        }
                    }
                }

        previous = api._bedrock
        try:
            api._bedrock = FakeBedrock()
            events = api._classify_email_messages(
                [
                    {
                        "message_id": "m1",
                        "received_at": "2026-09-07T00:00:00Z",
                        "subject": "Interview",
                        "body_excerpt": "x" * 5000,
                    }
                ]
            )
            self.assertEqual(events[0]["status"], "INTERVIEW")
            self.assertEqual(events[0]["status_at"], "2026-09-07T00:00:00Z")
            sent = json.loads(api._bedrock.request["messages"][0]["content"][0]["text"])
            self.assertEqual(len(sent[0]["body_excerpt"]), 2500)
        finally:
            api._bedrock = previous


if __name__ == "__main__":
    unittest.main()
