/**
 * Backend API Client for AI Software Issue Investigator
 * Connects directly to FastAPI backend endpoints.
 */

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

async function handleResponse(response) {
  if (!response.ok) {
    let errorDetail = `HTTP ${response.status} ${response.statusText}`
    try {
      const data = await response.json()
      if (data && data.detail) {
        errorDetail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
      }
    } catch {
      // response wasn't JSON
    }
    throw new Error(errorDetail)
  }
  return response.json()
}

export async function fetchInvestigations() {
  const res = await fetch(`${API_BASE}/api/investigations`)
  return handleResponse(res)
}

export async function fetchSession(sessionId) {
  const res = await fetch(`${API_BASE}/api/investigation/${sessionId}`)
  return handleResponse(res)
}

export async function startInvestigation({
  problem,
  projectZip,
  projectPath,
  repositoryUrl,
  autoRun = false,
}) {
  const formData = new FormData()
  formData.append('problem', problem)
  formData.append('auto_run', autoRun ? 'true' : 'false')
  if (projectZip) {
    formData.append('project_zip', projectZip)
  }
  if (projectPath) {
    formData.append('project_path', projectPath)
  }
  if (repositoryUrl) {
    formData.append('repository_url', repositoryUrl)
  }

  const res = await fetch(`${API_BASE}/api/investigation/start`, {
    method: 'POST',
    body: formData,
  })
  return handleResponse(res)
}

export async function addContext(sessionId, additionalContext) {
  const res = await fetch(`${API_BASE}/api/investigation/${sessionId}/context`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ additional_context: additionalContext }),
  })
  return handleResponse(res)
}

export async function setupWorkspace(sessionId) {
  const res = await fetch(`${API_BASE}/api/investigation/${sessionId}/workspace`, {
    method: 'POST',
  })
  return handleResponse(res)
}

export async function analyzeRepository(sessionId) {
  const res = await fetch(`${API_BASE}/api/investigation/${sessionId}/repository-analysis`, {
    method: 'POST',
  })
  return handleResponse(res)
}

export async function gatherContext(sessionId) {
  const res = await fetch(`${API_BASE}/api/investigation/${sessionId}/context-gathering`, {
    method: 'POST',
  })
  return handleResponse(res)
}

export async function fetchPlan(sessionId) {
  const res = await fetch(`${API_BASE}/api/investigation/${sessionId}/plan`, {
    method: 'GET',
  })
  return handleResponse(res)
}

export async function executeInvestigation(sessionId) {
  const res = await fetch(`${API_BASE}/api/investigation/${sessionId}/execute`, {
    method: 'POST',
  })
  return handleResponse(res)
}

export async function runFullInvestigation(sessionId, autoReview = false) {
  const query = autoReview ? '?auto_review=true' : ''
  const res = await fetch(`${API_BASE}/api/investigation/${sessionId}/run-full${query}`, {
    method: 'POST',
  })
  return handleResponse(res)
}

export async function reviewInvestigation(sessionId) {
  const res = await fetch(`${API_BASE}/api/investigation/${sessionId}/review`, {
    method: 'POST',
  })
  return handleResponse(res)
}

export async function replanInvestigation(sessionId) {
  const res = await fetch(`${API_BASE}/api/investigation/${sessionId}/replan`, {
    method: 'POST',
  })
  return handleResponse(res)
}

export async function fetchWorkspaceFiles(sessionId) {
  const res = await fetch(`${API_BASE}/api/investigation/${sessionId}/files`)
  return handleResponse(res)
}

export async function fetchFileContent(sessionId, filePath) {
  const res = await fetch(`${API_BASE}/api/investigation/${sessionId}/files/content?path=${encodeURIComponent(filePath)}`)
  return handleResponse(res)
}

export async function runDiagnosis(sessionId) {
  const res = await fetch(`${API_BASE}/api/investigation/${sessionId}/diagnosis`, {
    method: 'POST',
  })
  return handleResponse(res)
}

export async function fetchReport(sessionId) {
  const res = await fetch(`${API_BASE}/api/investigation/${sessionId}/report`)
  return handleResponse(res)
}
