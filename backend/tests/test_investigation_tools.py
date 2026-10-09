from app.tools.investigation_tools import (
    list_files,
    search_code,
    read_file,
)


WORKSPACE = r"workspaces\14bef38f-310e-4b56-ae10-acac1a767f36"


print("\n" + "=" * 60)
print("PHASE 4 — INVESTIGATION TOOLS TEST")
print("=" * 60)


# ============================================================
# 1. TEST list_files()
# ============================================================

print("\n\n[1] TESTING list_files()")
print("-" * 60)

list_result = list_files(WORKSPACE)

print("Success:", list_result["success"])
print("Path:", list_result["path"])
print("Count:", list_result["count"])

print("\nProject entries:")

for entry in list_result["entries"]:
    print(f'{entry["type"]:10} {entry["path"]}')


# ============================================================
# 2. TEST search_code()
# ============================================================
# ============================================================
# 2. TEST search_code()
# ============================================================

print("\n\n[2] TESTING search_code()")
print("-" * 60)


# ---------- Frontend search ----------

print("\n--- FRONTEND SEARCH: login ---")

frontend_result = search_code(
    WORKSPACE,
    query="login",
    path="DevTender/frontend"
)

print("Success:", frontend_result["success"])
print("Results:", frontend_result["count"])

for item in frontend_result["results"]:
    print(
        f'{item["path"]} '
        f'(line {item["line"]})'
    )


# ---------- Backend search ----------

print("\n--- BACKEND SEARCH: auth ---")

backend_result = search_code(
    WORKSPACE,
    query="auth",
    path="DevTender/backend"
)

print("Success:", backend_result["success"])
print("Results:", backend_result["count"])

for item in backend_result["results"]:
    print(
        f'{item["path"]} '
        f'(line {item["line"]})'
    )
# ============================================================
# 3. TEST read_file()
# ============================================================

print("\n\n[3] TESTING read_file()")
print("-" * 60)

file_to_read = None

for entry in list_result["entries"]:
    if entry["type"] == "file":
        file_to_read = entry["path"]
        break


if file_to_read:

    print("Reading:", file_to_read)

    file_result = read_file(
        WORKSPACE,
        file_to_read
    )

    print("Success:", file_result["success"])
    print("File:", file_result["path"])

    print(
        "Lines:",
        file_result["start_line"],
        "-",
        file_result["end_line"]
    )

    print("\nFile content:")
    print("-" * 60)
    print(file_result["content"][:3000])

else:
    print(
        "No file found in the current directory."
    )


print("\n\n" + "=" * 60)
print("ALL TOOL TESTS COMPLETED")
print("=" * 60)