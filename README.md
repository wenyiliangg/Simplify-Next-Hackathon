# Simplify Next Hackathon

Internship discovery, fit ranking, and read-only Gmail application tracking powered by Amazon Bedrock AgentCore.

## Version-controlled AgentCore configuration

- `agentcore/orchestrator.yaml` records the unified Orchestrator model, version, and tool bindings.
- `agentcore/system-prompt.md` contains the exact deployed Orchestrator system prompt.
- `agentcore/email-tools.yaml` records the Gmail gateway contract and OAuth callback without secrets.
- `deployment/aws-live-state.md` records the deployed AWS resource names and the remaining export work.

OAuth credentials and tokens must remain in AWS-managed secret storage and must never be committed.
