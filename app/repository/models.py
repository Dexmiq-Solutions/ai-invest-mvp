from typing import TypedDict


class RepositorySummary(TypedDict, total=False):
    project_structure: dict
    languages: list[str]
    frameworks: list[str]
    important_directories: list[str]
    configuration_files: list[str]
    entry_points: list[str]
    test_directories: list[str]
    git_info: dict