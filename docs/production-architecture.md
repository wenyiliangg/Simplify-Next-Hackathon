# Production architecture and multi-user email access

## Request path

1. The static frontend is hosted by AWS Amplify Hosting.
2. A user signs in through an Amazon Cognito User Pool using Authorization Code + PKCE.
3. API Gateway validates the Cognito JWT before invoking `SimplifyNextOrchestratorApi`.
4. The API Lambda derives the user identity only from the verified JWT `sub`; request JSON cannot choose a user ID.
5. `POST /chat` creates a short-lived job record and asynchronously invokes the worker path of the same Lambda, so long career-site verification does not hit API Gateway's synchronous timeout.
6. The worker calls the AgentCore Harness `DEFAULT` endpoint with both a server-derived `runtimeUserId` and a user-scoped `runtimeSessionId`; the frontend polls `GET /requests/{requestId}`.
7. AgentCore receives the user ID for Harness runtime/session isolation. This
   does not automatically propagate that identity to a Lambda target behind an
   AgentCore Gateway; downstream email identity needs a separate trusted
   propagation mechanism.

The frontend extracts text from a selected PDF locally with PDF.js. The file itself is not uploaded or retained. The extracted text is included only in the private asynchronous Lambda invocation for a fit request and is capped at 60,000 characters; it is not written to the request table.

## Gmail onboarding

Users do **not** create AWS resources or enter AWS credentials. AWS is configured once by the application owner. Each user:

1. creates/signs into an app account through Cognito;
2. clicks **Connect Gmail**;
3. signs into their own Google account;
4. grants the app read-only Gmail access; and
5. can revoke access later from their Google Account.

The current Google OAuth consent screen is in **Testing** status and the owner's Gmail account is a test user. In that state, only explicitly added Google test users can authorize. Before public launch, first replace the deployed `demo-user` fallback with verified user propagation, then change the Google OAuth app to Production and complete Google's verification for the restricted `gmail.readonly` scope. The OAuth client ID and redirect URI are app-level configuration; users do not configure them individually.

Never commit the Google client secret or any refresh/access token. They remain in AWS Secrets Manager. For a larger launch, replace the single JSON secret map with one encrypted secret per user or a KMS-encrypted token table to avoid whole-map write contention.

## Outlook onboarding

The deployed Gateway currently implements Gmail, not Outlook. To add Outlook for formal users, register one multi-tenant Microsoft Entra application, request delegated Microsoft Graph `Mail.Read`, add an AWS callback URL, and store each user's refresh token under the same Cognito user partition. Users then consent individually; they still do not configure AWS.

## Security requirements before public launch

- Set `ALLOW_DEMO_USER_ID=false` on the email-tools Lambda after confirming `runtimeUserId` propagation through the production API.
- Remove `demo_user_id` from public tool schemas.
- Do not assume `invoke_harness(runtimeUserId=...)` reaches a Gateway Lambda
  target. AWS's documented Lambda target context contains Gateway/tool metadata,
  not that field. Use a trusted API/proxy or authenticated identity propagation
  and verify isolation with two users before launch.
- Restrict API CORS to the exact Amplify origin.
- Keep Cognito JWT verification enabled on every private route.
- Add DynamoDB point-in-time recovery, Secrets Manager rotation/monitoring, log retention, request throttling, and deletion/export workflows.
- Add a privacy policy and terms page before Google OAuth verification.
- Do not store raw email bodies or resume files; retain only compact status evidence.
