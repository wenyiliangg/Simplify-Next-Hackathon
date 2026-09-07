# AWS access recovery and final verification

The application is not fully production-ready until every item below passes.

## 1. Restore the sandbox role

Restore the Innovation Sandbox lease/assignment for account `591255906109` and
role `hack2026_IsbUsersPS`. Confirm that the AWS access portal lists the account
and that the console opens in `us-east-1`.

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

## 4. Keep email single-user until identity is fixed

Do not onboard additional Gmail users to the currently deployed email Gateway.
Its Lambda target receives Gateway/tool metadata, not the Harness
`runtimeUserId`, and currently falls back to `demo-user`. Before public use,
route email tool calls through a trusted component that derives the user from a
verified Cognito JWT and passes it to the Lambda invocation context, then disable
the demo fallback and remove `demo_user_id` from every public schema.

After that change, test with two separate Cognito users and two separate Gmail
test accounts. Each user must see only their own connection, messages,
application records, and Excel report.

## 5. Provider launch gates

- Gmail: move the Google OAuth consent app from Testing to Production and
  complete verification for `gmail.readonly` before admitting general users.
- Outlook: deploy a multi-tenant Microsoft Entra application using delegated
  Microsoft Graph `Mail.Read`; Outlook is not implemented in the live stack yet.
- Never put OAuth secrets, tokens, mailbox contents, or resume text in GitHub.
