import React, { useState } from 'react'
import {
  Play,
  RotateCw,
  Upload,
  Copy,
  Check,
  FileArchive,
  History,
  AlertCircle,
  FastForward,
  ChevronRight,
  Zap,
  Bookmark,
} from 'lucide-react'

const PRESET_SCENARIOS = [
  {
    label: 'TaskFlow: Token Email Mismatch',
    problem:
      'The email in the login token does not match the email stored for the user. Investigate the authentication flow, token generation, stored user email, and any mismatch between them. Identify relevant files and report findings based on actual code.',
    context:
      'The reported issue is that the email in the login token does not match the email stored for the user. Inspect the actual TaskFlow source code to trace where the user email is stored, how the login token is generated, and how the token email is validated or consumed. Identify the relevant files and possible mismatch based on code evidence; do not assume the root cause.',
    useTaskFlow: true,
  },
  {
    label: 'Scenario A: 401 Profile Unauthorized',
    problem:
      'My profile page returns 401 after login. Login succeeds, but /profile is rejected with 401 Unauthorized. Expected profile data, but request returns 401.',
    context:
      'Expected behavior: Profile data should be returned. Actual behavior: Returns 401 Unauthorized. Trace route authorization middleware and token consumption.',
    useTaskFlow: true,
  },
  {
    label: 'Scenario C: Runtime Crash on Missing User Data',
    problem:
      'Runtime exception in payload processor: crash occurs when accessing nested user identifier during data processing.',
    context:
      'Application crashes with runtime error at processor initialization. Trace call chain and identify unhandled property or key access.',
    useTaskFlow: true,
  },
  {
    label: 'TaskFlow: Task Creation Parameter Order (Phase 8 Bug)',
    problem:
      'Task creation fails when creating tasks with due dates in taskModel.js. Investigate parameter ordering, column mapping, and database insertion in insertWithDue.',
    context:
      'Reported symptom: Creating a task with a due date causes SQLite constraint error or column value corruption. Expected behavior: task.dueDate and task.userId are inserted into their corresponding columns (due_date in column 4, user_id in column 5). Trace taskModel.js and taskController.js.',
    useTaskFlow: true,
  },
  {
    label: 'Custom Issue Description',
    problem: '',
    context: '',
    useTaskFlow: false,
  },
]

