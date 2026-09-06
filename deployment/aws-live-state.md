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
- Unified orchestrator: `internship_orchestrator_v1`, Version 2

The Orchestrator model and tool bindings are recorded in `agentcore/orchestrator.yaml`; its exact system prompt is in `agentcore/system-prompt.md`.

## Remaining source export

The deployed CloudFormation template and Lambda ZIP were not available in the local checkout. They must be downloaded from the authenticated AWS account before they can be committed as the canonical deployable source. Do not recreate those files from memory: that could differ from the deployed resources.

Never commit OAuth client secrets, OAuth tokens, authorization codes, mailbox content, or resume data.

