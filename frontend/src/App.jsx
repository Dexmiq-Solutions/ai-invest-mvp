import React, { useState, useEffect, useMemo, useCallback } from 'react'
import {
  fetchInvestigations,
  fetchSession,
  startInvestigation,
  addContext,
  setupWorkspace,
  analyzeRepository,
  gatherContext,
  fetchPlan,
  executeInvestigation,
  runFullInvestigation,
  reviewInvestigation,
  replanInvestigation,
  runDiagnosis,
} from './api/investigatorApi'
import { buildWorkflowNodes } from './utils/workflowMapping'
import LeftPanel from './components/LeftPanel'
import CenterTimeline from './components/CenterTimeline'
import RightInspector from './components/RightInspector'
import InvestigationReport from './components/InvestigationReport'
import ErrorBoundary from './components/ErrorBoundary'
import {
  FileText,
  AlertTriangle,
  GitGraph,
} from 'lucide-react'

export default function App() {
  const [sessionId, setSessionId] = useState('')
  const [state, setState] = useState(null)
  const [investigationsList, setInvestigationsList] = useState([])
  const [selectedNodeId, setSelectedNodeId] = useState(null)
  const [isRunning, setIsRunning] = useState(false)
  const [activeStepKey, setActiveStepKey] = useState(null)
  const [currentView, setCurrentView] = useState('dashboard') // 'dashboard' | 'report'
  const [errorMessage, setErrorMessage] = useState(null)
  const [nodeExecutionTrace, setNodeExecutionTrace] = useState({})

  // Fetch previous investigations on mount
  const refreshInvestigations = useCallback(async () => {
    try {
      const data = await fetchInvestigations()
      if (data && data.investigations) {
        setInvestigationsList(data.investigations)
      }
    } catch (err) {
      console.warn('Could not fetch previous investigations:', err)
    }
  }, [])

  // Load a session by ID
  const loadSession = useCallback(async (sid) => {
    try {
      setErrorMessage(null)
      const data = await fetchSession(sid)
      if (data && data.state) {
        setSessionId(sid)
        setState(data.state)
        // Select initial node or final outcome node
        const mapped = buildWorkflowNodes(data.state)
        if (mapped.length > 0) {
          setSelectedNodeId(mapped[mapped.length - 1].id)
        }
      }
    } catch (err) {
      setErrorMessage(`Failed to load session ${sid}: ${err.message}`)
    }
  }, [])

  useEffect(() => {
    let mounted = true
    async function init() {
      try {
        const data = await fetchInvestigations()
        if (mounted && data && data.investigations) {
          setInvestigationsList(data.investigations)
          if (data.investigations.length > 0) {
            loadSession(data.investigations[0].session_id)
          }
        }
      } catch (err) {
        console.warn('Could not initialize investigations:', err)
      }
    }
    init()
    return () => {
      mounted = false
    }
  }, [loadSession])

  // Map backend state to nodes
  const nodes = useMemo(() => {
    return buildWorkflowNodes(state || {}, activeStepKey, nodeExecutionTrace)
  }, [state, activeStepKey, nodeExecutionTrace])

  // Determine active selected node safely without triggering re-render effect
  const activeNodeId = useMemo(() => {
    if (selectedNodeId && nodes.some((n) => n.id === selectedNodeId)) {
      return selectedNodeId
    }
    return nodes[0]?.id || null
  }, [selectedNodeId, nodes])

  const selectedNode = useMemo(() => {
    return nodes.find((n) => n.id === activeNodeId) || nodes[0] || null
  }, [nodes, activeNodeId])

  // Determine what the next stage is when running step-by-step
  // Determine what the next stage is when running step-by-step
  const nextStepName = useMemo(() => {
    if (!state) return null
    const currentStep = state.current_step
    const ctxStatus = state.context_status

    if (ctxStatus !== 'sufficient') return null

    // 1. If review has been evaluated
    if (state.review_result) {
      if (
        state.review_result.decision === 'insufficient' &&
        (state.investigation_iterations || 1) < (state.max_iterations || 3) &&
        !state.is_conclusive
      ) {
        return 'Investigation Re-planning'
      }
      if (!state.final_report && !state.suggested_fix) {
        return 'Diagnosis & Verification'
      }
      return null
    }

    // 2. If execution completed and reviewer evaluation is next
    if (
      !state.review_result &&
      (['investigation_completed', 'completed', 'partial'].includes(
        state.execution_status || state.investigation_status
      ) || currentStep === 'investigation_completed')
    ) {
      return 'Reviewer Evaluation'
    }

    // 3. If plan exists and execution is next
    if (
      state.investigation_plan &&
      state.investigation_plan.length > 0 &&
      (!state.execution_status ||
        state.execution_status === 'pending' ||
        (state.investigation_status === 'pending' && currentStep !== 'investigation_completed'))
    ) {
      return 'Specialist Execution & Aggregation'
    }

    // 4. If context gathered and plan creation is next
    if (
      (!state.investigation_plan || state.investigation_plan.length === 0) &&
      ((state.gathered_context && Object.keys(state.gathered_context).length > 0) ||
        currentStep === 'context_gathering_complete' ||
        currentStep === 'context_gathered')
    ) {
      return 'Plan Creation & Validation'
    }

    // 5. If repository analyzed and context gathering is next
    if (
      (!state.gathered_context || Object.keys(state.gathered_context).length === 0) &&
      ((state.repository_summary && Object.keys(state.repository_summary).length > 0) ||
        currentStep === 'repository_analyzed')
    ) {
      return 'Context Gathering'
    }

    // 6. If workspace ready and repository analysis is next
    if (
      (!state.repository_summary || Object.keys(state.repository_summary).length === 0) &&
      (state.workspace_path || state.workspace_id || currentStep === 'workspace_ready')
    ) {
      return 'Repository Analysis'
    }

    // 7. Initial workspace setup
    if (
      !state.workspace_path &&
      !state.workspace_id &&
      currentStep !== 'workspace_ready' &&
      !state.repository_summary &&
      !state.investigation_plan
    ) {
      return 'Workspace Setup'
    }

    return null
  }, [state])

  // Helper to record client-side node duration
  const measureStep = async (stepKey, asyncFn) => {
    setActiveStepKey(stepKey)
    const t0 = Date.now()
    try {
      const res = await asyncFn()
      const t1 = Date.now()
      setNodeExecutionTrace((prev) => ({
        ...prev,
        [stepKey]: {
          duration_seconds: Number(((t1 - t0) / 1000).toFixed(2)),
          start_time: Math.floor(t0 / 1000),
          end_time: Math.floor(t1 / 1000),
          status: 'completed',
        },
      }))
      return res
    } catch (err) {
      const t1 = Date.now()
      setNodeExecutionTrace((prev) => ({
        ...prev,
        [stepKey]: {
          duration_seconds: Number(((t1 - t0) / 1000).toFixed(2)),
          start_time: Math.floor(t0 / 1000),
          end_time: Math.floor(t1 / 1000),
          status: 'failed',
          error: err.message,
        },
      }))
      throw err
    }
  }

  // Sequential node-by-node investigation pipeline
  const handleStartInvestigation = async ({
    problem,
    projectZip,
    projectPath,
    repositoryUrl,
    developerContext,
  }) => {
    setIsRunning(true)
    setErrorMessage(null)
    setNodeExecutionTrace({})

    try {
      // Step 1: Input Validation & Context check
      const startRes = await measureStep('initial_validation', () =>
        startInvestigation({ problem, projectZip, projectPath, repositoryUrl })
      )
      const sid = startRes.session_id
      setSessionId(sid)
      let currentState = startRes.state
      setState(currentState)

      if (startRes.status === 'VALIDATION_FAILED') {
        setIsRunning(false)
        setActiveStepKey(null)
        setErrorMessage(
          'Input validation failed: ' + (currentState.validation_errors || []).join(', ')
        )
        return
      }

      // Step 2: Add Developer Context if provided
      if (developerContext && developerContext.trim()) {
        const ctxRes = await measureStep('context_check', () =>
          addContext(sid, developerContext.trim())
        )
        currentState = ctxRes.state
        setState(currentState)
      }

      if (currentState.context_status !== 'sufficient') {
        setIsRunning(false)
        setActiveStepKey(null)
        return
      }

      // Step 3: Workspace Setup
      const wsRes = await measureStep('workspace', () => setupWorkspace(sid))
      currentState = wsRes.state
      setState(currentState)
      if (wsRes.status !== 'WORKSPACE_CREATED') {
        setIsRunning(false)
        setActiveStepKey(null)
        setErrorMessage('Workspace setup failed')
        return
      }

      // Step 4: Repository Analysis
      const repoRes = await measureStep('repository_analysis', () => analyzeRepository(sid))
      currentState = repoRes.state
      setState(currentState)
      if (repoRes.status !== 'REPOSITORY_ANALYZED') {
        setIsRunning(false)
        setActiveStepKey(null)
        setErrorMessage('Repository analysis failed')
        return
      }

      // Step 5: Context Gathering
      const gatherRes = await measureStep('context_gathering', () => gatherContext(sid))
      currentState = gatherRes.state
      setState(currentState)
      if (gatherRes.status !== 'CONTEXT_GATHERING_COMPLETE') {
        setIsRunning(false)
        setActiveStepKey(null)
        setErrorMessage('Context gathering failed')
        return
      }

      // Step 6: Plan Creation & Context-Aware Validation (GET /api/investigation/{sid}/plan)
      const planRes = await measureStep('plan', () => fetchPlan(sid))
      currentState = planRes.state
      setState(currentState)
      if (planRes.status !== 'PLAN_READY') {
        setIsRunning(false)
        setActiveStepKey(null)
        setErrorMessage('Plan creation failed')
        return
      }

      // Step 7: Specialist Execution & State Aggregation (POST /api/investigation/{sid}/execute)
      const execRes = await measureStep('execute', () => executeInvestigation(sid))
      currentState = execRes.state || currentState
      setState(currentState)

      // Step 8: Phase 8 Reviewer Evidence Evaluation (POST /api/investigation/{sid}/review)
      if (
        !currentState.review_result &&
        ['completed', 'partial', 'investigation_completed'].includes(currentState.execution_status || currentState.investigation_status)
      ) {
        try {
          const revRes = await measureStep('review', () => reviewInvestigation(sid))
          if (revRes && revRes.state) {
            currentState = revRes.state
            setState(currentState)
          }
        } catch (revErr) {
          console.warn('Automatic reviewer evaluation step encountered an error:', revErr)
        }
      }

      // Step 9: Multi-Cycle Re-plan & Follow-up Investigation loop
      while (
        currentState?.review_result &&
        currentState.review_result.decision === 'insufficient' &&
        (currentState.investigation_iterations || 1) < (currentState.max_iterations || 3) &&
        !currentState.is_conclusive
      ) {
        try {
          const replanRes = await measureStep('replan', () => replanInvestigation(sid))
          if (replanRes && replanRes.state) {
            currentState = replanRes.state
            setState(currentState)
          } else {
            break
          }
        } catch (replanErr) {
          console.warn('Replanning loop step encountered an error:', replanErr)
          break
        }
      }

      // Step 10: Phase 9 Diagnosis & Verification Synthesis
      try {
        const diagRes = await measureStep('diagnosis', () => runDiagnosis(sid))
        if (diagRes && diagRes.state) {
          currentState = diagRes.state
          setState(currentState)
        }
      } catch (diagErr) {
        console.warn('Phase 9 diagnosis step encountered an error:', diagErr)
      }

      setSelectedNodeId('investigation_outcome')

      setIsRunning(false)
      setActiveStepKey(null)
      refreshInvestigations()
    } catch (err) {
      setIsRunning(false)
      setActiveStepKey(null)
      setErrorMessage(`Investigation execution error: ${err.message}`)
    }
  }

  // Fast-track full investigation using backend /run-full endpoint
  const handleRunFullInvestigation = async ({
    problem,
    projectZip,
    projectPath,
    repositoryUrl,
    developerContext,
  }) => {
    setIsRunning(true)
    setErrorMessage(null)
    setNodeExecutionTrace({})

    try {
      const startRes = await measureStep('initial_validation', () =>
        startInvestigation({ problem, projectZip, projectPath, repositoryUrl })
      )
      const sid = startRes.session_id
      setSessionId(sid)
      let currentState = startRes.state
      setState(currentState)

      if (startRes.status === 'VALIDATION_FAILED') {
        setIsRunning(false)
        setActiveStepKey(null)
        setErrorMessage(
          'Input validation failed: ' + (currentState.validation_errors || []).join(', ')
        )
        return
      }

      if (developerContext && developerContext.trim()) {
        const ctxRes = await measureStep('context_check', () =>
          addContext(sid, developerContext.trim())
        )
        currentState = ctxRes.state
        setState(currentState)
      }

      if (currentState.context_status !== 'sufficient') {
        setIsRunning(false)
        setActiveStepKey(null)
        return
      }

      // Call backend /run-full endpoint
      const fullRes = await measureStep('run_full', () => runFullInvestigation(sid))
      currentState = fullRes?.state
      if (currentState) {
        if (
          !currentState.review_result &&
          ['completed', 'partial', 'investigation_completed'].includes(currentState.execution_status || currentState.investigation_status)
        ) {
          try {
            const revRes = await measureStep('review', () => reviewInvestigation(sid))
            if (revRes && revRes.state) {
              currentState = revRes.state
            }
          } catch (revErr) {
            console.warn('Reviewer step after run-full encountered an error:', revErr)
          }
        }

        // Complete any pending replanning loops if necessary
        while (
          currentState?.review_result &&
          currentState.review_result.decision === 'insufficient' &&
          (currentState.investigation_iterations || 1) < (currentState.max_iterations || 3) &&
          !currentState.is_conclusive
        ) {
          try {
            const replanRes = await measureStep('replan', () => replanInvestigation(sid))
            if (replanRes && replanRes.state) {
              currentState = replanRes.state
            } else {
              break
            }
          } catch (replanErr) {
            console.warn('Post-full run replanning step encountered an error:', replanErr)
            break
          }
        }

        // Step 10: Phase 9 Diagnosis & Verification Synthesis
        try {
          const diagRes = await measureStep('diagnosis', () => runDiagnosis(sid))
          if (diagRes && diagRes.state) {
            currentState = diagRes.state
          }
        } catch (diagErr) {
          console.warn('Phase 9 diagnosis step after run-full encountered an error:', diagErr)
        }

        setState(currentState)
        setSelectedNodeId('investigation_outcome')
      }

      setIsRunning(false)
      setActiveStepKey(null)
      refreshInvestigations()
    } catch (err) {
      setIsRunning(false)
      setActiveStepKey(null)
      setErrorMessage(`Full run error: ${err.message}`)
    }
  }

  // Run Step-by-Step (starts session and pauses after Step 1)
  const handleRunStepByStep = async ({
    problem,
    projectZip,
    projectPath,
    repositoryUrl,
    developerContext,
  }) => {
    setIsRunning(true)
    setErrorMessage(null)
    try {
      const startRes = await measureStep('initial_validation', () =>
        startInvestigation({ problem, projectZip, projectPath, repositoryUrl })
      )
      const sid = startRes.session_id
      setSessionId(sid)
      let currentState = startRes.state
      setState(currentState)

      if (developerContext && developerContext.trim()) {
        const ctxRes = await measureStep('context_check', () =>
          addContext(sid, developerContext.trim())
        )
        currentState = ctxRes.state
        setState(currentState)
      }

      setIsRunning(false)
      setActiveStepKey(null)
      refreshInvestigations()
    } catch (err) {
      setIsRunning(false)
      setActiveStepKey(null)
      setErrorMessage(`Step execution error: ${err.message}`)
    }
  }

  // Execute single next step
  const handleRunNextStep = async () => {
    if (!sessionId || isRunning || !nextStepName) return
    setIsRunning(true)
    setErrorMessage(null)

    try {
      let res
      if (nextStepName === 'Workspace Setup') {
        res = await measureStep('workspace', () => setupWorkspace(sessionId))
      } else if (nextStepName === 'Repository Analysis') {
        res = await measureStep('repository_analysis', () => analyzeRepository(sessionId))
      } else if (nextStepName === 'Context Gathering') {
        res = await measureStep('context_gathering', () => gatherContext(sessionId))
      } else if (nextStepName === 'Plan Creation & Validation') {
        res = await measureStep('plan', () => fetchPlan(sessionId))
      } else if (nextStepName === 'Specialist Execution & Aggregation') {
        res = await measureStep('execute', () => executeInvestigation(sessionId))
        refreshInvestigations()
      } else if (nextStepName === 'Reviewer Evaluation') {
        res = await measureStep('review', () => reviewInvestigation(sessionId))
        refreshInvestigations()
      } else if (nextStepName === 'Investigation Re-planning') {
        res = await measureStep('replan', () => replanInvestigation(sessionId))
        refreshInvestigations()
      } else if (nextStepName === 'Diagnosis & Verification') {
        res = await measureStep('diagnosis', () => runDiagnosis(sessionId))
        refreshInvestigations()
      }

      if (res && res.state) {
        setState(res.state)
        if (
          nextStepName === 'Specialist Execution & Aggregation' ||
          nextStepName === 'Reviewer Evaluation' ||
          nextStepName === 'Diagnosis & Verification'
        ) {
          setSelectedNodeId('investigation_outcome')
        }
      }
      setIsRunning(false)
      setActiveStepKey(null)
    } catch (err) {
      setIsRunning(false)
      setActiveStepKey(null)
      setErrorMessage(`Error executing ${nextStepName}: ${err.message}`)
    }
  }

  // Handle adding context when prompted
  const handleAddContext = async (contextText) => {
    if (!sessionId || isRunning) return
    setIsRunning(true)
    setErrorMessage(null)
    try {
      const res = await measureStep('context_check', () => addContext(sessionId, contextText))
      if (res && res.state) {
        setState(res.state)
      }
      setIsRunning(false)
      setActiveStepKey(null)
    } catch (err) {
      setIsRunning(false)
      setActiveStepKey(null)
      setErrorMessage(`Context addition error: ${err.message}`)
    }
  }

  // Handle manual / on-demand diagnosis execution
  const handleRunDiagnosis = async () => {
    if (!sessionId || isRunning) return
    setIsRunning(true)
    setErrorMessage(null)
    try {
      const diagRes = await measureStep('diagnosis', () => runDiagnosis(sessionId))
      if (diagRes && diagRes.state) {
        setState(diagRes.state)
        setSelectedNodeId('investigation_outcome')
      }
      setIsRunning(false)
      setActiveStepKey(null)
      refreshInvestigations()
    } catch (err) {
      setIsRunning(false)
      setActiveStepKey(null)
      setErrorMessage(`Diagnosis execution error: ${err.message}`)
    }
  }

  return (
    <div className="app-layout">
      {/* Top Navigation Bar */}
      <header className="app-topbar">
        <div className="topbar-branding">
          <div className="brand-icon">
            <GitGraph size={18} />
          </div>
          <div>
            <h1 className="brand-title">AI Software Issue Investigator</h1>
            <span className="brand-subtitle">Node-by-Node Execution Dashboard</span>
          </div>
        </div>

        {/* Top bar center: Session status indicators */}
        <div className="topbar-center">
          {sessionId ? (
            <div className="topbar-session-info">
              <span className="session-pill font-mono">
                Session: {sessionId.substring(0, 8)}...
              </span>
              {state?.is_conclusive && (
                <span className="badge badge-success">
                  {state.status_label || 'Conclusive'}
                </span>
              )}
            </div>
          ) : (
            <span className="text-muted text-xs">Ready to start investigation</span>
          )}
        </div>

        {/* View toggles */}
        <div className="topbar-actions">
          <div className="view-toggle-group">
            <button
              type="button"
              className={`view-toggle-btn ${currentView === 'dashboard' ? 'active' : ''}`}
              onClick={() => setCurrentView('dashboard')}
            >
              <GitGraph size={14} />
              <span>Execution Timeline</span>
            </button>
            <button
              type="button"
              className={`view-toggle-btn ${currentView === 'report' ? 'active' : ''}`}
              onClick={() => setCurrentView('report')}
              disabled={!state}
              title={!state ? 'Run an investigation to view report' : 'View final report'}
            >
              <FileText size={14} />
              <span>Investigation Report</span>
              {(state?.aggregated_findings?.length > 0 || state?.findings?.length > 0) && (
                <span className="badge-pill">
                  {state.aggregated_findings?.length || state.findings?.length}
                </span>
              )}
            </button>
          </div>
        </div>
      </header>

      {/* Global Error Banner */}
      {errorMessage && (
        <div className="global-error-banner">
          <AlertTriangle size={16} />
          <span>{errorMessage}</span>
          <button
            type="button"
            className="btn-icon-subtle"
            onClick={() => setErrorMessage(null)}
          >
            ×
          </button>
        </div>
      )}

      {/* Main Workspace Area */}
      <main className="app-main">
        {currentView === 'dashboard' ? (
          <div className="three-panel-grid">
            {/* Panel A: Investigation Controls */}
            <LeftPanel
              sessionId={sessionId}
              state={state}
              isRunning={isRunning}
              activeStepKey={activeStepKey}
              onStartInvestigation={handleStartInvestigation}
              onRunFullInvestigation={handleRunFullInvestigation}
              onRunStepByStep={handleRunStepByStep}
              onRunNextStep={handleRunNextStep}
              onAddContext={handleAddContext}
              onSelectSession={loadSession}
              investigationsList={investigationsList}
              onRefreshInvestigations={refreshInvestigations}
              nextStepName={nextStepName}
            />

            {/* Panel B: Node Execution Timeline */}
            <ErrorBoundary fallbackTitle="Timeline Rendering Error">
              <CenterTimeline
                nodes={nodes}
                selectedNodeId={activeNodeId}
                onSelectNode={setSelectedNodeId}
                activeStepKey={activeStepKey}
              />
            </ErrorBoundary>

            {/* Panel C: Selected Node Details */}
            <ErrorBoundary fallbackTitle="Node Inspector Rendering Error">
              <RightInspector node={selectedNode} />
            </ErrorBoundary>
          </div>
        ) : (
          <div className="report-view-wrapper">
            <ErrorBoundary fallbackTitle="Investigation Report Rendering Error">
              <InvestigationReport
                state={state}
                sessionId={sessionId}
                onClose={() => setCurrentView('dashboard')}
                onRunDiagnosis={handleRunDiagnosis}
                isRunning={isRunning}
              />
            </ErrorBoundary>
          </div>
        )}
      </main>
    </div>
  )
}
