# Internship Compass

An English-language internship discovery, fit-ranking, and application-tracking product built on Amazon Bedrock AgentCore.

## What works in AWS

- The authenticated API routes requests between the fit Harness and the
  user-bound Gmail workflow.
- Job discovery searches official company career sources, verifies job descriptions, applies hard eligibility gates, and produces transparent rankings.
- Gmail tracking uses read-only OAuth, classifies application updates, stores compact status evidence in DynamoDB, and exports Excel reports to private S3.
- The Orchestrator runs on the US cross-region Claude Sonnet 4.5 inference profile.
- Its `DEFAULT` endpoint is ready in `us-east-1`.

## Repository map

```text
frontend/                     Static responsive web app and local PDF parsing
backend/api/                  Cognito-authenticated orchestrator API
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

For a presentation that must not call AWS or Gmail, open
`http://localhost:5173/?demo=1` or click **Preview demo**. The page clearly marks
all displayed identities, messages, jobs, and scores as sample data and provides
a clickable walkthrough of Gmail connection, synchronization, pipeline, Excel,
and fit-ranking results.

## Production deployment

The current manual Amplify deployment is available at
`https://production.d7ti2jcdjuy09.amplifyapp.com`. Authentication remains
blocked by a pending Cognito callback/CORS update after the sandbox AWS role was
revoked; see `docs/aws-access-recovery.md` for the exact recovery steps.

1. Package `backend/api/lambda_function.py` with the versions in `backend/api/requirements.txt` and upload the ZIP to a private S3 deployment bucket.
2. Deploy `infrastructure/web-app.yaml` in `us-east-1` with that S3 bucket/key.
3. Copy the stack outputs (`ApiUrl`, `CognitoDomain`, and `UserPoolClientId`) into `frontend/config.js`.
4. Connect this repository to AWS Amplify Hosting using `amplify.yml`.
5. Update the stack's `FrontendCallbackUrl`, `FrontendLogoutUrl`, and `FrontendOrigin` with the final Amplify URL.
6. Publish the Google OAuth application and complete verification before admitting non-test Gmail users.

See [production architecture](docs/production-architecture.md) for user isolation and formal Gmail/Outlook onboarding.

Do not onboard additional email users until the updated API and email Lambda
packages are deployed. The source now binds every Gmail operation to the
verified Cognito user in trusted Lambda ClientContext, disables `demo-user`, and
stores new refresh tokens per user. The recovery checklist records the required
deployment and two-user isolation test.

The HTTP API is asynchronous: `POST /chat` returns a request ID immediately and
the browser polls `GET /requests/{requestId}`. This is required because verified
multi-job discovery can run longer than API Gateway's synchronous request window.

Email classification uses the lower-cost Claude Haiku 4.5 US inference profile;
the more capable Sonnet 4.5 Harness is reserved for job discovery and fit
ranking.

## Safety and privacy

- Gmail is read-only. The application never sends, deletes, labels, moves, or marks messages as read.
- Email attachments are never accessed.
- Raw emails and resume files are not stored.
- OAuth secrets and tokens belong in AWS-managed secret storage and must never be committed.
- Fit scores use only explicit resume/profile evidence and verified job requirements.

## Tests

```bash
python3 -m unittest -v tests.test_email_tools tests.test_api_identity
```

The deployed resource names and verified repairs are recorded in `deployment/aws-live-state.md`.
