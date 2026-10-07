from pathlib import Path
import json
import subprocess


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

LANGUAGE_EXTENSIONS = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".java": "Java",
    ".cpp": "C++",
    ".cc": "C++",
    ".c": "C",
    ".cs": "C#",
    ".go": "Go",
    ".rs": "Rust",
    ".php": "PHP",
    ".rb": "Ruby",
    ".kt": "Kotlin",
    ".swift": "Swift",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".sql": "SQL",
}

CONFIG_FILES = {
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "Pipfile",
    "Pipfile.lock",
    "poetry.lock",
    "uv.lock",
    "setup.py",
    "setup.cfg",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "tsconfig.json",
    "vite.config.js",
    "vite.config.ts",
    "next.config.js",
    "next.config.ts",
    "webpack.config.js",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".gitignore",
    ".env.example",
    "pytest.ini",
    "tox.ini",
}

IMPORTANT_DIRECTORY_NAMES = {
    "src",
    "app",
    "api",
    "routes",
    "controllers",
    "services",
    "models",
    "components",
    "pages",
    "utils",
    "config",
    "middleware",
    "tests",
    "test",
    "docs",
    "scripts",
}

ENTRY_POINT_NAMES = {
    "main.py",
    "app.py",
    "server.py",
    "index.py",
    "index.js",
    "index.ts",
    "main.js",
    "main.ts",
    "server.js",
    "server.ts",
    "manage.py",
}


def analyze_repository(workspace_path):
    root = Path(workspace_path)

    if not root.exists():
        raise FileNotFoundError(
            f"Repository path does not exist: {workspace_path}"
        )

    if not root.is_dir():
        raise NotADirectoryError(
            f"Repository path is not a directory: {workspace_path}"
        )

    # Workspace manager may wrap the actual project inside one directory.
    root = _resolve_repository_root(root)

    files = _collect_files(root)

    return {
        "project_structure": _analyze_structure(root, files),
        "languages": _detect_languages(files),
        "frameworks": _detect_frameworks(root, files),
        "important_directories": _detect_important_directories(root),
        "configuration_files": _detect_configuration_files(root, files),
        "entry_points": _detect_entry_points(root, files),
        "test_directories": _detect_test_directories(root),
        "git_info": _get_git_info(root),
    }


def _resolve_repository_root(root: Path) -> Path:
    """
    Resolve the actual project directory when the workspace contains
    a single wrapped project directory.

    Examples:
        workspace/
            sample_project/
                src/
                tests/

    becomes:

        workspace/sample_project
    """

    # Git clone already points directly to the repository.
    if (root / ".git").exists():
        return root

    try:
        children = [
            path
            for path in root.iterdir()
            if path.name not in IGNORED_DIRECTORIES
        ]
    except OSError:
        return root

    directories = [path for path in children if path.is_dir()]

    meaningful_files = [
        path
        for path in children
        if path.is_file() and path.name not in {".DS_Store"}
    ]

    # If the workspace contains exactly one directory and no files,
    # treat that directory as the actual repository.
    if len(directories) == 1 and not meaningful_files:
        return directories[0]

    return root


def _collect_files(root: Path):
    files = []

    for path in root.rglob("*"):
        if not path.is_file():
            continue

        relative_parts = path.relative_to(root).parts

        if any(part in IGNORED_DIRECTORIES for part in relative_parts):
            continue

        files.append(path)

    return files


def _analyze_structure(root: Path, files):
    top_level_files = []
    top_level_directories = []

    for path in root.iterdir():
        if path.name in IGNORED_DIRECTORIES:
            continue

        if path.is_file():
            top_level_files.append(path.name)

        elif path.is_dir():
            top_level_directories.append(path.name)

    return {
        "root": str(root),
        "top_level_files": sorted(top_level_files),
        "top_level_directories": sorted(top_level_directories),
        "file_count": len(files),
        "directory_count": sum(
            1
            for path in root.rglob("*")
            if path.is_dir()
            and not any(
                part in IGNORED_DIRECTORIES
                for part in path.relative_to(root).parts
            )
        ),
    }


