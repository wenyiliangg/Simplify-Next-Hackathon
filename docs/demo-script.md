# Offline hackathon demo script

Start the frontend without AWS or Gmail:

```bash
cd frontend
python3 -m http.server 5173
```

Open `http://localhost:5173/?demo=1`. Keep the green **Offline demo mode**
disclosure visible at the beginning so the audience knows the records are
representative sample data.

## Suggested two-minute walkthrough

1. Click **Connect Gmail**.
   - Say: "In production this opens Google's consent screen and requests only
     read-only Gmail access. The app never sends, deletes, moves, or marks mail
     as read."
2. Click **Sync Gmail**.
   - Say: "The email workflow searches a bounded set of internship-related
     messages, uses Haiku to classify genuine application updates, and stores
     only compact status evidence."
3. Point to the pipeline rows.
   - Say: "Applied, assessment, interview, rejection, and offer events are
     reduced chronologically into one current state per application."
4. Click **Download Excel**.
   - Say: "The production action creates a private, short-lived Excel download
     containing summary, applications, today's changes, and items needing
     review."
5. Click **Find & rank internships**.
   - Say: "The fit workflow verifies official job-detail pages, applies degree,
     graduation, authorization, location, and availability gates, then scores
     eligible jobs using skills, similar experience, projects, career alignment,
     preferred qualifications, and education relevance."

## Architecture statement

"The API is the orchestrator. It routes fit work to the AgentCore Harness and
Gmail work through a trusted user-bound Lambda path. The mailbox identity comes
only from the Cognito JWT—not from a prompt or model tool argument—so each
user's OAuth token, application records, and Excel report are isolated."

## Honest current-state statement

"The complete production source is in this repository. The AWS sandbox deployment
is currently frozen by the hackathon budget threshold, so this presentation uses
the explicitly labeled offline mode. Public Gmail onboarding additionally
requires Google's verification of the restricted `gmail.readonly` scope."
