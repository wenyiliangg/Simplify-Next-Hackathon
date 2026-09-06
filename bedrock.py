import boto3
import json
from filter_keyword import potential_email
client=boto3.client('bedrock-runtime',region_name='us-east-1')


def classify(header, body):
    message = f"""
    Read the following email and determine if it is a potential job application status update email.
    Email Header:{header}
    Email Body:{body}

    Determine the following:
    1. Is this email a potential job application status update email? (Yes/No)
    2. If yes, what is the status of the application?
    (Shortlisted/Interview/Rejected/Offer)
    3. Is there action required on the applicant side? (Yes/No)
    4. If action is needed, what is the action required?

    Please provide your response in the following JSON format:
    {{
        "is_potential_job_application_status_email": "Yes/No",
        "application_status": "Shortlisted/Interview/Rejected/Offer",
        "action_required": "Yes/No",
        "action_details": "Provide details if action is required"
    }}
    """

    response=client.converse(modelId="amazon.nova-micro-v1:0",messages=[{"role": "user", "content": [{"text": message}]}])
    result = response["output"]["message"]["content"][0]["text"]
    print("RAW RESPONSE:")
    print(result)
    start = result.find("{")
    end = result.rfind("}") + 1
    result = json.loads(result[start:end])
    return result

test_emails = [
    {
        "header": "Application Update - Data Analyst Internship",
        "body": """
        Thank you for your application.

        We are pleased to inform you that you have been shortlisted
        for an interview. Please attend the interview on 15 September.
        """
    },

    {
        "header": "Update on your application",
        "body": """
        Thank you for your interest in the Data Analyst position.

        After careful consideration, we regret to inform you that
        your application was unsuccessful.
        """
    },

    {
        "header": "Interview Invitation - Software Engineering Intern",
        "body": """
        We are pleased to invite you to an interview for the
        Software Engineering Internship position.

        Please select an available interview slot using the link below.
        """
    },

    {
        "header": "Congratulations! You have been selected",
        "body": """
        Congratulations!

        We are delighted to offer you the position of
        Data Analyst Intern.

        Please confirm your acceptance of this offer by 20 September.
        """
    },

    {
        "header": "Weekly Newsletter",
        "body": """
        Here are this week's technology news and updates.
        We hope you enjoy reading our newsletter.
        """
    }
]


for email in test_emails:
    print("\n------------------------------")
    print("EMAIL:", email["header"])
    print("------------------------------")
    if potential_email(email["header"],email["body"]):
        result = classify(email["header"],email["body"])
        print("Classification:")
        print(result)
    else:
        print("Skipped because not a potential application email.")


