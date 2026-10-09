from app.workspace.manager import create_workspace


PROJECT_ZIP = r"C:\Users\Dellj'\Downloads\TaskFlow.zip"


print("\n==============================")
print("CREATING REAL PROJECT WORKSPACE")
print("==============================")


workspace_id, workspace_path, source_type = create_workspace(
    project_path=PROJECT_ZIP
)


print("\nWorkspace ID:")
print(workspace_id)

print("\nWorkspace Path:")
print(workspace_path)

print("\nSource Type:")
print(source_type)