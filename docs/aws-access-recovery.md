# AWS access recovery and final verification

The application is not fully production-ready until every item below passes.

## 1. Restore the sandbox role

The account was frozen after exceeding the lease budget. Ask a Sandbox Manager
or Administrator to increase the maximum budget for the existing lease first,
then choose **Unfreeze**. Simply unfreezing without raising the threshold will
cause the monitor to freeze it again. If the lease is already terminated, it
cannot be extended; request a new lease instead.

Restore the lease/assignment for account `591255906109` and role
`hack2026_IsbUsersPS`. Confirm that the AWS access portal lists the account and
that the console opens in `us-east-1`.

## 2. Finish the web stack update

Open CloudFormation stack `simplify-next-web`, choose **Update**, keep the
current template, and set:

```text
FrontendCallbackUrl = https://production.d7ti2jcdjuy09.amplifyapp.com/
FrontendLogoutUrl   = https://production.d7ti2jcdjuy09.amplifyapp.com/
FrontendOrigin      = https://production.d7ti2jcdjuy09.amplifyapp.com
```

Submit the update and wait for `UPDATE_COMPLETE`.

## 3. Verify the fit route

Use the live frontend, create a Cognito account, upload a text-based PDF resume,
and request at least five current internships. Verify that Version 4 returns
only official, reachable, current/future job-detail pages. A past deadline,
closed role, generic careers/search page, or unverifiable opening must never
appear in `ranked_jobs`.

## 4. Deploy and verify multi-user Gmail isolation

Deploy the updated web API and email-tools packages from this repository. The
web API derives the user from the verified Cognito JWT, invokes the email Lambda
directly with that identity in trusted Lambda ClientContext, and never accepts a
mailbox user ID in public JSON. The updated email Lambda disables the demo
fallback, and the public schema no longer contains `demo_user_id`.

New Gmail refresh tokens are written to each user's encrypted DynamoDB
partition instead of a shared JSON token map. The existing owner's old token is
read only as a migration fallback.

After deployment, test with two separate Cognito users and two separate Gmail
test accounts. Each user must see only their own connection, messages,
application records, and Excel report.

## 5. Provider launch gates

- Gmail: move the Google OAuth consent app from Testing to Production and
  complete verification for the restricted `gmail.readonly` scope before
  admitting general users. Testing-mode Gmail refresh tokens expire after seven
  days; server-side handling of restricted data may require Google's approved
  third-party security assessment.
- Never put OAuth secrets, tokens, mailbox contents, or resume text in GitHub.

## 6. Control cost after unfreezing

- Keep Sonnet 4.5 for job discovery/ranking, but use Haiku 4.5 for email
  classification as configured in `web-app.yaml`.
- Do not repeatedly run broad job discovery during development; use stored
  fixtures for UI work and one bounded live run for the demo.
- Keep Gmail scans capped at 30 messages per synchronization.
- Check Cost Explorer by service before increasing the lease budget, and set an
  alert below the new freeze threshold.
