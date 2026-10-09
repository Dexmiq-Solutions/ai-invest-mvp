import os
import uuid
import tempfile
from pathlib import Path
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.graph.graph import (
    initial_node,
    repository_analysis_node,
    context_gathering_graph_node
)
from app.graph.context import validate_input, check_context
from app.graph.workspace import setup_workspace

app = FastAPI(title="AI Investigator Orchestration API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session storage
sessions = {}

@app.post("/api/investigation/start")
async def start_investigation(
    problem: str = Form(...),
    project_zip: UploadFile = File(None),
    repository_url: str = Form(None)
):
    session_id = str(uuid.uuid4())
    
    # Handle ZIP upload
    zip_path = None
    if project_zip:
        temp_dir = Path(tempfile.gettempdir()) / "ai_investigator_uploads"
        temp_dir.mkdir(parents=True, exist_ok=True)
        zip_path = temp_dir / f"{session_id}_{project_zip.filename}"
        with open(zip_path, "wb") as f:
            f.write(await project_zip.read())
            
    # Initial state
    state = {
        "problem": problem,
        "project_path": str(zip_path) if zip_path else "",
        "repository_url": repository_url if repository_url else "",
        "problem_context": {}
    }
    
    # 1. Initial Node
    state = initial_node(state)
    
    # 2. Validation Node
    state = validate_input(state)
    if state.get("validation_errors"):
        sessions[session_id] = state
        return {"session_id": session_id, "state": state, "status": "VALIDATION_FAILED"}
        
    # 3. Context Check Node
    state = check_context(state)
    
    sessions[session_id] = state
    
    if state.get("context_status") == "sufficient":
        return {"session_id": session_id, "state": state, "status": "CONTEXT_SUFFICIENT"}
    else:
        return {"session_id": session_id, "state": state, "status": "CONTEXT_INSUFFICIENT"}

class ContextAddRequest(BaseModel):
    additional_context: str

@app.post("/api/investigation/{session_id}/context")
async def add_context(session_id: str, req: ContextAddRequest):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
        
    state = sessions[session_id]
    
    # Append developer context
    existing_problem = state.get("problem", "")
    state["problem"] = f"{existing_problem}\n\nAdditional Developer Context:\n{req.additional_context}"
    
    # Re-run Context Check
    state = check_context(state)
    sessions[session_id] = state
    
    if state.get("context_status") == "sufficient":
        return {"session_id": session_id, "state": state, "status": "CONTEXT_SUFFICIENT"}
    else:
        return {"session_id": session_id, "state": state, "status": "CONTEXT_INSUFFICIENT"}

@app.post("/api/investigation/{session_id}/workspace")
async def create_workspace(session_id: str):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
        
    state = sessions[session_id]
    state = setup_workspace(state)
    sessions[session_id] = state
    
    if state.get("current_step") == "workspace_ready":
        return {"session_id": session_id, "state": state, "status": "WORKSPACE_CREATED"}
    else:
        return {"session_id": session_id, "state": state, "status": "WORKSPACE_FAILED"}

@app.post("/api/investigation/{session_id}/repository-analysis")
async def analyze_repository(session_id: str):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
        
    state = sessions[session_id]
    state = repository_analysis_node(state)
    sessions[session_id] = state
    
    if state.get("current_step") == "repository_analyzed":
        return {"session_id": session_id, "state": state, "status": "REPOSITORY_ANALYZED"}
    else:
        return {"session_id": session_id, "state": state, "status": "REPOSITORY_ANALYSIS_FAILED"}

@app.post("/api/investigation/{session_id}/context-gathering")
async def gather_context(session_id: str):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
        
    state = sessions[session_id]
    state = context_gathering_graph_node(state)
    sessions[session_id] = state
    
    if state.get("current_step") != "context_gathering_failed":
        return {"session_id": session_id, "state": state, "status": "CONTEXT_GATHERING_COMPLETE"}
    else:
        return {"session_id": session_id, "state": state, "status": "CONTEXT_GATHERING_FAILED"}
