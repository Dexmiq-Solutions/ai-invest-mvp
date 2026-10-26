import React, { useState } from 'react'
import {
  FileText,
  CheckCircle2,
  ShieldAlert,
  ShieldCheck,
  Database,
  AlertOctagon,
  Code2,
  GitFork,
  Bug,
  Layers,
  Wrench,
  Copy,
  Check,
  Download,
  RotateCw,
  Info,
  FileCode,
} from 'lucide-react'

function SqlTypeBadge({ sqlType, kind }) {
  if (sqlType === 'read_query' || kind === 'database_read_query') {
    return (
      <span className="badge badge-sm badge-info" title="SQL SELECT read query">
        <Database size={11} className="mr-1 inline" />
        SQL Read (SELECT)
      </span>
    )
  }
  if (sqlType === 'write_operation' || kind === 'database_write_operation') {
    return (
      <span className="badge badge-sm badge-warning" title="SQL Mutation (INSERT/UPDATE/DELETE)">
        <Database size={11} className="mr-1 inline" />
        SQL Mutation (Write)
      </span>
    )
  }
  if (sqlType === 'ddl' || kind === 'database_ddl') {
    return (
      <span className="badge badge-sm badge-neutral" title="SQL DDL schema definition">
        <Database size={11} className="mr-1 inline" />
        SQL DDL
      </span>
    )
  }
  return null
}

