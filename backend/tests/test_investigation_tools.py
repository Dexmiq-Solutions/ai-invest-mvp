from pathlib import Path
import pytest

from app.tools.investigation_tools import (
    list_files,
    search_code,
    read_file,
    _validate_workspace,
    _safe_path,
)


@pytest.fixture
def sample_workspace(tmp_path: Path) -> Path:
    """Create a structured sample workspace for tool testing."""
    ws = tmp_path / "test_workspace"
    ws.mkdir()

    # Directories
    (ws / "src" / "api").mkdir(parents=True)
    (ws / "src" / "services").mkdir(parents=True)
    (ws / "tests").mkdir(parents=True)
    (ws / ".git").mkdir()
    (ws / "node_modules" / "pkg").mkdir(parents=True)
    (ws / "__pycache__").mkdir()

    # Files
    (ws / "src" / "api" / "auth.py").write_text(
        "def login(user, password):\n"
        "    # Authenticate user\n"
        "    if not user:\n"
        "        return 401\n"
        "    return 200\n",
        encoding="utf-8",
    )

    (ws / "src" / "services" / "user_service.py").write_text(
        "class UserService:\n"
        "    def get_profile(self, user_id):\n"
        "        return {'id': user_id, 'role': 'admin'}\n",
        encoding="utf-8",
    )

    (ws / "tests" / "test_auth.py").write_text(
        "def test_login():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    # Ignored directory content
    (ws / ".git" / "config").write_text("git config", encoding="utf-8")
    (ws / "node_modules" / "pkg" / "index.js").write_text("console.log('pkg')", encoding="utf-8")
    (ws / "__pycache__" / "auth.cpython-313.pyc").write_text("compiled", encoding="utf-8")

    # Config files
    (ws / "requirements.txt").write_text("fastapi==0.100.0\npytest\n", encoding="utf-8")

    return ws


# ============================================================
# 1. list_files tests
# ============================================================

def test_list_files_success(sample_workspace: Path):
    result = list_files(str(sample_workspace))

    assert result["success"] is True
    assert result["count"] > 0
    paths = [entry["path"] for entry in result["entries"]]

    # Primary files must be included
    assert "src/api/auth.py" in paths
    assert "src/services/user_service.py" in paths
    assert "requirements.txt" in paths

    # Ignored directories and their contents must NOT be listed
    for p in paths:
        assert not p.startswith(".git")
        assert not p.startswith("node_modules")
        assert not p.startswith("__pycache__")


def test_list_files_subdirectory(sample_workspace: Path):
    result = list_files(str(sample_workspace), path="src/api")

    assert result["success"] is True
    paths = [entry["path"] for entry in result["entries"]]
    assert "src/api/auth.py" in paths
    assert "src/services/user_service.py" not in paths


def test_list_files_nonexistent_workspace(tmp_path: Path):
    nonexistent = tmp_path / "does_not_exist"
    with pytest.raises(ValueError, match="Workspace does not exist"):
        list_files(str(nonexistent))


def test_list_files_nonexistent_subpath(sample_workspace: Path):
    result = list_files(str(sample_workspace), path="nonexistent_dir")
    assert result["success"] is False
    assert "does not exist" in result["error"]


def test_list_files_path_traversal(sample_workspace: Path):
    with pytest.raises(ValueError, match="outside the workspace"):
        list_files(str(sample_workspace), path="../../")


# ============================================================
# 2. search_code tests
# ============================================================

def test_search_code_found(sample_workspace: Path):
    result = search_code(str(sample_workspace), query="login")

    assert result["success"] is True
    assert result["count"] >= 1
    matches = result["results"]
    match_paths = [m["path"] for m in matches]
    assert "src/api/auth.py" in match_paths


def test_search_code_case_insensitive(sample_workspace: Path):
    result = search_code(str(sample_workspace), query="LOGIN")

    assert result["success"] is True
    assert result["count"] >= 1


def test_search_code_no_matches(sample_workspace: Path):
    result = search_code(str(sample_workspace), query="nonexistent_symbol_12345")

    assert result["success"] is True
    assert result["count"] == 0
    assert result["results"] == []


def test_search_code_empty_query(sample_workspace: Path):
    result = search_code(str(sample_workspace), query="")

    assert result["success"] is False
    assert "empty" in result["error"].lower()


def test_search_code_scoped_to_path(sample_workspace: Path):
    result = search_code(str(sample_workspace), query="def", path="src/services")

    assert result["success"] is True
    for item in result["results"]:
        assert item["path"].startswith("src/services")


def test_search_code_ignores_excluded_dirs(sample_workspace: Path):
    result = search_code(str(sample_workspace), query="console.log")

    assert result["success"] is True
    # node_modules should not be searched
    assert result["count"] == 0


def test_search_code_path_traversal(sample_workspace: Path):
    with pytest.raises(ValueError, match="outside the workspace"):
        search_code(str(sample_workspace), query="login", path="../../")


# ============================================================
# 3. read_file tests
# ============================================================

def test_read_file_success(sample_workspace: Path):
    result = read_file(str(sample_workspace), "src/api/auth.py")

    assert result["success"] is True
    assert "def login" in result["content"]
    assert result["start_line"] == 1
    assert result["end_line"] == 5


def test_read_file_line_range(sample_workspace: Path):
    result = read_file(
        str(sample_workspace),
        "src/api/auth.py",
        start_line=2,
        end_line=4,
    )

    assert result["success"] is True
    assert result["start_line"] == 2
    assert result["end_line"] == 4
    lines = result["content"].splitlines()
    assert len(lines) == 3
    assert "Authenticate user" in lines[0]


def test_read_file_nonexistent(sample_workspace: Path):
    result = read_file(str(sample_workspace), "nonexistent.py")

    assert result["success"] is False
    assert "does not exist" in result["error"]


def test_read_file_is_directory(sample_workspace: Path):
    result = read_file(str(sample_workspace), "src/api")

    assert result["success"] is False
    assert "not a file" in result["error"]


def test_read_file_path_traversal(sample_workspace: Path):
    with pytest.raises(ValueError, match="outside the workspace"):
        read_file(str(sample_workspace), "../secret.txt")