export default function LeftPanel({
  sessionId,
  state,
  isRunning,
  activeStepKey,
  onStartInvestigation,
  onRunFullInvestigation,
  onRunStepByStep,
  onRunNextStep,
  onAddContext,
  onSelectSession,
  investigationsList = [],
  onRefreshInvestigations,
  nextStepName = null,
}) {
  const [problem, setProblem] = useState(PRESET_SCENARIOS[0].problem)
  const [developerContext, setDeveloperContext] = useState(PRESET_SCENARIOS[0].context)
  const [projectZip, setProjectZip] = useState(null)
  const [useDefaultTaskFlow, setUseDefaultTaskFlow] = useState(true)
  const [copiedSessionId, setCopiedSessionId] = useState(false)
  const [additionalContextInput, setAdditionalContextInput] = useState('')
  const [selectedPresetIndex, setSelectedPresetIndex] = useState(0)

  const handlePresetChange = (idx) => {
    setSelectedPresetIndex(idx)
    const p = PRESET_SCENARIOS[idx]
    if (p) {
      setProblem(p.problem)
      setDeveloperContext(p.context)
      setUseDefaultTaskFlow(p.useTaskFlow)
      if (p.useTaskFlow) {
        setProjectZip(null)
      }
    }
  }

  const handleCopySession = () => {
    if (!sessionId) return
    navigator.clipboard.writeText(sessionId)
    setCopiedSessionId(true)
    setTimeout(() => setCopiedSessionId(false), 2000)
  }

  const handleFileChange = (e) => {
    const file = e.target.files?.[0]
    if (file) {
      setProjectZip(file)
      setUseDefaultTaskFlow(false)
    }
  }

  const handleSubmitStart = (e) => {
    e.preventDefault()
    if (!problem.trim() || isRunning) return
    onStartInvestigation({
      problem: problem.trim(),
      projectZip: useDefaultTaskFlow ? null : projectZip,
      projectPath: useDefaultTaskFlow ? 'default' : null,
      developerContext: developerContext.trim() || null,
    })
  }

  const handleRunFull = () => {
    if (!problem.trim() || isRunning) return
    if (onRunFullInvestigation) {
      onRunFullInvestigation({
        problem: problem.trim(),
        projectZip: useDefaultTaskFlow ? null : projectZip,
        projectPath: useDefaultTaskFlow ? 'default' : null,
        developerContext: developerContext.trim() || null,
      })
    }
  }

  const handleContextSubmit = (e) => {
    e.preventDefault()
    if (!additionalContextInput.trim() || !sessionId || isRunning) return
    onAddContext(additionalContextInput.trim())
    setAdditionalContextInput('')
  }

  const contextStatus = state?.context_status
  const contextQuestions = state?.context_questions || []
  const needsMoreContext = contextStatus === 'insufficient' || contextStatus === 'incomplete'

  return (
    <aside className="panel-left">
      <div className="panel-header">
        <h2 className="panel-title">Investigation Controls</h2>
      </div>

      <div className="panel-body">
        {/* Active Session ID badge */}
        {sessionId && (
          <div className="session-banner">
            <div className="session-banner-header">
              <span className="text-muted">Session ID</span>
              <button
                type="button"
                className="btn-icon-subtle"
                onClick={handleCopySession}
                title="Copy Session ID"
              >
                {copiedSessionId ? <Check size={13} className="text-success" /> : <Copy size={13} />}
                <span className="session-copy-text">{copiedSessionId ? 'Copied' : 'Copy'}</span>
              </button>
            </div>
            <div className="session-id-display font-mono" title={sessionId}>
              {sessionId}
            </div>
            {state?.current_step && (
              <div className="session-step-tag">
                <span className="text-muted">Step:</span>{' '}
                <span className="session-step-name">{state.current_step}</span>
              </div>
            )}
            <div className="session-badges-row flex items-center gap-1 mt-1 flex-wrap">
              {state?.investigation_iterations && (
                <span className="badge badge-sm badge-info font-mono">
                  Iteration {state.investigation_iterations} of {state.max_iterations || 3}
                </span>
              )}
              {state?.review_result?.decision && (
                <span
                  className={`badge badge-sm ${
                    state.review_result.decision === 'sufficient'
                      ? 'badge-success'
                      : state.review_result.decision === 'inconclusive'
                      ? 'badge-neutral'
                      : 'badge-warning'
                  }`}
                >
                  Review: {state.review_result.decision}
                </span>
              )}
              {state?.is_conclusive && (
                <span className="badge badge-sm badge-success">
                  {state.status_label || 'Conclusive'}
                </span>
              )}
            </div>
          </div>
        )}

        {/* Insufficient context prompt */}
        {needsMoreContext && (
          <div className="alert-card alert-warning">
            <div className="alert-header">
              <AlertCircle size={15} />
              <strong>Additional Context Required</strong>
            </div>
            <p className="alert-body text-xs">
              The backend requires more observable symptom details before proceeding.
            </p>
            {contextQuestions.length > 0 && (
              <div className="alert-questions text-xs mt-1">
                <span className="alert-questions-label font-semibold">Clarifying Questions:</span>
                <ul className="list-disc pl-4 mt-1">
                  {contextQuestions.map((q, i) => (
                    <li key={i}>{q}</li>
                  ))}
                </ul>
              </div>
            )}
            <form onSubmit={handleContextSubmit} className="alert-form mt-2">
              <textarea
                className="form-control form-textarea text-xs"
                rows={3}
                placeholder="Provide additional runtime logs, error messages, or observed behavior..."
                value={additionalContextInput}
                onChange={(e) => setAdditionalContextInput(e.target.value)}
                disabled={isRunning}
              />
              <button
                type="submit"
                className="btn btn-sm btn-primary mt-1"
                disabled={!additionalContextInput.trim() || isRunning}
              >
                Submit Additional Context
              </button>
            </form>
          </div>
        )}

        {/* Step-by-step next action control */}
        {sessionId && nextStepName && !needsMoreContext && (
          <div className="step-action-card">
            <div className="step-action-info">
              <span className="step-action-label text-xs text-muted">Next Stage Ready:</span>
              <span className="step-action-target text-sm font-semibold">{nextStepName}</span>
            </div>
            <button
              type="button"
              className="btn btn-sm btn-accent mt-1"
              onClick={onRunNextStep}
              disabled={isRunning}
            >
              <ChevronRight size={14} />
              <span>Execute {nextStepName}</span>
            </button>
          </div>
        )}

        {/* Preset scenario selection */}
        <div className="form-group mb-2">
          <label className="form-label flex items-center gap-1">
            <Bookmark size={13} className="text-accent" />
            <span>Problem Presets</span>
          </label>
          <select
            className="form-control text-xs"
            value={selectedPresetIndex}
            onChange={(e) => handlePresetChange(Number(e.target.value))}
            disabled={isRunning}
          >
            {PRESET_SCENARIOS.map((p, idx) => (
              <option key={idx} value={idx}>
                {p.label}
              </option>
            ))}
          </select>
        </div>

        {/* Main Investigation Input Form */}
        <form onSubmit={handleSubmitStart} className="investigation-form">
          <div className="form-group">
            <label htmlFor="problem-input" className="form-label">
              Problem Statement <span className="text-danger">*</span>
            </label>
            <textarea
              id="problem-input"
              className="form-control form-textarea"
              rows={4}
              placeholder="Describe the issue, defect, or unexpected behavior to investigate..."
              value={problem}
              onChange={(e) => setProblem(e.target.value)}
              disabled={isRunning}
              required
            />
          </div>

          <div className="form-group">
            <label className="form-label">Repository Source</label>
            <div className="repo-source-toggle">
              <label className="radio-label">
                <input
                  type="radio"
                  name="repoSource"
                  checked={useDefaultTaskFlow}
                  onChange={() => {
                    setUseDefaultTaskFlow(true)
                    setProjectZip(null)
                  }}
                  disabled={isRunning}
                />
                <span>TaskFlow Benchmark (Default Fixture)</span>
              </label>
              <label className="radio-label">
                <input
                  type="radio"
                  name="repoSource"
                  checked={!useDefaultTaskFlow}
                  onChange={() => setUseDefaultTaskFlow(false)}
                  disabled={isRunning}
                />
                <span>Upload Custom ZIP Archive</span>
              </label>
            </div>

            {!useDefaultTaskFlow && (
              <div className="file-upload-box mt-2">
                <input
                  type="file"
                  id="project-zip-upload"
                  accept=".zip"
                  onChange={handleFileChange}
                  disabled={isRunning}
                  className="file-input-hidden"
                />
                <label htmlFor="project-zip-upload" className="file-upload-label">
                  <Upload size={16} />
                  <span>{projectZip ? projectZip.name : 'Choose a .zip repository archive'}</span>
                </label>
                {projectZip && (
                  <div className="file-selected-info">
                    <FileArchive size={14} />
                    <span>{(projectZip.size / (1024 * 1024)).toFixed(2)} MB</span>
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="form-group">
            <label htmlFor="developer-context-input" className="form-label">
              Additional Developer Context <span className="text-muted">(Optional)</span>
            </label>
            <textarea
              id="developer-context-input"
              className="form-control form-textarea text-xs"
              rows={3}
              placeholder="Initial developer hints, stack traces, expected behaviors, or suspect modules..."
              value={developerContext}
              onChange={(e) => setDeveloperContext(e.target.value)}
              disabled={isRunning}
            />
          </div>

          <div className="form-actions">
            <button
              type="submit"
              className="btn btn-primary btn-block"
              disabled={!problem.trim() || isRunning}
              title="Execute full sequential pipeline node-by-node"
            >
              {isRunning && activeStepKey !== 'run_full' ? (
                <>
                  <RotateCw size={15} className="spinner" />
                  <span>Investigating ({activeStepKey || 'running'})...</span>
                </>
              ) : (
                <>
                  <Play size={15} />
                  <span>Start Pipeline (Step-Measured)</span>
                </>
              )}
            </button>

            <button
              type="button"
              className="btn btn-accent btn-block"
              onClick={handleRunFull}
              disabled={!problem.trim() || isRunning}
              title="Fast-track full investigation using backend /run-full endpoint"
            >
              {isRunning && activeStepKey === 'run_full' ? (
                <>
                  <RotateCw size={15} className="spinner" />
                  <span>Executing /run-full...</span>
                </>
              ) : (
                <>
                  <Zap size={14} />
                  <span>Run Full Investigation (/run-full)</span>
                </>
              )}
            </button>

            <button
              type="button"
              className="btn btn-secondary btn-block"
              onClick={() => {
                if (!problem.trim() || isRunning) return
                onRunStepByStep({
                  problem: problem.trim(),
                  projectZip: useDefaultTaskFlow ? null : projectZip,
                  projectPath: useDefaultTaskFlow ? 'default' : null,
                  developerContext: developerContext.trim() || null,
                })
              }}
              disabled={!problem.trim() || isRunning}
              title="Initialize session and pause after Step 1"
            >
              <FastForward size={14} />
              <span>Step-by-Step Mode (Pause after step)</span>
            </button>
          </div>
        </form>

        {/* Previous Investigations Section */}
        <div className="previous-investigations-section">
          <div className="section-title-row">
            <div className="section-title">
              <History size={14} />
              <span>Previous Investigations</span>
            </div>
            <button
              type="button"
              className="btn-icon-subtle"
              onClick={onRefreshInvestigations}
              title="Refresh investigations list"
              disabled={isRunning}
            >
              <RotateCw size={12} />
            </button>
          </div>

          {investigationsList.length === 0 ? (
            <div className="empty-subtle">No previous sessions found</div>
          ) : (
            <div className="investigations-list">
              {investigationsList.map((inv) => {
                const isSelected = inv.session_id === sessionId
                return (
                  <button
                    key={inv.session_id}
                    type="button"
                    className={`investigation-item ${isSelected ? 'active' : ''}`}
                    onClick={() => onSelectSession(inv.session_id)}
                    disabled={isRunning}
                  >
                    <div className="investigation-item-header">
                      <span className="investigation-id font-mono" title={inv.session_id}>
                        {inv.session_id.substring(0, 8)}...
                      </span>
                      <span
                        className={`badge badge-sm ${
                          inv.is_conclusive ? 'badge-success' : 'badge-neutral'
                        }`}
                      >
                        {inv.is_conclusive
                          ? 'Conclusive'
                          : inv.review_decision
                          ? `Review: ${inv.review_decision}`
                          : inv.status_label && inv.status_label !== 'Unverified hypothesis'
                          ? inv.status_label
                          : 'In Progress'}
                      </span>
                    </div>
                    <div className="investigation-problem-snippet" title={inv.problem}>
                      {inv.problem}
                    </div>
                    <div className="investigation-metrics">
                      <span>{inv.findings_count ?? 0} findings</span>
                      <span>·</span>
                      <span>{inv.evidence_count ?? 0} evidence</span>
                      <span>·</span>
                      <span>{inv.tasks_count ?? 0} tasks</span>
                      {inv.iteration && inv.iteration > 1 && (
                        <>
                          <span>·</span>
                          <span className="font-semibold text-accent">Iter {inv.iteration}</span>
                        </>
                      )}
                      {inv.review_decision && (
                        <>
                          <span>·</span>
                          <span className="font-mono text-xs">{inv.review_decision}</span>
                        </>
                      )}
                    </div>
                  </button>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </aside>
  )
}
