# AWS live deployment snapshot

This file records the AWS resources configured through the console on 2026-09-06 in `us-east-1`.

## Email tracker backend

- CloudFormation stack: `internship-email-tracker-backend`
- Lambda function: `InternshipEmailTools`
- Lambda deployment object: `email-tools-v2-1788704386.zip`
- OAuth callback: `https://ik2i54o3q5.execute-api.us-east-1.amazonaws.com/oauth/google/callback`
- Gmail permission: read-only (`gmail.readonly`)
- AgentCore gateway: `internship-email-tools-t0yzltcdzr`

## AgentCore Harnesses

- Email tracker: `internship_email_tracker_v2`, Version 2
- Fit agent: `internship_job_matcher_oss`, Version 11
- Unified orchestrator: `internship_orchestrator_v1`, Version 4
- Orchestrator endpoint: `DEFAULT` (Ready)
- Model: `us.anthropic.claude-sonnet-4-5-20250929-v1:0`
- Gateway invocation policy: `InternshipOrchestratorInvokeGateways`

On 2026-09-07 the Orchestrator was repaired after two verified failures:

1. Its execution role lacked `bedrock-agentcore:InvokeGateway`, producing HTTP 403 when loading the email gateway.
2. Claude Sonnet 4.6 was explicitly denied by the sandbox organization SCP. The Harness was changed to the allowed US cross-region Claude Sonnet 4.5 profile.

After the repairs, the `EMAIL_STATUS` route invoked `get_application_statuses` successfully without rescanning Gmail.

Version 4 adds strict current-date and canonical job-detail validation after a
test correctly exposed stale 2025 listings in search results. A role is no
longer allowed into the ranked list unless its official current/future posting
and open status are verified.

## Production web API

- CloudFormation stack: `simplify-next-web` (`CREATE_COMPLETE`)
- API URL: `https://5y98amhv9k.execute-api.us-east-1.amazonaws.com`
- Health check: `GET /health` verified successfully
- Cognito user pool: `us-east-1_P79tsiDnZ`
- Cognito app client: `52i3ttll1dtm6ojmsekr52kulu`
- Cognito domain: `https://simplify-next-591255906109.auth.us-east-1.amazoncognito.com`
- Request table: `SimplifyNextRequests` with one-day TTL
- API pattern: authenticated asynchronous job submission and polling

## Amplify frontend

- Amplify app: `internship-compass` (`d7ti2jcdjuy09`)
- Branch: `production`
- Live URL: `https://production.d7ti2jcdjuy09.amplifyapp.com`
- Static deployment: verified loading successfully with no browser console errors

The live page is deployed, but authentication is **not yet usable**. The stack
still contains the localhost Cognito callback/logout URL and localhost API CORS
origin, so Cognito currently returns `redirect_mismatch`. The prepared stack
update must set:

- `FrontendCallbackUrl=https://production.d7ti2jcdjuy09.amplifyapp.com/`
- `FrontendLogoutUrl=https://production.d7ti2jcdjuy09.amplifyapp.com/`
- `FrontendOrigin=https://production.d7ti2jcdjuy09.amplifyapp.com`

That final update could not be submitted because the Innovation Sandbox SSO
assignment for role `hack2026_IsbUsersPS` was revoked while the CloudFormation
review page was open. The access portal then showed zero AWS accounts. Restore
the lease/role assignment before updating the stack and running the authenticated
end-to-end test.

## Production identity gate

The deployed email Gateway is still a single-user demo. AWS documents the
Lambda-target client context as Gateway/tool metadata; it does not include the
Harness `runtimeUserId`. Therefore passing a Cognito-derived `runtimeUserId` to
`invoke_harness` does **not**, by itself, securely partition the downstream
email Lambda. Keep `ALLOW_DEMO_USER_ID=true` only for the owner's demo account.
Before adding public users, put email operations behind a trusted user-bound
API/proxy (or an authenticated Gateway flow that injects a verified subject),
remove `demo_user_id` from the public tool schema, and set
`ALLOW_DEMO_USER_ID=false`.

The Orchestrator model and tool bindings are recorded in `agentcore/orchestrator.yaml`; its exact system prompt is in `agentcore/system-prompt.md`.

## Source layout

- `backend/email_tools/` contains the deployed Lambda source.
- `infrastructure/email-tools.yaml` contains its CloudFormation template.
- `agentcore/email-state-tools.json` contains the eight-tool Gateway schema.
- `backend/api/` and `infrastructure/web-app.yaml` contain the authenticated production web API.
- `frontend/` contains the static frontend suitable for AWS Amplify Hosting.

Never commit OAuth client secrets, OAuth tokens, authorization codes, mailbox content, or resume data.
