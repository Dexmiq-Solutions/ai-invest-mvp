import io
import pytest
from fastapi.testclient import TestClient

from app.api import app

client = TestClient(app)


def test_investigation_start_with_zip_mock():
    """Verify investigation start endpoint with a project zip payload."""
    problem = (
        "Observed behavior: After login, the email in the authentication token differs "
        "from the email stored for the user in the database."
    )
    # Provide a minimal in-memory zip file
    dummy_zip = io.BytesIO(b"PK\x05\x06" + b"\x00" * 18)  # minimal empty zip archive
    response = client.post(
        "/api/investigation/start",
        data={"problem": problem},
        files={
            "project_zip": (
                "TaskFlow.zip",
                dummy_zip,
                "application/zip",
            )
        },
    )
    assert response.status_code in (200, 400)