def _detect_languages(files):
    languages = set()

    for path in files:
        language = LANGUAGE_EXTENSIONS.get(path.suffix.lower())

        if language:
            languages.add(language)

    return sorted(languages)


def _read_text_file(path: Path):
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def _detect_frameworks(root: Path, files):
    frameworks = set()

    file_names = {path.name for path in files}

    # Python dependency/configuration detection
    python_files = {
        "requirements.txt",
        "requirements-dev.txt",
        "pyproject.toml",
        "Pipfile",
        "setup.py",
    }

    for path in files:
        if path.name in python_files:
            content = _read_text_file(path).lower()

            if "fastapi" in content:
                frameworks.add("FastAPI")

            if "django" in content:
                frameworks.add("Django")

            if "flask" in content:
                frameworks.add("Flask")

            if "langgraph" in content:
                frameworks.add("LangGraph")

            if "langchain" in content:
                frameworks.add("LangChain")

            if "streamlit" in content:
                frameworks.add("Streamlit")

    # package.json detection
    package_json = next(
        (path for path in files if path.name == "package.json"),
        None,
    )

    if package_json:
        try:
            package_data = json.loads(_read_text_file(package_json))

            dependencies = {}

            dependencies.update(
                package_data.get("dependencies", {})
            )
            dependencies.update(
                package_data.get("devDependencies", {})
            )

            dependency_names = {
                name.lower()
                for name in dependencies
            }

            if "react" in dependency_names:
                frameworks.add("React")

            if "next" in dependency_names:
                frameworks.add("Next.js")

            if "vue" in dependency_names:
                frameworks.add("Vue.js")

            if "@angular/core" in dependency_names:
                frameworks.add("Angular")

            if "express" in dependency_names:
                frameworks.add("Express")

            if "@nestjs/core" in dependency_names:
                frameworks.add("NestJS")

            if "vite" in dependency_names:
                frameworks.add("Vite")

        except (json.JSONDecodeError, TypeError):
            pass

    # Configuration-file based detection
    if "vite.config.js" in file_names or "vite.config.ts" in file_names:
        frameworks.add("Vite")

    if "next.config.js" in file_names or "next.config.ts" in file_names:
        frameworks.add("Next.js")

    return sorted(frameworks)


def _detect_important_directories(root: Path):
    important = []

    for path in root.rglob("*"):
        if not path.is_dir():
            continue

        relative_path = path.relative_to(root)

        if any(
            part in IGNORED_DIRECTORIES
            for part in relative_path.parts
        ):
            continue

        if path.name.lower() in IMPORTANT_DIRECTORY_NAMES:
            # Always use "/" so results are consistent across
            # Windows/Linux/macOS.
            important.append(relative_path.as_posix())

    return sorted(set(important))


def _detect_configuration_files(root: Path, files):
    configuration_files = []

    for path in files:
        if path.name in CONFIG_FILES:
            configuration_files.append(
                path.relative_to(root).as_posix()
            )

    return sorted(configuration_files)


def _detect_entry_points(root: Path, files):
    entry_points = []

    for path in files:
        if path.name in ENTRY_POINT_NAMES:
            entry_points.append(
                path.relative_to(root).as_posix()
            )

    return sorted(entry_points)


def _detect_test_directories(root: Path):
    test_directories = []

    for path in root.rglob("*"):
        if not path.is_dir():
            continue

        relative_path = path.relative_to(root)

        if any(
            part in IGNORED_DIRECTORIES
            for part in relative_path.parts
        ):
            continue

        if path.name.lower() in {"test", "tests"}:
            test_directories.append(relative_path.as_posix())

    return sorted(set(test_directories))


def _get_git_info(root: Path):
    git_directory = root / ".git"

    if not git_directory.exists():
        return {
            "is_git_repository": False,
            "branch": None,
            "commit": None,
        }

    try:
        branch_result = subprocess.run(
            ["git", "-C", str(root), "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        commit_result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        branch = branch_result.stdout.strip() or None
        commit = commit_result.stdout.strip() or None

        return {
            "is_git_repository": True,
            "branch": branch,
            "commit": commit,
        }

    except (
        OSError,
        subprocess.SubprocessError,
    ):
        return {
            "is_git_repository": True,
            "branch": None,
            "commit": None,
        }