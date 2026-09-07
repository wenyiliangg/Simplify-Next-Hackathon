# Product walkthrough

Start the frontend:

```bash
cd frontend
python3 -m http.server 5173
```

Open `http://localhost:5173`. Live sign-up, Gmail, ranking, and export actions
require the deployed AWS backend. If the backend is unavailable, demonstrate
the interface and architecture without claiming that live results were created.

## Suggested two-minute walkthrough

1. Click **Sign up**, then explain that Cognito securely creates the account.
2. After sign-in, click **Connect Gmail**.
   - Say: "This opens Google's consent screen and requests only read-only Gmail
     access. The app never sends, deletes, moves, or marks mail as read."
3. Click **Sync Gmail**.
   - Say: "The email workflow searches a bounded set of internship-related
     messages, uses Haiku to classify genuine application updates, and stores
     only compact status evidence."
4. Point to the pipeline rows.
   - Say: "Applied, assessment, interview, rejection, and offer events are
     reduced chronologically into one current state per application."
5. Click **Download Excel**.
   - Say: "The production action creates a private, short-lived Excel download
     containing summary, applications, today's changes, and items needing
     review."
6. Upload a text-based PDF résumé, complete the profile, and click **Find & rank internships**.
   - Say: "The fit workflow verifies official job-detail pages, applies degree,
     graduation, authorization, location, and availability gates, then scores
     eligible jobs using skills, similar experience, projects, career alignment,
     preferred qualifications, and education relevance."

## Architecture statement

"The API is the orchestrator. It routes fit work to the AgentCore Harness and
Gmail work through a trusted user-bound Lambda path. The mailbox identity comes
only from the Cognito JWT—not from a prompt or model tool argument—so each
user's OAuth token, application records, and Excel report are isolated."

## Current-state statement

"The complete production source is in this repository. The original AWS sandbox
deployment is currently frozen by the hackathon budget threshold. Restoring the
deployment enables live agent actions. Public Gmail onboarding additionally
requires Google's verification of the restricted `gmail.readonly` scope."
