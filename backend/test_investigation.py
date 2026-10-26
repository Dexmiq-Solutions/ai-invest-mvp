import json
import requests

zip_path = r"C:\Users\Dellj'\Downloads\TaskFlow.zip"

problem = (
    "The email in the login token does not match the email stored for "
    "the user. Investigate the authentication flow, token generation, "
    "stored user email, and any mismatch between them. Identify relevant "
    "files and report findings based on actual code."
)

url = "http://127.0.0.1:8000/api/investigation/start"

with open(zip_path, "rb") as project_file:
    response = requests.post(
        url,
        data={"problem": problem, "auto_run": "true"},
        files={
            "project_zip": (
                "TaskFlow.zip",
                project_file,
                "application/zip",
            )
        },
        timeout=300,
    )

print("HTTP Status:", response.status_code)
try:
    data = response.json()
    print(json.dumps(data, indent=2, default=str))
except Exception:
    print(response.text)

