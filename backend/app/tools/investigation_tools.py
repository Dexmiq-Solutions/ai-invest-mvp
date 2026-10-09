from pathlib import Path


# Directories that should never be investigated
IGNORED_DIRECTORIES = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "dist",
    "build",
    "coverage",
}


MAX_SEARCH_FILE_SIZE = 1 * 1024 * 1024  # 1 MB
DEFAULT_MAX_RESULTS = 50
DEFAULT_MAX_CHARS = 50_000


# ============================================================
# WORKSPACE VALIDATION
# ============================================================

def _validate_workspace(workspace_path):
    """
    Validate that the workspace exists and is a directory.
    """

    workspace = Path(workspace_path).resolve()

    if not workspace.exists():
        raise ValueError(
            f"Workspace does not exist: {workspace_path}"
        )

    if not workspace.is_dir():
        raise ValueError(
            f"Workspace is not a directory: {workspace_path}"
        )

    return workspace


# ============================================================
# SAFE PATH RESOLUTION
# ============================================================

def _safe_path(workspace, relative_path):
    """
    Resolve a path and make sure it stays inside the workspace.
    """

    target = (workspace / relative_path).resolve()

    try:
        target.relative_to(workspace)
    except ValueError:
        raise ValueError(
            f"Path is outside the workspace: {relative_path}"
        )

    return target


# ============================================================
# IGNORED DIRECTORY CHECK
# ============================================================

def _is_ignored(path):
    """
    Check whether a path contains an ignored directory.
    """

    return any(
        part in IGNORED_DIRECTORIES
        for part in path.parts
    )


# ============================================================
# LIST FILES
# ============================================================

def list_files(
    workspace_path,
    path="",
    max_results=500
):
    """
    Recursively list files and directories inside the workspace.

    Returns both directories and files.
    """

    workspace = _validate_workspace(workspace_path)

    target = _safe_path(workspace, path)

    if not target.exists():
        return {
            "success": False,
            "path": path,
            "entries": [],
            "count": 0,
            "error": f"Path does not exist: {path}",
        }

    if not target.is_dir():
        return {
            "success": False,
            "path": path,
            "entries": [],
            "count": 0,
            "error": f"Path is not a directory: {path}",
        }

    entries = []

    def scan(directory):
        if len(entries) >= max_results:
            return

        try:
            children = sorted(
                directory.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower())
            )
        except PermissionError:
            return

        for child in children:

            if len(entries) >= max_results:
                break

            relative_path = child.relative_to(workspace)

            # Ignore unwanted directories
            if _is_ignored(relative_path):
                continue

            if child.is_dir():

                entries.append({
                    "type": "directory",
                    "path": str(relative_path).replace("\\", "/"),
                })

                scan(child)

            elif child.is_file():

                entries.append({
                    "type": "file",
                    "path": str(relative_path).replace("\\", "/"),
                })

    scan(target)

    return {
        "success": True,
        "path": path or ".",
        "entries": entries,
        "count": len(entries),
        "truncated": len(entries) >= max_results,
    }


# ============================================================
# SEARCH CODE
# ============================================================

def search_code(
    workspace_path,
    query,
    path="",
    max_results=DEFAULT_MAX_RESULTS
):
    """
    Search for a text query inside project files.
    """

    workspace = _validate_workspace(workspace_path)

    if not query or not query.strip():
        return {
            "success": False,
            "query": query,
            "results": [],
            "count": 0,
            "error": "Search query cannot be empty.",
        }

    target = _safe_path(workspace, path)

    if not target.exists():
        return {
            "success": False,
            "query": query,
            "results": [],
            "count": 0,
            "error": f"Path does not exist: {path}",
        }

    results = []
    query_lower = query.lower()

    # --------------------------------------------------------
    # Get files to search
    # --------------------------------------------------------

    if target.is_file():
        files = [target]

    else:
        files = []

        for file_path in target.rglob("*"):

            relative_path = file_path.relative_to(workspace)

            if _is_ignored(relative_path):
                continue

            if file_path.is_file():
                files.append(file_path)

    # --------------------------------------------------------
    # Search each file
    # --------------------------------------------------------

    for file_path in files:

        if len(results) >= max_results:
            break

        try:
            if file_path.stat().st_size > MAX_SEARCH_FILE_SIZE:
                continue

            content = file_path.read_text(
                encoding="utf-8",
                errors="ignore"
            )

        except (OSError, UnicodeDecodeError):
            continue

        for line_number, line in enumerate(
            content.splitlines(),
            start=1
        ):

            if query_lower in line.lower():

                relative_path = file_path.relative_to(workspace)

                results.append({
                    "path": str(relative_path).replace("\\", "/"),
                    "line": line_number,
                    "content": line.strip(),
                })

                if len(results) >= max_results:
                    break

    return {
        "success": True,
        "query": query,
        "results": results,
        "count": len(results),
        "truncated": len(results) >= max_results,
    }


# ============================================================
# READ FILE
# ============================================================

def read_file(
    workspace_path,
    path,
    start_line=None,
    end_line=None,
    max_chars=DEFAULT_MAX_CHARS
):
    """
    Read a specific file inside the workspace.
    """

    workspace = _validate_workspace(workspace_path)

    file_path = _safe_path(workspace, path)

    if not file_path.exists():
        return {
            "success": False,
            "path": path,
            "error": f"File does not exist: {path}",
        }

    if not file_path.is_file():
        return {
            "success": False,
            "path": path,
            "error": f"Path is not a file: {path}",
        }

    try:
        content = file_path.read_text(
            encoding="utf-8",
            errors="ignore"
        )

    except OSError as exc:
        return {
            "success": False,
            "path": path,
            "error": str(exc),
        }

    lines = content.splitlines()

    # --------------------------------------------------------
    # Apply line range
    # --------------------------------------------------------

    actual_start = start_line if start_line is not None else 1
    actual_end = end_line if end_line is not None else len(lines)

    if actual_start < 1:
        actual_start = 1

    if actual_end > len(lines):
        actual_end = len(lines)

    selected_lines = lines[
        actual_start - 1:actual_end
    ]

    selected_content = "\n".join(selected_lines)

    truncated = False

    if len(selected_content) > max_chars:
        selected_content = selected_content[:max_chars]
        truncated = True

    return {
        "success": True,
        "path": str(
            file_path.relative_to(workspace)
        ).replace("\\", "/"),
        "content": selected_content,
        "start_line": actual_start,
        "end_line": actual_end,
        "truncated": truncated,
    }