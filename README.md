# Internship Compass

An English-language internship discovery, fit-ranking, and application-tracking product built on Amazon Bedrock AgentCore.

## What works in AWS

- `internship_orchestrator_v1` routes requests between two specialist toolsets.
- Job discovery searches official company career sources, verifies job descriptions, applies hard eligibility gates, and produces transparent rankings.
- Gmail tracking uses read-only OAuth, classifies application updates, stores compact status evidence in DynamoDB, and exports Excel reports to private S3.
- The Orchestrator runs on the US cross-region Claude Sonnet 4.5 inference profile.
- Its `DEFAULT` endpoint is ready in `us-east-1`.

## Repository map

```text
frontend/                     Static responsive web app and local PDF parsing
backend/api/                  Cognito-authenticated API → AgentCore Harness
backend/email_tools/          Gmail/state/Excel Lambda used by AgentCore Gateway
infrastructure/web-app.yaml   Cognito, HTTP API, and web API Lambda
infrastructure/email-tools.yaml
agentcore/                    Harness prompts and Gateway contracts
deployment/                   Verified AWS resource snapshot
docs/                         Production identity and OAuth guidance
tests/                        Email tool tests and synthetic fixtures
```

## Local frontend preview

```bash
cd frontend
python3 -m http.server 5173
```

Open `http://localhost:5173`. Authentication and chat require deployed values in `frontend/config.js`; the page and PDF parser can still be previewed before that.

## Production deployment

1. Package `backend/api/lambda_function.py` with the versions in `backend/api/requirements.txt` and upload the ZIP to a private S3 deployment bucket.
2. Deploy `infrastructure/web-app.yaml` in `us-east-1` with that S3 bucket/key.
3. Copy the stack outputs (`ApiUrl`, `CognitoDomain`, and `UserPoolClientId`) into `frontend/config.js`.
4. Connect this repository to AWS Amplify Hosting using `amplify.yml`.
5. Update the stack's `FrontendCallbackUrl`, `FrontendLogoutUrl`, and `FrontendOrigin` with the final Amplify URL.
6. Publish the Google OAuth application and complete verification before admitting non-test Gmail users.

See [production architecture](docs/production-architecture.md) for user isolation and formal Gmail/Outlook onboarding.

The HTTP API is asynchronous: `POST /chat` returns a request ID immediately and
the browser polls `GET /requests/{requestId}`. This is required because verified
multi-job discovery can run longer than API Gateway's synchronous request window.

## Safety and privacy

- Gmail is read-only. The application never sends, deletes, labels, moves, or marks messages as read.
- Email attachments are never accessed.
- Raw emails and resume files are not stored.
- OAuth secrets and tokens belong in AWS-managed secret storage and must never be committed.
- Fit scores use only explicit resume/profile evidence and verified job requirements.

## Tests

```bash
python3 -m pytest tests/test_email_tools.py
```

The deployed resource names and verified repairs are recorded in `deployment/aws-live-state.md`.
