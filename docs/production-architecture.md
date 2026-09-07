# Production architecture and multi-user email access

## Request path

1. The static frontend is hosted by AWS Amplify Hosting.
2. A user signs in through an Amazon Cognito User Pool using Authorization Code + PKCE.
3. API Gateway validates the Cognito JWT before invoking `SimplifyNextOrchestratorApi`.
4. The API Lambda derives the user identity only from the verified JWT `sub`; request JSON cannot choose a user ID.
5. `POST /chat` creates a short-lived job record and asynchronously invokes the worker path of the same Lambda, so long career-site verification does not hit API Gateway's synchronous timeout.
6. For fit/discovery, the worker calls the AgentCore Harness `DEFAULT` endpoint
   with a server-derived `runtimeUserId` and user-scoped `runtimeSessionId`.
7. For Gmail tasks, the API acts as the trusted orchestrator: it invokes
   `InternshipEmailTools` directly and places the verified Cognito `sub` only in
   Lambda ClientContext. Public request JSON and model-generated tool arguments
   cannot choose a mailbox identity.
8. Gmail scanning remains read-only. Claude Haiku 4.5 classifies only compact
   message excerpts, after which the API writes normalized events through the
   same user-bound Lambda path. The frontend polls `GET /requests/{requestId}`.

The frontend extracts text from a selected PDF locally with PDF.js. The file itself is not uploaded or retained. The extracted text is included only in the private asynchronous Lambda invocation for a fit request and is capped at 60,000 characters; it is not written to the request table.

## Gmail onboarding

Users do **not** create AWS resources or enter AWS credentials. AWS is configured once by the application owner. Each user:

1. creates/signs into an app account through Cognito;
2. clicks **Connect Gmail**;
3. signs into their own Google account;
4. grants the app read-only Gmail access; and
5. can revoke access later from their Google Account.

The current Google OAuth consent screen is in **Testing** status and the owner's Gmail account is a test user. In that state, only explicitly added Google test users can authorize, and external-testing refresh tokens using Gmail scopes expire after seven days. The repository now contains verified user propagation and disables the `demo-user` fallback, but those packages still need to be deployed after AWS access is restored. Then change the Google OAuth app to Production and complete Google's verification for the restricted `gmail.readonly` scope. Because restricted Gmail data is transmitted through the AWS backend, Google may also require an approved third-party security assessment. The OAuth client ID and redirect URI are app-level configuration; users do not configure them individually.

Never commit the Google client secret or any refresh/access token. The app-level
Google client secret and OAuth state signing secret remain in AWS Secrets
Manager. New refresh tokens are stored in the encrypted DynamoDB table under
that Cognito user's `USER#<sub>/OAUTH#gmail` partition. A compatibility read of
the old secret map is retained only so the owner's existing test connection is
not lost during migration; new callbacks never add tokens to that shared map.

## Security requirements before public launch

- Deploy the repository versions that set `ALLOW_DEMO_USER_ID=false` and remove
  `demo_user_id` from public tool schemas.
- Do not assume `invoke_harness(runtimeUserId=...)` reaches a Gateway Lambda
  target. AWS's documented Lambda target context contains Gateway/tool metadata,
  not that field. This project therefore uses the trusted API Lambda path for
  Gmail and must verify isolation with two users before launch.
- Restrict API CORS to the exact Amplify origin.
- Keep Cognito JWT verification enabled on every private route.
- Add DynamoDB point-in-time recovery, Secrets Manager rotation/monitoring, log retention, request throttling, and deletion/export workflows.
- Add a privacy policy and terms page before Google OAuth verification.
- Do not store raw email bodies or resume files; retain only compact status evidence.