function parseMarkdownComponents(md) {
  if (typeof md !== 'string') return []
  const lines = md
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l.startsWith('|') && l.endsWith('|'))

  if (lines.length > 0) {
    const rows = []
    for (const line of lines) {
      if (/^\|[\s:-|]+\|$/.test(line)) continue
      const rawCells = line.split('|').slice(1, -1)
      const cells = rawCells.map((c) =>
        c
          .trim()
          .replace(/^\*\*|\*\*$/g, '')
          .replace(/^`|`$/g, '')
          .trim()
      )
      if (cells.length >= 2 && (cells[0].toLowerCase() === 'component' || cells[0].toLowerCase() === 'name')) {
        continue
      }
      if (cells.length >= 2 && cells[0]) {
        rows.push({
          name: cells[0],
          file: cells[1] || cells[0] || 'N/A',
          role: cells[2] || 'Affected component',
        })
      }
    }
    if (rows.length > 0) return rows
  }

  // Fallback for bullet lines like: - `Component` (file): role
  const bulletLines = md
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l.startsWith('-') || l.startsWith('*'))
  const rows = []
  for (const bl of bulletLines) {
    const clean = bl.replace(/^[-*]\s+/, '').replace(/\*\*/g, '').replace(/`/g, '')
    const match = clean.match(/^([^(:\n]+)(?:\(([^)]+)\))?(?::\s*(.+))?/)
    if (match) {
      rows.push({
        name: match[1]?.trim() || 'Component',
        file: match[2]?.trim() || match[1]?.trim() || 'N/A',
        role: match[3]?.trim() || 'Affected component',
      })
    }
  }
  return rows
}

function getAgentIcon(agentId) {
  if (agentId === 'code_investigator') return <Code2 size={16} className="text-accent" />
  if (agentId === 'dependency_impact_investigator') return <GitFork size={16} className="text-accent" />
  if (agentId === 'runtime_error_investigator') return <Bug size={16} className="text-accent" />
  return <Layers size={16} className="text-accent" />
}

function getAgentFindingsGroups(state, effectiveFindings = []) {
  const plan = state.investigation_plan || []
  const taskResults = state.task_results || {}
  const allCompleted = state.completed_tasks || state.completed_investigation_tasks || []
  const allSkipped = state.skipped_tasks || state.skipped_investigation_tasks || []
  const allFailed = state.failed_tasks || state.failed_investigation_tasks || []
  const allUnresolved = state.unresolved_tasks || []

  const agentFriendlyNames = {
    code_investigator: 'Code Investigator',
    dependency_impact_investigator: 'Dependency / Impact Investigator',
    runtime_error_investigator: 'Runtime / Error Investigator',
  }

  const claimedFindingIndices = new Set()
  const groups = []

  if (plan.length > 0) {
    plan.forEach((task, idx) => {
      const taskId = task.task_id || task.step_id || `TASK-${idx + 1}`
      const taskResult = taskResults[taskId] || {}
      const agentId = taskResult.agent_name || task.assigned_agent || task.investigator || 'code_investigator'
      const agentName = taskResult.investigator_name || agentFriendlyNames[agentId] || agentId
      const objective = taskResult.investigation_task || task.objective || task.instructions || task.task || 'Component analysis'
      const scope = task.scope || task.target_files || []

      const isCompleted = allCompleted.some((t) => t.task_id === taskId || t.step_id === taskId) || task.status === 'completed'
      const isSkipped = allSkipped.some((t) => t.task_id === taskId || t.step_id === taskId) || task.status === 'skipped'
      const isFailed = allFailed.some((t) => t.task_id === taskId || t.step_id === taskId) || task.status === 'failed'
      const isUnresolved = allUnresolved.some((t) => t.task_id === taskId || t.step_id === taskId) || task.status === 'unresolved'

      const status = taskResult.status || (isCompleted ? 'completed' : isUnresolved ? 'unresolved' : isSkipped ? 'skipped' : isFailed ? 'failed' : task.status || 'pending')

      let taskFindings = []
      if (Array.isArray(taskResult.findings) && taskResult.findings.length > 0) {
        taskFindings = taskResult.findings
      } else {
        effectiveFindings.forEach((f, fIdx) => {
          if (
            (f.task_id && f.task_id === taskId) ||
            (!f.task_id && (f.source_investigator === agentId || f.agent_name === agentId))
          ) {
            taskFindings.push(f)
            claimedFindingIndices.add(fIdx)
          }
        })
      }

      groups.push({
        taskId,
        agentId,
        agentName,
        objective,
        scope,
        status,
        findings: taskFindings,
      })
    })
  } else if (Object.keys(taskResults).length > 0) {
    Object.entries(taskResults).forEach(([taskId, tr]) => {
      const agentId = tr.agent_name || 'code_investigator'
      const agentName = tr.investigator_name || agentFriendlyNames[agentId] || agentId
      const objective = tr.investigation_task || 'Component analysis'
      const status = tr.status || 'completed'
      const taskFindings = tr.findings || []

      groups.push({
        taskId,
        agentId,
        agentName,
        objective,
        scope: [],
        status,
        findings: taskFindings,
      })
    })
  }

  // Gather unassociated findings
  const unassociatedFindings = effectiveFindings.filter((_, fIdx) => !claimedFindingIndices.has(fIdx))
  if (unassociatedFindings.length > 0) {
    if (groups.length === 0) {
      const byAgent = {}
      unassociatedFindings.forEach((f) => {
        const ag = f.source_investigator || f.agent_name || 'specialist_agent'
        if (!byAgent[ag]) byAgent[ag] = []
        byAgent[ag].push(f)
      })
      Object.entries(byAgent).forEach(([ag, findingsList]) => {
        groups.push({
          taskId: null,
          agentId: ag,
          agentName: agentFriendlyNames[ag] || ag,
          objective: 'Specialist source code inspection',
          scope: [],
          status: 'completed',
          findings: findingsList,
        })
      })
    } else {
      unassociatedFindings.forEach((f) => {
        const matchingGroup = groups.find((g) => g.agentId === (f.source_investigator || f.agent_name))
        if (matchingGroup) {
          matchingGroup.findings.push(f)
        } else {
          groups.push({
            taskId: f.task_id || null,
            agentId: f.source_investigator || f.agent_name || 'additional_findings',
            agentName: agentFriendlyNames[f.source_investigator || f.agent_name] || f.source_investigator || f.agent_name || 'Specialist Findings',
            objective: 'Additional investigation findings',
            scope: [],
            status: 'completed',
            findings: [f],
          })
        }
      })
    }
  }

  return groups
}

export default function InvestigationReport({
  state,
  sessionId,
  onClose,
  onRunDiagnosis,
  isRunning = false,
}) {
  const [activeTab, setActiveTab] = useState('seven_sections') // 'seven_sections' | 'audit_trail' | 'raw_markdown'
  const [copiedMarkdown, setCopiedMarkdown] = useState(false)

  if (!state) {
    return (
      <div className="report-modal-backdrop">
        <div className="report-card">
          <div className="report-header">
            <h3>Final Investigation Report</h3>
            {onClose && (
              <button type="button" className="btn-close" onClick={onClose}>
                ×
              </button>
            )}
          </div>
          <div className="report-empty">
            No investigation data available yet. Run an investigation first.
          </div>
        </div>
      </div>
    )
  }

  const {
    problem,
    original_problem,
    repository_summary,
    investigation_plan = [],
    plan_validation = null,
    completed_tasks = [],
    completed_investigation_tasks = [],
    findings = [],
    aggregated_findings = [],
    evidence = [],
    evidence_pool = [],
    diagnosis = {},
    likely_root_cause,
    affected_components = [],
    remaining_uncertainty,
    is_conclusive,
    status_label,
    _investigation_status,
    investigation_confidence,
    investigation_stop_reason,
    stop_reason,
    review_result = {},
    investigation_history = [],
    // Phase 9 specific properties:
    final_report = '',
    report_sections = null,
    suggested_fix = null,
    suggested_fix_details = null,
    verification_steps = [],
    verification_status = null,
    verification_summary = null,
  } = state

  const isConclusiveBool = Boolean(is_conclusive ?? diagnosis?.is_conclusive ?? false)
  const confidenceValue = investigation_confidence || state.confidence || diagnosis?.confidence || 'medium'
  const displayStatusLabel = status_label && status_label !== 'Unverified hypothesis'
    ? status_label
    : (diagnosis?.status_label && diagnosis.status_label !== 'Unverified hypothesis'
      ? diagnosis.status_label
      : (isConclusiveBool ? 'Likely root cause' : 'Inconclusive Result'))
  const actualStopReason = stop_reason || investigation_stop_reason || 'Investigation completed after shared state aggregation'

  const effectiveFindings = aggregated_findings.length > 0 ? aggregated_findings : findings
  const effectiveEvidence = evidence_pool.length > 0 ? evidence_pool : evidence

  const agentGroups = getAgentFindingsGroups(state, effectiveFindings)

  const allCompleted = completed_tasks.length > 0 ? completed_tasks : completed_investigation_tasks

  const asSafeObj = (val) => (val && typeof val === 'object' && !Array.isArray(val) ? val : {})

  // -------------------------------------------------------------
  // Section 1: Problem Summary data extraction
  // -------------------------------------------------------------
  const sec1 = asSafeObj(report_sections?.problem_summary)
  const reportedIssueText = sec1.reported_issue || sec1.problem_description || problem || original_problem || 'Not provided'
  const expectedBehaviorText =
    sec1.expected_behavior ||
    state.validated_problem_context?.expected_behavior ||
    state.context_analysis?.expected_behavior ||
    state.problem_context?.expected_behavior ||
    'Expected correct functional behavior without errors.'
  const observedBehaviorText =
    sec1.observed_behavior ||
    state.validated_problem_context?.observed_behavior ||
    state.validated_problem_context?.observable_behavior ||
    state.context_analysis?.observable_behavior ||
    state.problem_context?.observable_behavior ||
    'Application failure or data divergence observed.'
  const rawTargetAreas =
    state.validated_problem_context?.target_areas ||
    state.context_analysis?.target_areas ||
    state.problem_context?.target_areas ||
    []
  const targetAreasList = Array.isArray(rawTargetAreas) ? rawTargetAreas : []

  // -------------------------------------------------------------
  // Section 2: Root-Cause Diagnosis data extraction
  // -------------------------------------------------------------
  const sec2 = asSafeObj(report_sections?.root_cause_diagnosis)
  const resolvedRootCause =
    sec2.most_likely_cause ||
    likely_root_cause ||
    diagnosis?.likely_root_cause ||
    diagnosis?.root_cause ||
    (isConclusiveBool ? 'Defect established by corroborating causal evidence' : 'Inconclusive: insufficient evidence to establish root cause')
  const diagnosisStatusBadge = sec2.status || displayStatusLabel
  const causalExplanationText =
    sec2.causal_explanation ||
    diagnosis?.causal_explanation ||
    diagnosis?.reasoning ||
    review_result?.reasoning ||
    null

  // -------------------------------------------------------------
  // Section 3: Supporting Evidence data extraction
  // -------------------------------------------------------------
  const sec3 = asSafeObj(report_sections?.supporting_evidence)
  const rawCausalEvidence =
    sec3.causal_defect_evidence ||
    state.supporting_evidence ||
    diagnosis?.supporting_evidence ||
    effectiveEvidence.filter(
      (ev) =>
        ev.relationship_to_hypothesis === 'causal' ||
        ev.is_defect_evidence ||
        String(ev.classification || '').toLowerCase().includes('defect')
    )
  const causalDefectEvidence = Array.isArray(rawCausalEvidence) ? rawCausalEvidence : []

  const rawContextualEvidence =
    sec3.contextual_evidence ||
    effectiveEvidence.filter(
      (ev) =>
        ev.relationship_to_hypothesis !== 'causal' &&
        !ev.is_defect_evidence &&
        !String(ev.classification || '').toLowerCase().includes('defect')
    )
  const contextualEvidence = Array.isArray(rawContextualEvidence) ? rawContextualEvidence : []

  const contradictoryEvidence =
    sec3.contradictory_evidence ||
    diagnosis?.contradictory_evidence ||
    review_result?.contradictory_evidence ||
    null

  // -------------------------------------------------------------
  // Section 4: Affected Components data extraction
  // -------------------------------------------------------------
  const rawSec4 = report_sections?.affected_components ?? diagnosis?.affected_components
  let sec4ComponentsList = []

  if (Array.isArray(rawSec4)) {
    sec4ComponentsList = rawSec4
  } else if (rawSec4 && typeof rawSec4 === 'object' && Array.isArray(rawSec4.components)) {
    sec4ComponentsList = rawSec4.components
  } else if (typeof rawSec4 === 'string' && (rawSec4.includes('|') || rawSec4.includes('-'))) {
    sec4ComponentsList = parseMarkdownComponents(rawSec4)
  } else if (Array.isArray(affected_components) && affected_components.length > 0) {
    sec4ComponentsList = affected_components
  }

  const sec4Components = sec4ComponentsList.map((c, idx) => {
    if (typeof c === 'string') {
      return { name: c, file: c, role: 'Affected component' }
    }
    return {
      name: c?.name || c?.symbol || c?.file || `Component #${idx + 1}`,
      file: c?.file || c?.file_path || c?.source || 'N/A',
      role: c?.role || 'Affected component',
    }
  })

  // -------------------------------------------------------------
  // Section 5: Suggested Fix data extraction
  // -------------------------------------------------------------
  const sec5 = asSafeObj(report_sections?.suggested_fix) || suggested_fix_details || {}
  const fixTargetLocation = sec5.target_location || null
  const fixRecommendedChange = sec5.recommended_change || (typeof suggested_fix === 'string' && !suggested_fix.includes('return ') ? suggested_fix : null)
  const fixRationale = sec5.rationale || null
  const fixCodeSnippet =
    sec5.code_snippet ||
    (typeof suggested_fix === 'string' && (suggested_fix.includes('\n') || suggested_fix.includes('return') || suggested_fix.includes('(')) ? suggested_fix : null)
  const fixContractInvariants = sec5.contract_invariants || null
  const fixPotentialRegressions = Array.isArray(sec5.potential_regressions) ? sec5.potential_regressions : []

  // -------------------------------------------------------------
  // Section 6: Verification Steps data extraction
  // -------------------------------------------------------------
  const sec6 = asSafeObj(report_sections?.verification_steps) || verification_summary || {}
  const effectiveVerificationStatus =
    sec6.status ||
    verification_status ||
    verification_summary?.status ||
    (isConclusiveBool ? 'partially verified' : 'not verified')
  const executionBlockers = Array.isArray(sec6.execution_blockers)
    ? sec6.execution_blockers
    : Array.isArray(verification_summary?.blockers)
    ? verification_summary.blockers
    : []
  const executedTests = Array.isArray(sec6.executed_tests)
    ? sec6.executed_tests
    : Array.isArray(verification_summary?.executed_tests)
    ? verification_summary.executed_tests
    : []
  const rawProposedChecks =
    sec6.proposed_checks ||
    verification_steps ||
    verification_summary?.proposed_checks ||
    []
  const proposedChecks = Array.isArray(rawProposedChecks) ? rawProposedChecks : []

  // -------------------------------------------------------------
  // Section 7: Remaining Uncertainty data extraction
  // -------------------------------------------------------------
  const sec7 = asSafeObj(report_sections?.remaining_uncertainty)
  const rawUnresolvedQuestions =
    sec7.unresolved_questions ||
    state.unanswered_questions ||
    review_result?.unanswered_questions ||
    []
  const unresolvedQuestions = Array.isArray(rawUnresolvedQuestions)
    ? rawUnresolvedQuestions
    : (typeof rawUnresolvedQuestions === 'string' && rawUnresolvedQuestions ? [rawUnresolvedQuestions] : [])
  const nextValidationSteps =
    sec7.next_steps ||
    (typeof remaining_uncertainty === 'string' ? remaining_uncertainty : null) ||
    'Execute proposed verification checks in an isolated runtime environment with debug logging enabled.'

  // Copy Markdown Report handler
  const handleCopyMarkdown = () => {
    const textToCopy = final_report || generateClientMarkdownReport()
    navigator.clipboard.writeText(textToCopy)
    setCopiedMarkdown(true)
    setTimeout(() => setCopiedMarkdown(false), 2000)
  }

  // Download Report handler
  const handleDownloadReport = () => {
    const textToDownload = final_report || generateClientMarkdownReport()
    const blob = new Blob([textToDownload], { type: 'text/markdown;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.setAttribute('download', `investigation_report_${(sessionId || 'session').slice(0, 8)}.md`)
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
  }

  // Fallback client-side markdown generator if backend markdown wasn't returned
  function generateClientMarkdownReport() {
    return `# Final Investigation Report

## 1. Problem Summary
${reportedIssueText}
- Expected Behavior: ${expectedBehaviorText}
- Observed Behavior: ${observedBehaviorText}

## 2. Root-Cause Diagnosis
- Most Likely Cause: ${resolvedRootCause}
- Status: ${diagnosisStatusBadge} (Confidence: ${confidenceValue})
- Causal Explanation: ${causalExplanationText || 'None provided'}

## 3. Supporting Evidence
${(causalDefectEvidence || []).map((e) => `- ${e.source || e.path}:${e.line ?? '?'}: ${e.snippet || e.matched_text || ''}`).join('\n')}

## 4. Affected Components
${sec4Components.map((c) => `- ${c.name || c.file} (${c.role || 'component'})`).join('\n')}

## 5. Suggested Fix
${fixRecommendedChange || 'None'}
\`\`\`
${fixCodeSnippet || ''}
\`\`\`

## 6. Verification Steps
- Status: ${effectiveVerificationStatus}
${proposedChecks.map((p) => `- ${typeof p === 'object' ? p.title || p.instructions : p}`).join('\n')}

## 7. Remaining Uncertainty
${Array.isArray(unresolvedQuestions) ? unresolvedQuestions.join('\n') : unresolvedQuestions || 'None'}
`
  }

  return (
    <div className="report-container">
      {/* Top Header */}
      <div className="report-header">
        <div className="report-title-group">
          <FileText size={22} className="text-accent" />
          <div>
            <h2 className="report-main-title">Final Investigation Report</h2>
            <div className="report-meta">
              <span>Session: <strong className="font-mono">{sessionId || 'N/A'}</strong></span>
              <span>·</span>
              <span>Status: <strong>{displayStatusLabel}</strong></span>
              <span>·</span>
              <span>Confidence: <strong className="uppercase">{confidenceValue}</strong></span>
              <span>·</span>
              <span>Verification: <strong>{effectiveVerificationStatus}</strong></span>
            </div>
          </div>
        </div>

        <div className="report-toolbar-actions">
          {onRunDiagnosis && (
            <button
              type="button"
              className="btn-report-tool"
              onClick={onRunDiagnosis}
              disabled={isRunning}
              title="Trigger Phase 9 Diagnosis & Verification synthesis on this session"
            >
              {isRunning ? (
                <>
                  <RotateCw size={13} className="spinner" />
                  <span>Synthesizing...</span>
                </>
              ) : (
                <>
                  <Wrench size={13} className="text-accent" />
                  <span>Run Phase 9 Diagnosis</span>
                </>
              )}
            </button>
          )}

          <button
            type="button"
            className="btn-report-tool"
            onClick={handleCopyMarkdown}
            title="Copy entire markdown report to clipboard"
          >
            {copiedMarkdown ? (
              <>
                <Check size={13} className="text-success" />
                <span className="text-success">Copied!</span>
              </>
            ) : (
              <>
                <Copy size={13} />
                <span>Copy Markdown</span>
              </>
            )}
          </button>

          <button
            type="button"
            className="btn-report-tool"
            onClick={handleDownloadReport}
            title="Download report as .md file"
          >
            <Download size={13} />
            <span>Download .md</span>
          </button>

          {onClose && (
            <button type="button" className="btn-close" onClick={onClose} title="Close Report">
              ×
            </button>
          )}
        </div>
      </div>

      {/* OVERALL INVESTIGATION STATUS BANNER */}
      <div
        className={`report-status-banner ${
          isConclusiveBool ? 'banner-conclusive' : 'banner-inconclusive'
        }`}
      >
        <div className="banner-icon-col">
          {isConclusiveBool ? (
            <CheckCircle2 size={24} className="text-success" />
          ) : (
            <ShieldAlert size={24} className="text-warning" />
          )}
        </div>
        <div className="banner-content-col">
          <div className="banner-title-row">
            <h3 className="banner-title">{displayStatusLabel}</h3>
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="badge badge-outline">
                Confidence: {confidenceValue.toUpperCase()}
              </span>
              <span
                className={`badge badge-sm ${
                  effectiveVerificationStatus === 'verified'
                    ? 'badge-success'
                    : effectiveVerificationStatus === 'verification blocked'
                    ? 'badge-failed'
                    : effectiveVerificationStatus === 'partially verified'
                    ? 'badge-warning'
                    : 'badge-outline'
                }`}
              >
                Verification: {effectiveVerificationStatus.toUpperCase()}
              </span>
            </div>
          </div>
          <p className="banner-desc">
            {isConclusiveBool
              ? 'Conclusive root cause established with corroborating multi-component causal defect evidence.'
              : 'Intermediate / Inconclusive Result: Findings represent candidate hypotheses based on static source inspection; empirical runtime trace or dynamic verification has not yet confirmed the conclusion.'}
          </p>
          {actualStopReason && (
            <div className="banner-stop-reason">
              <span className="text-muted text-xs">Workflow Stop Reason:</span>{' '}
              <span className="text-xs font-mono">{actualStopReason}</span>
            </div>
          )}
        </div>
      </div>

      {/* Navigation Tabs */}
      <nav className="report-tabs">
        <button
          type="button"
          className={`report-tab-btn ${activeTab === 'seven_sections' ? 'active' : ''}`}
          onClick={() => setActiveTab('seven_sections')}
        >
          <FileText size={15} />
          <span>Investigation Report (7 Sections)</span>
        </button>
        <button
          type="button"
          className={`report-tab-btn ${activeTab === 'audit_trail' ? 'active' : ''}`}
          onClick={() => setActiveTab('audit_trail')}
        >
          <Layers size={15} />
          <span>Specialist & Evidence Audit</span>
          {effectiveFindings.length > 0 && (
            <span className="badge-pill">{effectiveFindings.length}</span>
          )}
        </button>
        <button
          type="button"
          className={`report-tab-btn ${activeTab === 'raw_markdown' ? 'active' : ''}`}
          onClick={() => setActiveTab('raw_markdown')}
        >
          <FileCode size={15} />
          <span>Raw Markdown Report</span>
        </button>
      </nav>

      {/* TAB 1: 7 SECTIONS IN EXACT ORDER */}
      {activeTab === 'seven_sections' && (
        <div className="report-body">
          {/* 1. Problem Summary */}
          <section className="report-section">
            <h4 className="report-section-title">1. Problem Summary</h4>
            <div className="report-text-block font-medium mb-3">
              <strong>Reported Issue: </strong>
              <span>{reportedIssueText}</span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-2">
              <div className="p-3 border rounded bg-surface text-xs">
                <span className="font-bold text-success uppercase tracking-wider block mb-1">
                  Expected Behavior
                </span>
                <p className="text-secondary">{expectedBehaviorText}</p>
              </div>

              <div className="p-3 border rounded bg-surface text-xs">
                <span className="font-bold text-warning uppercase tracking-wider block mb-1">
                  Observed Behavior
                </span>
                <p className="text-secondary">{observedBehaviorText}</p>
              </div>
            </div>

            {targetAreasList.length > 0 && (
              <div className="mt-2 text-xs">
                <span className="text-muted font-semibold">Target Subsystems / Areas: </span>
                <span className="font-mono text-secondary">{targetAreasList.join(', ')}</span>
              </div>
            )}

            {state.developer_context && (
              <div className="mt-2 text-xs text-secondary border-t pt-1.5">
                <strong>Provided Developer Context: </strong>
                <span>
                  {typeof state.developer_context === 'string'
                    ? state.developer_context
                    : JSON.stringify(state.developer_context)}
                </span>
              </div>
            )}
          </section>

          {/* 2. Root-Cause Diagnosis */}
          <section className="report-section">
            <h4 className="report-section-title">2. Root-Cause Diagnosis</h4>
            <div
              className={`p-3.5 border rounded-md mb-2 ${
                isConclusiveBool ? 'bg-emerald-50 border-emerald-200' : 'bg-amber-50 border-amber-200'
              }`}
            >
              <div className="flex items-center justify-between mb-1.5 flex-wrap gap-1">
                <span className="text-xs font-bold uppercase tracking-wider text-secondary">
                  Most Likely Root Cause
                </span>
                <div className="flex items-center gap-1.5">
                  <span
                    className={`badge badge-sm ${
                      isConclusiveBool ? 'badge-success' : 'badge-warning'
                    }`}
                  >
                    {diagnosisStatusBadge}
                  </span>
                  <span className="badge badge-sm badge-outline">
                    Confidence: {confidenceValue.toUpperCase()}
                  </span>
                </div>
              </div>

              <h5
                className={`text-base font-bold mb-1.5 ${
                  isConclusiveBool ? 'text-emerald-900' : 'text-amber-900'
                }`}
              >
                {resolvedRootCause}
              </h5>

              {causalExplanationText && (
                <div className="mt-2 pt-2 border-t border-dashed border-gray-300 text-xs text-secondary leading-relaxed">
                  <strong className="text-primary block mb-1">Causal Explanation:</strong>
                  <p>{causalExplanationText}</p>
                </div>
              )}
            </div>
          </section>

          {/* 3. Supporting Evidence */}
          <section className="report-section">
            <h4 className="report-section-title">
              3. Supporting Evidence ({causalDefectEvidence.length + (contextualEvidence?.length || 0)})
            </h4>

            {/* Causal Defect Evidence */}
            <div className="mb-3">
              <span className="text-xs font-bold text-secondary uppercase tracking-wider block mb-1.5">
                Causal / Defect Evidence ({causalDefectEvidence.length})
              </span>
              {causalDefectEvidence.length === 0 ? (
                <div className="text-muted text-xs italic p-2 border rounded bg-surface">
                  No direct causal defect evidence captured in this phase.
                </div>
              ) : (
                <div className="flex flex-col gap-2">
                  {causalDefectEvidence.map((ev, i) => (
                    <div key={ev.evidence_id || i} className="p-2.5 border rounded bg-surface text-xs">
                      <div className="flex items-center justify-between font-mono font-bold mb-1">
                        <span className="text-accent">{ev.evidence_id || `EV-${i + 1}`}</span>
                        <span className="text-secondary">{ev.source || ev.path}:{ev.line ?? '?'}</span>
                      </div>
                      {(ev.snippet || ev.matched_text) && (
                        <pre className="code-snippet font-mono text-xs my-1">{ev.snippet || ev.matched_text}</pre>
                      )}
                      {ev.relationship_to_hypothesis && (
                        <div className="text-muted text-xs mt-1">
                          Relationship: <strong className="text-secondary">{ev.relationship_to_hypothesis}</strong>
                        </div>
                      )}
                      {(ev.explanation || ev.rationale) && (
                        <div className="text-secondary text-xs mt-0.5">
                          <strong>Causal Link: </strong>{ev.explanation || ev.rationale}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Contextual Observations */}
            {contextualEvidence && contextualEvidence.length > 0 && (
              <div className="mb-3">
                <span className="text-xs font-bold text-muted uppercase tracking-wider block mb-1">
                  Contextual Observations ({contextualEvidence.length})
                </span>
                <div className="evidence-scroll-table">
                  <table className="report-table text-xs">
                    <thead>
                      <tr>
                        <th>Source</th>
                        <th>Classification</th>
                        <th>Snippet</th>
                      </tr>
                    </thead>
                    <tbody>
                      {contextualEvidence.slice(0, 10).map((ev, i) => (
                        <tr key={i}>
                          <td className="font-mono text-xs">{ev.source || ev.path}:{ev.line ?? '?'}</td>
                          <td><SqlTypeBadge sqlType={ev.sql_type} kind={ev.kind} /></td>
                          <td>
                            {(ev.snippet || ev.matched_text) ? (
                              <pre className="code-snippet font-mono text-xs">{ev.snippet || ev.matched_text}</pre>
                            ) : (
                              <span className="text-muted">—</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {contextualEvidence.length > 10 && (
                    <div className="p-1.5 text-xs text-muted text-center">
                      Showing 10 of {contextualEvidence.length} contextual observations. View full list in Specialist Audit tab.
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Contradictory Evidence */}
            <div className="p-2.5 border rounded bg-surface text-xs">
              <strong className="text-secondary block mb-0.5">Contradictory Evidence Assessment:</strong>
              {contradictoryEvidence && (Array.isArray(contradictoryEvidence) ? contradictoryEvidence.length > 0 : String(contradictoryEvidence).length > 0) ? (
                <div className="text-danger mt-1">
                  {Array.isArray(contradictoryEvidence) ? (
                    <ul className="list-disc pl-4">
                      {contradictoryEvidence.map((cev, idx) => (
                        <li key={idx}>{typeof cev === 'string' ? cev : `${cev.source}:${cev.line} - ${cev.snippet}`}</li>
                      ))}
                    </ul>
                  ) : (
                    <p>{String(contradictoryEvidence)}</p>
                  )}
                </div>
              ) : (
                <p className="text-muted">
                  No contradictory evidence was found; all accumulated findings corroborate the diagnosis.
                </p>
              )}
            </div>
          </section>

          {/* 4. Affected Components */}
          <section className="report-section">
            <h4 className="report-section-title">
              4. Affected Components ({sec4Components.length})
            </h4>
            {sec4Components.length === 0 ? (
              <div className="text-muted text-xs italic">No specific affected components identified.</div>
            ) : (
              <div className="table-wrapper">
                <table className="report-table text-xs">
                  <thead>
                    <tr>
                      <th>Component / Symbol</th>
                      <th>File Path</th>
                      <th>Role in Failure</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sec4Components.map((comp, idx) => {
                      const name = comp.name || comp.symbol || comp.file || `Component #${idx + 1}`
                      const file = comp.file || comp.file_path || comp.source || 'N/A'
                      const role = comp.role || 'Inspected Component'
                      const isDefectSite = role.toLowerCase().includes('defect')
                      return (
                        <tr key={idx} className={isDefectSite ? 'bg-red-50' : ''}>
                          <td className="font-bold text-accent">{name}</td>
                          <td className="font-mono text-xs">{file}</td>
                          <td>
                            <span
                              className={`badge badge-sm ${
                                isDefectSite
                                  ? 'badge-failed'
                                  : role.toLowerCase().includes('caller')
                                  ? 'badge-warning'
                                  : 'badge-neutral'
                              }`}
                            >
                              {role}
                            </span>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* 5. Suggested Fix */}
          <section className="report-section">
            <h4 className="report-section-title">5. Suggested Fix</h4>
            <div className="fix-card">
              {fixTargetLocation && (
                <div className="fix-header-row">
                  <span className="text-xs text-muted font-semibold">Target Location:</span>
                  <span className="fix-target-pill">{fixTargetLocation}</span>
                </div>
              )}

              {fixRecommendedChange ? (
                <div className="mb-2 text-xs">
                  <strong className="text-primary block mb-0.5">Recommended Change:</strong>
                  <p className="text-secondary">{fixRecommendedChange}</p>
                </div>
              ) : (
                <div className="text-muted text-xs italic mb-2">
                  No automated code fix recommendation available. Review investigation findings.
                </div>
              )}

              {fixRationale && (
                <div className="mb-2 text-xs text-muted border-t pt-1.5">
                  <strong className="text-secondary">Correction Rationale: </strong>
                  <span>{fixRationale}</span>
                </div>
              )}

              {fixCodeSnippet && (
                <div className="fix-snippet-wrapper">
                  <div className="fix-snippet-title">Illustrative Code Snippet</div>
                  <pre className="fix-snippet-code">{fixCodeSnippet}</pre>
                </div>
              )}

              <div className="fix-contract-grid">
                {fixContractInvariants && (
                  <div className="fix-subblock fix-subblock-invariants">
                    <strong className="block mb-0.5">Contract Invariants to Preserve:</strong>
                    <span>{fixContractInvariants}</span>
                  </div>
                )}

                {fixPotentialRegressions && fixPotentialRegressions.length > 0 && (
                  <div className="fix-subblock fix-subblock-regressions">
                    <strong className="block mb-0.5">Potential Regressions to Check:</strong>
                    <ul className="list-disc pl-4 mt-0.5">
                      {fixPotentialRegressions.map((reg, idx) => (
                        <li key={idx}>{reg}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>

              <div className="fix-scope-alert">
                <Info size={14} className="text-accent flex-shrink-0" />
                <span>
                  <strong>Scope Restriction:</strong> Fix recommendation only. The target application's source code was not modified. Proposed changes require runtime testing prior to deployment.
                </span>
              </div>
            </div>
          </section>

          {/* 6. Verification Steps */}
          <section className="report-section">
            <div className="flex items-center justify-between mb-2">
              <h4 className="report-section-title mb-0">6. Verification Steps</h4>
              <span
                className={`badge ${
                  effectiveVerificationStatus === 'verified'
                    ? 'badge-success'
                    : effectiveVerificationStatus === 'verification blocked'
                    ? 'badge-failed'
                    : effectiveVerificationStatus === 'partially verified'
                    ? 'badge-warning'
                    : 'badge-outline'
                }`}
              >
                Status: {effectiveVerificationStatus.toUpperCase()}
              </span>
            </div>

            {/* Execution Blockers */}
            {executionBlockers.length > 0 && (
              <div className="verification-blocker-alert mb-3">
                <div className="flex items-center gap-1.5 font-bold mb-1">
                  <AlertOctagon size={16} />
                  <span>Execution Blockers ({executionBlockers.length})</span>
                </div>
                <ul>
                  {executionBlockers.map((b, i) => (
                    <li key={i}>{b}</li>
                  ))}
                </ul>
              </div>
            )}

            {/* Executed Tests / Probes */}
            {executedTests.length > 0 && (
              <div className="mb-3">
                <span className="text-xs font-bold text-secondary uppercase tracking-wider block mb-1">
                  Executed Tests / Empirical Probes ({executedTests.length})
                </span>
                <div className="flex flex-col gap-1.5">
                  {executedTests.map((t, idx) => (
                    <div key={idx} className="probe-run-card">
                      <div className="flex items-center justify-between text-xs font-mono text-accent mb-1">
                        <span>{t.command || t.test_name || `Probe #${idx + 1}`}</span>
                        <span>Exit: {t.exit_code ?? 0}</span>
                      </div>
                      {t.output && <pre className="text-xs text-gray-300 overflow-x-auto">{t.output}</pre>}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Proposed Actionable Checks */}
            <div>
              <span className="text-xs font-bold text-secondary uppercase tracking-wider block mb-1.5">
                Proposed Actionable Checks ({proposedChecks.length})
              </span>
              {proposedChecks.length === 0 ? (
                <div className="text-muted text-xs italic p-2 border rounded bg-surface">
                  No proposed verification checks recorded.
                </div>
              ) : (
                <div className="checks-grid">
                  {proposedChecks.map((step, idx) => {
                    const isObj = typeof step === 'object' && step !== null
                    const title = isObj ? (step.title || step.name || `Check #${idx + 1}`) : `Check #${idx + 1}`
                    const instructions = isObj ? (step.instructions || step.description || step.steps) : step
                    const expectedOutcome = isObj ? step.expected_outcome : null
                    const edgeCases = isObj ? step.edge_cases : null

                    return (
                      <div key={idx} className="check-card">
                        <div className="check-card-header font-bold text-accent">
                          <span>{title}</span>
                          <span className="badge badge-sm badge-neutral">Proposed Check</span>
                        </div>
                        <p className="text-secondary mt-1">{instructions}</p>
                        {expectedOutcome && (
                          <div className="text-xs text-success mt-1.5 p-1.5 bg-emerald-50 rounded">
                            <strong>Expected Outcome: </strong>{expectedOutcome}
                          </div>
                        )}
                        {edgeCases && (
                          <div className="text-xs text-muted mt-1">
                            <strong>Edge Cases to Validate: </strong>
                            {Array.isArray(edgeCases) ? edgeCases.join(', ') : edgeCases}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </section>

          {/* 7. Remaining Uncertainty */}
          <section className="report-section">
            <h4 className="report-section-title">7. Remaining Uncertainty</h4>
            <div className="p-3 border rounded bg-surface text-xs">
              <strong className="text-secondary block mb-1">Unresolved Questions & Gaps:</strong>
              {Array.isArray(unresolvedQuestions) && unresolvedQuestions.length > 0 ? (
                <ul className="list-disc pl-4 text-secondary mb-2">
                  {unresolvedQuestions.map((q, i) => (
                    <li key={i}>{q}</li>
                  ))}
                </ul>
              ) : (
                <p className="text-muted mb-2">
                  {typeof unresolvedQuestions === 'string' && unresolvedQuestions
                    ? unresolvedQuestions
                    : isConclusiveBool
                    ? 'No significant residual uncertainty noted. Corroborating code evidence validates defect location.'
                    : 'Evidence gaps remain: runtime trace or dynamic reproducer needed to conclusively verify defect.'}
                </p>
              )}

              <div className="border-t pt-2 mt-2">
                <strong className="text-accent block mb-0.5">Recommended Next Validation Steps:</strong>
                <p className="text-secondary">{nextValidationSteps}</p>
              </div>
            </div>
          </section>
        </div>
      )}

      {/* TAB 2: SPECIALIST & EVIDENCE AUDIT */}
      {activeTab === 'audit_trail' && (
        <div className="report-body">
          {/* Repository Architecture */}
          <section className="report-section">
            <h4 className="report-section-title">Repository & Workspace Context</h4>
            {repository_summary && Object.keys(repository_summary).length > 0 ? (
              <div className="repo-summary-grid">
                <div className="repo-stat-card">
                  <span className="stat-label">Languages</span>
                  <span className="stat-value">{repository_summary.languages?.join(', ') || 'N/A'}</span>
                </div>
                <div className="repo-stat-card">
                  <span className="stat-label">Frameworks</span>
                  <span className="stat-value">{repository_summary.frameworks?.join(', ') || 'None detected'}</span>
                </div>
                <div className="repo-stat-card">
                  <span className="stat-label">Entry Points</span>
                  <span className="stat-value">{repository_summary.entry_points?.join(', ') || 'None'}</span>
                </div>
                <div className="repo-stat-card">
                  <span className="stat-label">Workspace Files</span>
                  <span className="stat-value">
                    {repository_summary.project_structure?.file_count ?? 0} files,{' '}
                    {repository_summary.project_structure?.directory_count ?? 0} dirs
                  </span>
                </div>
              </div>
            ) : (
              <div className="text-muted text-xs">Not provided</div>
            )}
          </section>

          {/* Context-Aware Plan Validation Gate */}
          {plan_validation && (
            <section className="report-section">
              <h4 className="report-section-title">Context-Aware Plan Validation Gate</h4>
              <div className={`p-3 rounded-md border ${plan_validation.is_valid ? 'bg-emerald-50 border-emerald-200' : 'bg-red-50 border-red-200'}`}>
                <div className="flex items-center gap-2 mb-1">
                  {plan_validation.is_valid ? (
                    <>
                      <ShieldCheck size={18} className="text-emerald-600" />
                      <strong className="text-emerald-800 text-sm">Pre-Dispatch Gate: Approved ({plan_validation.validated_task_count ?? investigation_plan.length} tasks validated)</strong>
                    </>
                  ) : (
                    <>
                      <AlertOctagon size={18} className="text-red-600" />
                      <strong className="text-red-800 text-sm">Pre-Dispatch Gate: Rejected (Specialist dispatch blocked)</strong>
                    </>
                  )}
                </div>
                <p className="text-xs text-muted">
                  {plan_validation.is_valid
                    ? 'All tasks satisfy schema completeness, agent assignment validity, and scope relevance against gathered context.'
                    : 'Zero specialists dispatched to prevent corrupted execution on invalid tasks.'}
                </p>
              </div>
            </section>
          )}

          {/* Specialist Tasks & Execution Statuses */}
          <section className="report-section">
            <h4 className="report-section-title">Specialist Tasks & Statuses ({investigation_plan.length})</h4>
            <div className="table-wrapper">
              <table className="report-table text-xs">
                <thead>
                  <tr>
                    <th>Task ID</th>
                    <th>Specialist</th>
                    <th>Objective</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {investigation_plan.map((t, idx) => {
                    const taskId = t.task_id || t.step_id || `TASK-${idx + 1}`
                    const isDone = allCompleted.some((c) => c.task_id === taskId || c.step_id === taskId)
                    return (
                      <tr key={idx}>
                        <td className="font-mono font-bold text-accent">{taskId}</td>
                        <td>{t.assigned_agent || t.investigator}</td>
                        <td>{t.objective || t.instructions || t.task}</td>
                        <td>
                          <span className={`badge badge-sm ${isDone ? 'badge-success' : 'badge-neutral'}`}>
                            {isDone ? 'completed' : t.status || 'pending'}
                          </span>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </section>

          {/* Findings by Deep Agent */}
          <section className="report-section">
            <h4 className="report-section-title">Findings by Deep Agent ({effectiveFindings.length})</h4>
            <div className="agent-groups-container">
              {agentGroups.map((group, gIdx) => (
                <div key={group.taskId || gIdx} className="agent-findings-block">
                  <div className="agent-findings-header">
                    <div className="agent-findings-title">
                      {getAgentIcon(group.agentId)}
                      <span>{group.agentName}</span>
                      {group.taskId && <span className="investigator-pill font-mono text-xs">{group.taskId}</span>}
                    </div>
                    <span className={`badge badge-sm badge-${group.status || 'completed'}`}>{group.status || 'completed'}</span>
                  </div>

                  <div className="agent-findings-body">
                    {group.findings.length === 0 ? (
                      <div className="agent-empty-findings">No specific findings reported.</div>
                    ) : (
                      <div className="findings-report-grid">
                        {group.findings.map((f, i) => (
                          <div
                            key={i}
                            className={`finding-report-card ${
                              f.category?.toLowerCase().includes('defect') ? 'card-defect' : 'card-observation'
                            }`}
                          >
                            <div className="finding-report-header">
                              <span className="finding-type-tag">{f.category || 'Finding'}</span>
                              {f.confidence && <span className="badge badge-sm badge-outline">{f.confidence}</span>}
                            </div>
                            <div className="finding-report-statement font-medium text-xs">
                              {f.statement || f.finding || f.description}
                            </div>
                            {f.file_path && (
                              <div className="mt-1 text-xs font-mono text-accent">
                                {f.file_path}:{f.line_number ?? '?'}
                              </div>
                            )}
                            {(f.snippet || f.matched_text) && (
                              <pre className="code-snippet font-mono text-xs mt-1">{f.snippet || f.matched_text}</pre>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </section>

          {/* Discovered Evidence Pool */}
          <section className="report-section">
            <h4 className="report-section-title">Evidence Pool ({effectiveEvidence.length})</h4>
            <div className="evidence-scroll-table">
              <table className="report-table text-xs">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Source & Line</th>
                    <th>Type</th>
                    <th>Code Excerpt</th>
                  </tr>
                </thead>
                <tbody>
                  {effectiveEvidence.slice(0, 30).map((e, idx) => (
                    <tr key={idx}>
                      <td className="font-mono font-bold text-accent">{e.evidence_id || `E${idx + 1}`}</td>
                      <td className="font-mono text-xs">{e.source || e.path}:{e.line ?? '?'}</td>
                      <td><SqlTypeBadge sqlType={e.sql_type} kind={e.kind} /></td>
                      <td>
                        {(e.matched_text || e.snippet) ? (
                          <pre className="code-snippet font-mono text-xs">{e.matched_text || e.snippet}</pre>
                        ) : (
                          <span className="text-muted">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          {/* Reviewer Evaluation & Evidence Sufficiency */}
          {review_result && Object.keys(review_result).length > 0 && (
            <section className="report-section">
              <h4 className="report-section-title">Reviewer Evaluation & Sufficiency (Phase 8)</h4>
              <div className="p-3 border rounded bg-surface text-xs mb-2">
                <div className="flex items-center justify-between mb-1">
                  <strong>Reviewer Decision: {review_result.decision?.toUpperCase()}</strong>
                  <span className={`badge badge-sm ${review_result.is_sufficient ? 'badge-success' : 'badge-warning'}`}>
                    {review_result.is_sufficient ? 'Sufficient' : 'Insufficient'}
                  </span>
                </div>
                <p className="text-secondary">{review_result.reasoning || review_result.feedback}</p>
              </div>
            </section>
          )}

          {/* Investigation Re-planning History */}
          {investigation_history && investigation_history.length > 0 && (
            <section className="report-section">
              <h4 className="report-section-title">Re-planning History ({investigation_history.length})</h4>
              <div className="table-wrapper">
                <table className="report-table text-xs">
                  <thead>
                    <tr>
                      <th>Iteration</th>
                      <th>Tasks Added</th>
                      <th>Review Decision</th>
                      <th>Feedback</th>
                    </tr>
                  </thead>
                  <tbody>
                    {investigation_history.map((h, i) => (
                      <tr key={i}>
                        <td className="font-mono font-bold">Iter {h.iteration}</td>
                        <td>{h.tasks_added} new tasks</td>
                        <td><span className="badge badge-sm badge-neutral">{h.review_decision}</span></td>
                        <td className="text-secondary">{h.feedback}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </div>
      )}

      {/* TAB 3: RAW MARKDOWN REPORT */}
      {activeTab === 'raw_markdown' && (
        <div className="report-body">
          <section className="report-section">
            <div className="flex items-center justify-between mb-2">
              <h4 className="report-section-title mb-0">Raw Markdown Investigation Report</h4>
              <div className="flex items-center gap-2">
                <button type="button" className="btn-report-tool" onClick={handleCopyMarkdown}>
                  {copiedMarkdown ? <Check size={13} className="text-success" /> : <Copy size={13} />}
                  <span>{copiedMarkdown ? 'Copied!' : 'Copy'}</span>
                </button>
                <button type="button" className="btn-report-tool" onClick={handleDownloadReport}>
                  <Download size={13} />
                  <span>Download</span>
                </button>
              </div>
            </div>
            <pre className="p-4 bg-slate-900 text-slate-100 rounded-md font-mono text-xs overflow-x-auto leading-relaxed border border-slate-700">
              {final_report || generateClientMarkdownReport()}
            </pre>
          </section>
        </div>
      )}
    </div>
  )
}
