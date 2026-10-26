import React, { useState, useRef, useEffect } from 'react'
import {
  Layers,
  Code2,
  FileText,
  AlertOctagon,
  MinusCircle,
  ChevronDown,
  ChevronUp,
  ShieldCheck,
  Database,
  CheckCircle2,
  AlertCircle,
  ShieldAlert,
  RotateCw,
  Wrench,
  CheckSquare,
  Info,
} from 'lucide-react'
import JsonViewer from './JsonViewer'

function StatusTag({ status }) {
  const map = {
    completed: 'badge-completed',
    running: 'badge-running',
    failed: 'badge-failed',
    skipped: 'badge-skipped',
    unresolved: 'badge-unresolved',
    pending: 'badge-pending',
  }
  return <span className={`node-status-badge ${map[status] || 'badge-pending'}`}>{status}</span>
}

function SqlTypeBadge({ sqlType, kind }) {
  if (sqlType === 'read_query' || kind === 'database_read_query') {
    return (
      <span className="badge badge-sm badge-info" title="SQL SELECT read query">
        <Database size={11} className="mr-1 inline" />
        SQL Read Query (SELECT)
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
        SQL Schema (DDL)
      </span>
    )
  }
  return null
}

export default function RightInspector({ node }) {
  const [activeTab, setActiveTab] = useState('all') // 'all', 'overview', 'input', 'output', 'execution'
  const [expandedSections, setExpandedSections] = useState({
    overview: true,
    input: true,
    output: true,
    execution: true,
  })

  const scrollRef = useRef(null)

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = 0
    }
  }, [node?.id])

  const toggleSection = (section) => {
    setExpandedSections((prev) => ({ ...prev, [section]: !prev[section] }))
  }

  const handleTabChange = (tab) => {
    setActiveTab(tab)
    if (tab !== 'all') {
      setExpandedSections((prev) => ({ ...prev, [tab]: true }))
    }
  }

  if (!node) {
    return (
      <aside className="panel-right">
        <div className="panel-header">
          <h2 className="panel-title">Node Inspector</h2>
        </div>
        <div className="inspector-empty">
          <Layers size={36} className="text-muted" />
          <p>Select a node from the timeline to inspect its execution details</p>
          <span className="text-muted text-sm">
            View inputs received, outputs produced, findings generated, and execution metadata.
          </span>
        </div>
      </aside>
    )
  }

  const {
    id,
    name,
    order,
    purpose,
    status,
    duration,
    startTime,
    endTime,
    input,
    output,
    error,
    skipReason,
    executionDetails,
    investigator,
  } = node

  return (
    <aside className="panel-right">
      <div className="panel-header">
        <div className="inspector-title-group">
          <span className="order-tag">#{order}</span>
          <h2 className="panel-title" title={name}>
            {name}
          </h2>
        </div>
        <StatusTag status={status} />
      </div>

      {/* Section filter tabs */}
      <div className="inspector-tabs-bar">
        <button
          type="button"
          className={`inspector-tab ${activeTab === 'all' ? 'active' : ''}`}
          onClick={() => handleTabChange('all')}
        >
          All Sections
        </button>
        <button
          type="button"
          className={`inspector-tab ${activeTab === 'overview' ? 'active' : ''}`}
          onClick={() => handleTabChange('overview')}
        >
          1. Overview
        </button>
        <button
          type="button"
          className={`inspector-tab ${activeTab === 'input' ? 'active' : ''}`}
          onClick={() => handleTabChange('input')}
        >
          2. Input
        </button>
        <button
          type="button"
          className={`inspector-tab ${activeTab === 'output' ? 'active' : ''}`}
          onClick={() => handleTabChange('output')}
        >
          3. Output & Findings
        </button>
        <button
          type="button"
          className={`inspector-tab ${activeTab === 'execution' ? 'active' : ''}`}
          onClick={() => handleTabChange('execution')}
        >
          4. Execution
        </button>
      </div>

      <div ref={scrollRef} className="panel-body inspector-scrollable">
        {/* SECTION 1: OVERVIEW */}
        {(activeTab === 'all' || activeTab === 'overview') && (
          <div className="inspector-section">
            <div
              className="inspector-section-header"
              onClick={() => toggleSection('overview')}
            >
              <div className="section-title">
                <Info size={14} />
                <span>1. Overview</span>
              </div>
              {expandedSections.overview ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </div>

            {expandedSections.overview && (
              <div className="inspector-section-content">
                <div className="kv-grid">
                  <div className="kv-item">
                    <span className="kv-label">Node Name:</span>
                    <span className="kv-value font-medium">{name}</span>
                  </div>
                  <div className="kv-item">
                    <span className="kv-label">Execution Order:</span>
                    <span className="kv-value">#{order}</span>
                  </div>
                  <div className="kv-item">
                    <span className="kv-label">Status:</span>
                    <span className="kv-value">
                      <StatusTag status={status} />
                    </span>
                  </div>
                  <div className="kv-item">
                    <span className="kv-label">Duration:</span>
                    <span className="kv-value">
                      {typeof duration === 'number' ? (
                        <span className="duration-highlight">{duration} seconds</span>
                      ) : (
                        <span className="text-muted">Not recorded by backend</span>
                      )}
                    </span>
                  </div>
                  {startTime && (
                    <div className="kv-item">
                      <span className="kv-label">Start Time:</span>
                      <span className="kv-value font-mono text-xs">
                        {new Date(startTime * 1000).toLocaleTimeString()}
                      </span>
                    </div>
                  )}
                  {endTime && (
                    <div className="kv-item">
                      <span className="kv-label">End Time:</span>
                      <span className="kv-value font-mono text-xs">
                        {new Date(endTime * 1000).toLocaleTimeString()}
                      </span>
                    </div>
                  )}
                </div>

                <div className="kv-block mt-2">
                  <span className="kv-label">Purpose:</span>
                  <div className="kv-block-content">{purpose}</div>
                </div>

                {/* Plan Validation Special Callout */}
                {id === 'plan_validation' && output && (
                  <div className={`mt-3 alert-card ${output.is_valid ? 'alert-success' : 'alert-danger'}`}>
                    <div className="alert-header">
                      <ShieldCheck size={16} />
                      <strong>Plan Validation Gate: {output.is_valid ? 'VALIDATED & APPROVED' : 'DISPATCH BLOCKED'}</strong>
                    </div>
                    <p className="alert-body text-xs">
                      {output.is_valid
                        ? `All ${output.validated_task_count ?? 0} tasks satisfied schema constraints, specialist existence, and relevance to gathered context.`
                        : 'Plan validation failed pre-dispatch inspection. Zero specialists were called to prevent corrupted execution.'}
                    </p>
                    {output.errors?.length > 0 && (
                      <div className="mt-2 text-xs">
                        <strong className="text-danger">Errors:</strong>
                        <ul className="pl-4 list-disc">
                          {output.errors.map((err, i) => (
                            <li key={i} className="text-danger">{err}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {output.warnings?.length > 0 && (
                      <div className="mt-2 text-xs">
                        <strong className="text-warning">Warnings:</strong>
                        <ul className="pl-4 list-disc">
                          {output.warnings.map((warn, i) => (
                            <li key={i} className="text-warning">{warn}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                )}

                {skipReason && (
                  <div className="skip-alert">
                    <MinusCircle size={15} />
                    <div>
                      <strong>Skipped:</strong> {skipReason}
                    </div>
                  </div>
                )}

                {error && (
                  <div className="error-alert">
                    <AlertOctagon size={15} />
                    <div>
                      <strong>Failure:</strong> {error}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* SECTION 2: INPUT */}
        {(activeTab === 'all' || activeTab === 'input') && (
          <div className="inspector-section">
            <div
              className="inspector-section-header"
              onClick={() => toggleSection('input')}
            >
              <div className="section-title">
                <Layers size={14} />
                <span>2. Node Input</span>
              </div>
              {expandedSections.input ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </div>

            {expandedSections.input && (
              <div className="inspector-section-content">
                {input?.problem && (
                  <div className="input-card">
                    <span className="card-subtitle">Problem Context Provided:</span>
                    <div className="text-quote">{input.problem}</div>
                  </div>
                )}

                {input?.objective && (
                  <div className="input-card">
                    <span className="card-subtitle">Task Objective:</span>
                    <div className="text-highlight font-medium">{input.objective}</div>
                  </div>
                )}

                {input?.reason && (
                  <div className="input-card">
                    <span className="card-subtitle">Investigation Reason / Rationale:</span>
                    <div className="text-sm text-secondary">{input.reason}</div>
                  </div>
                )}

                {input?.instructions && input.instructions !== input.objective && (
                  <div className="input-card">
                    <span className="card-subtitle">Specialist Instructions:</span>
                    <div className="text-sm text-secondary">{input.instructions}</div>
                  </div>
                )}

                {input?.scope && input.scope.length > 0 && (
                  <div className="input-card">
                    <span className="card-subtitle">Target Scope & Candidate Files:</span>
                    <ul className="file-tag-list">
                      {input.scope.map((file, i) => (
                        <li key={i} className="file-tag font-mono">
                          {file}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {input?.completion_criteria && (
                  <div className="input-card">
                    <span className="card-subtitle">Completion Criteria:</span>
                    <div className="text-xs text-muted font-mono">{input.completion_criteria}</div>
                  </div>
                )}

                <JsonViewer data={input} title="Full Node Input Payload" maxHeight="280px" />
              </div>
            )}
          </div>
        )}

        {/* SECTION 3: OUTPUT */}
        {(activeTab === 'all' || activeTab === 'output') && (
          <div className="inspector-section">
            <div
              className="inspector-section-header"
              onClick={() => toggleSection('output')}
            >
              <div className="section-title">
                <FileText size={14} />
                <span>3. Node Output & Findings</span>
              </div>
              {expandedSections.output ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </div>

            {expandedSections.output && (
              <div className="inspector-section-content">
                {/* Phase 8 Dedicated Reviewer Evaluation Block */}
                {id === 'reviewer' && output && (
                  <div className="reviewer-inspector-block mb-3">
                    <div
                      className={`report-status-banner ${
                        output.is_sufficient
                          ? 'banner-conclusive'
                          : output.decision === 'inconclusive'
                          ? 'banner-inconclusive'
                          : 'banner-warning'
                      }`}
                    >
                      <div className="banner-icon-col">
                        {output.is_sufficient ? (
                          <CheckCircle2 size={22} className="text-success" />
                        ) : output.decision === 'inconclusive' ? (
                          <ShieldAlert size={22} className="text-warning" />
                        ) : (
                          <AlertCircle size={22} className="text-warning" />
                        )}
                      </div>
                      <div className="banner-content-col">
                        <div className="banner-title-row">
                          <h4 className="banner-title font-bold">
                            Reviewer Evaluation: {output.decision ? output.decision.toUpperCase() : 'IN PROGRESS'}
                          </h4>
                          <span
                            className={`badge ${
                              output.is_sufficient ? 'badge-success' : 'badge-warning'
                            }`}
                          >
                            Sufficiency: {output.is_sufficient ? 'SUFFICIENT' : 'INSUFFICIENT'}
                          </span>
                          <span className={`badge ${output.causal_chain_verified ? 'badge-success' : 'badge-warning'}`}>
                            Causal Chain: {output.causal_chain_verified ? 'VERIFIED' : 'UNVERIFIED'}
                          </span>
                          <span className="badge badge-outline">
                            Iteration {output.iteration || 1} of {output.max_iterations || 3}
                          </span>
                        </div>
                        <p className="banner-desc text-xs mt-1">
                          {output.reasoning ||
                            output.feedback ||
                            'Reviewer evaluated collected hypotheses and evidence in shared state.'}
                        </p>
                      </div>
                    </div>

                    {/* What has been established by evidence */}
                    {output.established_by_evidence && output.established_by_evidence.length > 0 && (
                      <div className="input-card mb-3">
                        <span className="card-subtitle font-bold text-success">
                          Established by Evidence ({output.established_by_evidence.length})
                        </span>
                        <ul className="list-disc pl-4 mt-1 text-xs text-secondary">
                          {output.established_by_evidence.map((item, idx) => (
                            <li key={idx}>{item}</li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {/* Evaluated Hypotheses Details */}
                    {output.hypothesis_evaluations && output.hypothesis_evaluations.length > 0 && (
                      <div className="input-card mb-3">
                        <span className="card-subtitle font-bold text-secondary">
                          Hypothesis Sufficiency Evaluations ({output.hypothesis_evaluations.length})
                        </span>
                        <div className="mt-2 flex flex-col gap-2">
                          {output.hypothesis_evaluations.map((he, hIdx) => {
                            const statusColor =
                              he.status === 'supported'
                                ? 'badge-success'
                                : he.status === 'contradicted'
                                ? 'badge-failed'
                                : 'badge-warning'
                            const isApproved = he.is_approved || he.hypothesis_id === output.approved_hypothesis_id
                            return (
                              <div key={hIdx} className={`p-2 border rounded bg-surface text-xs ${isApproved ? 'border-accent' : ''}`}>
                                <div className="flex items-center justify-between font-mono font-bold flex-wrap gap-1">
                                  <div className="flex items-center gap-1.5 flex-wrap">
                                    <span className="text-accent">{he.hypothesis_id || `HYP-${hIdx + 1}`}</span>
                                    {isApproved && (
                                      <span className="badge badge-sm badge-accent">
                                        ✓ Reviewer Approved Root Cause
                                      </span>
                                    )}
                                  </div>
                                  <div className="flex items-center gap-1">
                                    {he.is_relevant_to_problem === true && (
                                      <span className="badge badge-sm badge-success" title="Hypothesis directly addresses the reported problem and symptoms">
                                        Addresses Original Problem
                                      </span>
                                    )}
                                    {he.is_relevant_to_problem === false && (
                                      <span className="badge badge-sm badge-failed" title="Hypothesis targets an unrelated subsystem and does not address the reported problem">
                                        Irrelevant to Problem
                                      </span>
                                    )}
                                    <span className={`badge badge-sm ${statusColor}`}>
                                      {he.status ? he.status.toUpperCase() : 'EVALUATED'}
                                    </span>
                                  </div>
                                </div>
                                {he.relevance_explanation && (
                                  <div className="mt-1 text-xs text-muted">
                                    <strong>Relevance: </strong>
                                    <span>{he.relevance_explanation}</span>
                                  </div>
                                )}
                                {he.evaluation && (
                                  <div className="mt-1 text-secondary font-medium">
                                    <strong>Evaluation: </strong>
                                    <span>{he.evaluation}</span>
                                  </div>
                                )}
                                {he.why_supported_or_not && he.why_supported_or_not !== he.evaluation && (
                                  <div className="mt-1 text-xs text-secondary">
                                    <strong>Causal Evidence Support: </strong>
                                    <span>{he.why_supported_or_not}</span>
                                  </div>
                                )}
                                {he.supporting_evidence_ids && he.supporting_evidence_ids.length > 0 && (
                                  <div className="mt-1 flex items-center gap-1 flex-wrap">
                                    <span className="text-muted">Supporting Evidence:</span>
                                    {he.supporting_evidence_ids.map((eid, eIdx) => (
                                      <span key={eIdx} className="badge badge-sm badge-success font-mono">
                                        {eid}
                                      </span>
                                    ))}
                                  </div>
                                )}
                                {he.contradicting_evidence_ids && he.contradicting_evidence_ids.length > 0 && (
                                  <div className="mt-1 flex items-center gap-1 flex-wrap">
                                    <span className="text-danger font-semibold">Contradicting Evidence:</span>
                                    {he.contradicting_evidence_ids.map((ceid, cIdx) => (
                                      <span key={cIdx} className="badge badge-sm badge-failed font-mono">
                                        {ceid}
                                      </span>
                                    ))}
                                  </div>
                                )}
                              </div>
                            )
                          })}
                        </div>
                      </div>
                    )}

                    {/* Evidence Gaps and Unanswered Questions */}
                    {((output.missing_evidence && output.missing_evidence.length > 0) ||
                      (output.unanswered_questions && output.unanswered_questions.length > 0) ||
                      (output.unanswered_objectives && output.unanswered_objectives.length > 0)) && (
                      <div className="input-card mb-3">
                        <span className="card-subtitle font-bold text-danger">
                          Unresolved Evidence Gaps & Questions
                        </span>
                        {output.missing_evidence && output.missing_evidence.length > 0 && (
                          <div className="mt-1 text-xs">
                            <strong className="text-danger">Missing Evidence:</strong>
                            <ul className="list-disc pl-4 mt-0.5 text-secondary">
                              {output.missing_evidence.map((me, i) => (
                                <li key={i}>{me}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {output.unanswered_questions && output.unanswered_questions.length > 0 && (
                          <div className="mt-2 text-xs">
                            <strong className="text-secondary">Unanswered Questions:</strong>
                            <ul className="list-disc pl-4 mt-0.5 text-muted">
                              {output.unanswered_questions.map((uq, i) => (
                                <li key={i}>{uq}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {output.unanswered_objectives && output.unanswered_objectives.length > 0 && (
                          <div className="mt-2 text-xs">
                            <strong className="text-warning">Unanswered Objectives:</strong>
                            <ul className="list-disc pl-4 mt-0.5 text-muted">
                              {output.unanswered_objectives.map((uo, i) => (
                                <li key={i}>{uo}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                      </div>
                    )}

                    {/* Actionable Feedback for Re-planner */}
                    {output.actionable_feedback && (
                      <div className="input-card mb-3">
                        <span className="card-subtitle font-bold text-accent">
                          Actionable Feedback for Re-planner
                        </span>
                        <div className="mt-1 text-xs text-secondary">
                          {typeof output.actionable_feedback === 'string' ? (
                            <p>{output.actionable_feedback}</p>
                          ) : (
                            <div className="flex flex-col gap-1">
                              {output.actionable_feedback.missing_evidence && (
                                <div>
                                  <strong>Missing Evidence:</strong> {output.actionable_feedback.missing_evidence.join(', ')}
                                </div>
                              )}
                              {output.actionable_feedback.required_next_steps && (
                                <div>
                                  <strong>Required Next Steps:</strong>
                                  <ul className="list-disc pl-4 mt-0.5">
                                    {output.actionable_feedback.required_next_steps.map((s, i) => (
                                      <li key={i}>{s}</li>
                                    ))}
                                  </ul>
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {/* Phase 8 Dedicated Re-planner Block */}
                {id === 'replanner' && output && (
                  <div className="replanner-inspector-block mb-3">
                    <div className="report-status-banner banner-info">
                      <div className="banner-icon-col">
                        <RotateCw size={22} className="text-accent" />
                      </div>
                      <div className="banner-content-col">
                        <div className="banner-title-row">
                          <h4 className="banner-title font-bold">Investigation Re-planning</h4>
                          <span className="badge badge-info font-mono">
                            Iteration {output.investigation_iterations || 2} of {output.max_iterations || 3}
                          </span>
                        </div>
                        <p className="banner-desc text-xs mt-1">
                          Reviewer identified evidence gaps. Re-planner generated targeted follow-up tasks without discarding previous findings.
                        </p>
                      </div>
                    </div>

                    {/* Newly Created Tasks */}
                    {output.replanned_tasks && output.replanned_tasks.length > 0 && (
                      <div className="input-card mb-3">
                        <span className="card-subtitle font-bold text-secondary">
                          Newly Created Follow-up Tasks ({output.replanned_tasks.length})
                        </span>
                        <div className="mt-2 flex flex-col gap-2">
                          {output.replanned_tasks.map((task, tIdx) => (
                            <div key={tIdx} className="p-2 border rounded bg-surface text-xs">
                              <div className="flex items-center justify-between font-mono font-bold">
                                <span className="text-accent">{task.task_id}</span>
                                <span className="badge badge-sm badge-neutral">{task.assigned_agent}</span>
                              </div>
                              <div className="font-semibold text-secondary mt-1">{task.objective}</div>
                              {task.unanswered_question && (
                                <div className="text-secondary text-xs mt-0.5">
                                  <strong className="text-accent">Resolves Question: </strong>
                                  {task.unanswered_question}
                                </div>
                              )}
                              {task.target_hypothesis_id && (
                                <div className="text-muted text-xs mt-0.5">
                                  <strong>Tests Hypothesis: </strong>
                                  <span className="font-mono text-accent">{task.target_hypothesis_id}</span>
                                </div>
                              )}
                              {task.expected_evidence && (
                                <div className="text-secondary text-xs mt-0.5">
                                  <strong>Expected Evidence: </strong>
                                  {task.expected_evidence}
                                </div>
                              )}
                              {task.reason && (
                                <div className="text-muted mt-0.5">
                                  <strong>Why Needed: </strong>
                                  {task.reason}
                                </div>
                              )}
                              {task.scope && task.scope.length > 0 && (
                                <div className="font-mono text-muted text-xs mt-0.5">
                                  Target files: {task.scope.join(', ')}
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Unresolved Tasks Callout if present */}
                    {output.unresolved_tasks && output.unresolved_tasks.length > 0 && (
                      <div className="input-card mb-3">
                        <span className="card-subtitle font-bold text-warning">
                          Unresolved Investigation Tasks ({output.unresolved_tasks.length})
                        </span>
                        <div className="mt-1 flex flex-col gap-1 text-xs text-muted">
                          {output.unresolved_tasks.map((ut, uIdx) => (
                            <div key={uIdx} className="p-1.5 border rounded bg-surface">
                              <span className="font-mono font-bold text-accent">{ut.task_id}</span>: {ut.reason || ut.objective}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Iteration History */}
                    {output.investigation_history && output.investigation_history.length > 0 && (
                      <div className="input-card mb-3">
                        <span className="card-subtitle font-bold text-secondary">
                          Iteration History ({output.investigation_history.length})
                        </span>
                        <div className="mt-1 flex flex-col gap-1 text-xs">
                          {output.investigation_history.map((hist, hIdx) => (
                            <div key={hIdx} className="p-1.5 border rounded bg-surface">
                              <div className="flex items-center justify-between">
                                <strong>Iteration {hist.iteration}</strong>
                                <span className="badge badge-sm badge-outline">
                                  Tasks Added: {hist.tasks_added}
                                </span>
                              </div>
                              {hist.feedback && (
                                <div className="text-muted text-xs mt-0.5">{hist.feedback}</div>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {/* Dedicated Investigation Outcome & Assessment Block */}
                {id === 'investigation_outcome' && output && (
                  <div className="outcome-inspector-block mb-3">
                    <div
                      className={`report-status-banner ${
                        output.is_conclusive ? 'banner-conclusive' : 'banner-inconclusive'
                      }`}
                    >
                      <div className="banner-icon-col">
                        {output.is_conclusive ? (
                          <CheckCircle2 size={22} className="text-success" />
                        ) : (
                          <ShieldAlert size={22} className="text-warning" />
                        )}
                      </div>
                      <div className="banner-content-col">
                        <div className="banner-title-row">
                          <h4 className="banner-title font-bold">
                            {output.is_conclusive
                              ? 'Preliminary Root Cause Result'
                              : 'Inconclusive Investigation Result'}
                          </h4>
                          {output.confidence && (
                            <span className="badge badge-outline">
                              Confidence: {output.confidence.toUpperCase()}
                            </span>
                          )}
                          <span
                            className={`badge badge-sm ${
                              output.is_conclusive ? 'badge-success' : 'badge-neutral'
                            }`}
                          >
                            {output.is_conclusive ? (output.status_label || 'Conclusive') : 'Inconclusive Result'}
                          </span>
                        </div>
                        <p className="banner-desc text-xs mt-1">
                          {output.is_conclusive
                            ? 'Reviewer verified that evidence is sufficient to establish a preliminary root cause grounded in causal defect evidence.'
                            : 'Investigation limit reached without sufficient conclusive evidence to prove a root cause. Evidence gaps remain unresolved.'}
                        </p>
                        {output.stop_reason && (
                          <div className="banner-stop-reason text-xs">
                            <span className="text-muted">Stop Reason: </span>
                            <span className="font-mono">{output.stop_reason}</span>
                          </div>
                        )}
                      </div>
                    </div>

                    {/* Observable Symptoms & Scope */}
                    {(output.symptoms || output.expected_behavior || (output.target_areas && output.target_areas.length > 0)) && (
                      <div className="input-card mb-3">
                        <span className="card-subtitle font-bold text-secondary">Observable Symptoms & Target Scope</span>
                        {output.symptoms && (
                          <div className="text-xs mt-1">
                            <strong>Reported Symptom: </strong>
                            <span>{output.symptoms}</span>
                          </div>
                        )}
                        {output.expected_behavior && (
                          <div className="text-xs mt-1 text-muted">
                            <strong>Expected Behavior: </strong>
                            <span>{output.expected_behavior}</span>
                          </div>
                        )}
                        {output.target_areas && output.target_areas.length > 0 && (
                          <div className="text-xs mt-1">
                            <span className="font-semibold text-muted">Target Areas: </span>
                            <span className="font-mono text-xs text-secondary">{output.target_areas.join(', ')}</span>
                          </div>
                        )}
                      </div>
                    )}

                    {/* Root Cause Assessment & Remaining Uncertainty */}
                    <div className="conclusions-box mb-3">
                      <div className="conclusion-title flex items-center gap-2 mb-1">
                        {output.is_conclusive ? (
                          <>
                            <CheckCircle2 size={16} className="text-success" />
                            <strong className="text-success">Preliminary Root Cause:</strong>
                          </>
                        ) : (
                          <>
                            <AlertCircle size={16} className="text-warning" />
                            <strong className="text-warning">Inconclusive Assessment & Candidate Hypotheses:</strong>
                          </>
                        )}
                      </div>
                      <div className="text-xs">
                        {output.is_conclusive ? (
                          <div>
                            <div className="flex items-center gap-1.5 flex-wrap mb-1">
                              {output.approved_hypothesis_id && (
                                <span className="badge badge-sm badge-accent font-mono font-bold">
                                  Approved: {output.approved_hypothesis_id}
                                </span>
                              )}
                              <span className="badge badge-sm badge-success">
                                Preliminary Root Cause Confirmed
                              </span>
                            </div>
                            <p className="font-bold text-success text-sm">{output.likely_root_cause}</p>
                            {output.review_reasoning && (
                              <div className="mt-1 text-secondary">
                                <strong>Reviewer Reasoning: </strong>
                                <span>{output.review_reasoning}</span>
                              </div>
                            )}

                            {/* Phase 9 Causal Explanation */}
                            {output.causal_explanation && (
                              <div className="causal-chain-card mt-2">
                                <strong>Causal Mechanism: </strong>
                                <span>{output.causal_explanation}</span>
                              </div>
                            )}

                            {/* Reviewer-Approved Causal Supporting Evidence */}
                            {output.supporting_evidence && output.supporting_evidence.length > 0 && (
                              <div className="mt-2.5 border-t pt-2">
                                <span className="font-semibold text-xs text-secondary">
                                  Corroborating Causal Evidence ({output.supporting_evidence.length}):
                                </span>
                                <div className="mt-1.5 flex flex-col gap-1.5">
                                  {output.supporting_evidence.map((ev, evIdx) => (
                                    <div key={ev.evidence_id || evIdx} className="p-2 border rounded bg-surface text-xs">
                                      <div className="flex items-center justify-between font-mono font-bold">
                                        <span className="text-accent">{ev.evidence_id || `EV-${evIdx + 1}`}</span>
                                        <span className="text-muted">{ev.source || ev.path}:{ev.line ?? '?'}</span>
                                      </div>
                                      {(ev.matched_text || ev.snippet) && (
                                        <pre className="code-snippet font-mono text-xs mt-1">{ev.matched_text || ev.snippet}</pre>
                                      )}
                                      {ev.relationship_to_hypothesis && (
                                        <div className="text-muted text-xs mt-0.5">
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
                              </div>
                            )}
                          </div>
                        ) : (
                          <div>
                            <p className="text-secondary mb-1">
                              Status: <strong>Inconclusive Result</strong> (Evidence remains insufficient or investigation limits were reached without complete verification).
                            </p>
                            {output.likely_root_cause && (
                              <div className="candidate-box mt-1">
                                <span className="text-xs text-muted">
                                  {output.likely_root_cause.toLowerCase().startsWith('inconclusive') ? 'Investigation Assessment:' : 'Possible Cause (Hypothesis):'}
                                </span>
                                <div className="font-mono text-xs mt-0.5">{output.likely_root_cause}</div>
                              </div>
                            )}
                            {output.causal_explanation && (
                              <div className="causal-chain-card mt-2">
                                <strong>Assessment Explanation: </strong>
                                <span>{output.causal_explanation}</span>
                              </div>
                            )}
                          </div>
                        )}

                        {output.missing_evidence && output.missing_evidence.length > 0 && (
                          <div className="mt-2 text-xs text-danger border-t pt-1">
                            <strong>Remaining Evidence Gaps: </strong>
                            <ul className="list-disc pl-4 mt-0.5">
                              {output.missing_evidence.map((gap, gIdx) => (
                                <li key={gIdx}>{gap}</li>
                              ))}
                            </ul>
                          </div>
                        )}

                        {output.unanswered_questions && output.unanswered_questions.length > 0 && (
                          <div className="mt-2 text-xs text-secondary border-t pt-1">
                            <strong>Unanswered Questions: </strong>
                            <ul className="list-disc pl-4 mt-0.5 text-muted">
                              {output.unanswered_questions.map((uq, uIdx) => (
                                <li key={uIdx}>{uq}</li>
                              ))}
                            </ul>
                          </div>
                        )}

                        {output.remaining_uncertainty && (
                          <div className="mt-2 text-xs text-muted border-t pt-1">
                            <strong>Remaining Uncertainty: </strong>
                            {output.remaining_uncertainty}
                          </div>
                        )}

                        {/* Phase 9 Suggested Fix Card */}
                        {(output.suggested_fix || output.suggested_fix_details) && (
                          <div className="mt-3 border-t pt-2.5">
                            <div className="flex items-center gap-1.5 font-bold text-xs text-secondary mb-2">
                              <Wrench size={14} className="text-accent" />
                              <span>Phase 9 — Suggested Fix Recommendation</span>
                            </div>
                            <div className="fix-card text-xs">
                              {output.suggested_fix_details?.target_location && (
                                <div className="fix-header-row">
                                  <span className="text-muted">Target Location:</span>
                                  <span className="fix-target-pill">{output.suggested_fix_details.target_location}</span>
                                </div>
                              )}
                              {output.suggested_fix_details?.recommended_change && (
                                <div className="mb-2">
                                  <strong>Recommended Correction: </strong>
                                  <span className="text-secondary">{output.suggested_fix_details.recommended_change}</span>
                                </div>
                              )}
                              {output.suggested_fix_details?.rationale && (
                                <div className="mb-2 text-muted">
                                  <strong>Correction Rationale: </strong>
                                  <span>{output.suggested_fix_details.rationale}</span>
                                </div>
                              )}
                              {(output.suggested_fix_details?.code_snippet || (typeof output.suggested_fix === 'string' && output.suggested_fix.includes('\n'))) && (
                                <div className="fix-snippet-wrapper">
                                  <div className="fix-snippet-title">Illustrative Code Fix</div>
                                  <pre className="fix-snippet-code">
                                    {output.suggested_fix_details?.code_snippet || output.suggested_fix}
                                  </pre>
                                </div>
                              )}
                              {output.suggested_fix_details?.contract_invariants && (
                                <div className="fix-subblock fix-subblock-invariants mb-2">
                                  <strong>Preserved Invariant: </strong>
                                  <span>{output.suggested_fix_details.contract_invariants}</span>
                                </div>
                              )}
                              {output.suggested_fix_details?.potential_regressions?.length > 0 && (
                                <div className="fix-subblock fix-subblock-regressions mb-2">
                                  <strong>Potential Regressions to Check:</strong>
                                  <ul className="list-disc pl-4 mt-0.5">
                                    {output.suggested_fix_details.potential_regressions.map((reg, rIdx) => (
                                      <li key={rIdx}>{reg}</li>
                                    ))}
                                  </ul>
                                </div>
                              )}
                              <div className="fix-scope-alert">
                                <Info size={13} />
                                <span>Scope Restriction: Fix recommendation only. Target application source code was not modified.</span>
                              </div>
                            </div>
                          </div>
                        )}

                        {/* Phase 9 Verification Steps Card */}
                        {(output.verification_status || (output.verification_steps && output.verification_steps.length > 0)) && (
                          <div className="mt-3 border-t pt-2.5">
                            <div className="flex items-center justify-between mb-2">
                              <div className="flex items-center gap-1.5 font-bold text-xs text-secondary">
                                <CheckSquare size={14} className="text-accent" />
                                <span>Phase 9 — Verification Engine</span>
                              </div>
                              <span
                                className={`badge badge-sm ${
                                  output.verification_status === 'verified'
                                    ? 'badge-success'
                                    : output.verification_status === 'verification blocked'
                                    ? 'badge-failed'
                                    : output.verification_status === 'partially verified'
                                    ? 'badge-warning'
                                    : 'badge-outline'
                                }`}
                              >
                                {output.verification_status || 'not verified'}
                              </span>
                            </div>

                            {/* Blockers alert if blocked */}
                            {output.verification_summary?.blockers?.length > 0 && (
                              <div className="verification-blocker-alert">
                                <strong>Execution Blockers ({output.verification_summary.blockers.length}):</strong>
                                <ul>
                                  {output.verification_summary.blockers.map((b, bIdx) => (
                                    <li key={bIdx}>{b}</li>
                                  ))}
                                </ul>
                              </div>
                            )}

                            {/* Proposed verification checks */}
                            {output.verification_steps?.length > 0 && (
                              <div className="checks-grid mt-1">
                                {output.verification_steps.map((step, sIdx) => {
                                  const isObj = typeof step === 'object' && step !== null
                                  return (
                                    <div key={sIdx} className="check-card">
                                      <div className="check-card-header font-bold text-accent">
                                        <span>Check #{sIdx + 1} {isObj ? (step.title || step.name) : ''}</span>
                                      </div>
                                      <p className="text-secondary mt-0.5">
                                        {isObj ? (step.instructions || step.description || step.steps) : step}
                                      </p>
                                      {isObj && step.expected_outcome && (
                                        <div className="text-xs text-success mt-1">
                                          <strong>Expected Outcome: </strong>{step.expected_outcome}
                                        </div>
                                      )}
                                      {isObj && step.edge_cases?.length > 0 && (
                                        <div className="text-xs text-muted mt-0.5">
                                          <strong>Edge cases: </strong>{Array.isArray(step.edge_cases) ? step.edge_cases.join(', ') : step.edge_cases}
                                        </div>
                                      )}
                                    </div>
                                  )
                                })}
                              </div>
                            )}
                          </div>
                        )}

                        {output.no_fix_disclaimer && (
                          <div className="mt-2 text-xs text-muted font-italic border-t pt-1">
                            <em>Note: {output.no_fix_disclaimer}</em>
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                )}

                {/* Hypotheses sample if available */}
                {output?.hypotheses_sample && output.hypotheses_sample.length > 0 && (
                  <div className="hypotheses-subgroup">
                    <span className="card-subtitle font-bold text-secondary">
                      {id === 'investigation_outcome' ? '3. Identified Candidate Hypotheses' : 'Candidate Hypotheses Formulated'} ({output.hypotheses_sample.length})
                    </span>
                    <div className="hypotheses-list mt-1">
                      {output.hypotheses_sample.map((h, i) => {
                        const title = h.hypothesis_title || h.title || h.statement || `Possible Cause #${i + 1}`
                        const author = h.produced_by || h.agent_name || investigator || 'Specialist'
                        const evalItem = (output.hypothesis_evaluations || []).find((e) => e.hypothesis_id === h.hypothesis_id)
                        const isApproved = evalItem?.is_approved || h.hypothesis_id === output.approved_hypothesis_id
                        const statusBadgeClass =
                          h.status === 'supported'
                            ? 'badge-success'
                            : h.status === 'contradicted'
                            ? 'badge-failed'
                            : 'badge-warning'
                        const statusBadgeLabel =
                          h.status === 'supported'
                            ? 'Supported'
                            : h.status === 'contradicted'
                            ? 'Contradicted'
                            : 'Needs Validation'

                        return (
                          <div key={h.hypothesis_id || i} className={`hypothesis-card ${isApproved ? 'border-accent' : ''}`}>
                            <div className="hypothesis-header">
                              <div className="flex items-center gap-1 flex-wrap">
                                {h.hypothesis_id && (
                                  <span className="font-mono text-xs font-bold text-accent">
                                    {h.hypothesis_id}
                                  </span>
                                )}
                                {isApproved && (
                                  <span className="badge badge-sm badge-accent">
                                    ✓ Reviewer Approved
                                  </span>
                                )}
                                {evalItem?.is_relevant_to_problem === true && (
                                  <span className="badge badge-sm badge-success" title="Hypothesis directly addresses the original problem">
                                    Addresses Original Problem
                                  </span>
                                )}
                                {evalItem?.is_relevant_to_problem === false && (
                                  <span className="badge badge-sm badge-failed" title="Hypothesis targets an unrelated subsystem and does not address the reported problem">
                                    Irrelevant to Problem
                                  </span>
                                )}
                                <span className={`badge badge-sm ${statusBadgeClass}`}>
                                  {statusBadgeLabel}
                                </span>
                              </div>
                              <div className="flex items-center gap-2 text-xs">
                                <span className="text-muted">
                                  Proposed by: <strong className="text-secondary">{author}</strong>
                                </span>
                                {h.confidence && (
                                  <span className="badge badge-sm badge-outline">
                                    {h.confidence}
                                  </span>
                                )}
                              </div>
                            </div>
                            <div className="hypothesis-statement text-sm font-bold mt-1">
                              {title}
                            </div>
                            {evalItem?.relevance_explanation && (
                              <div className="mt-1 text-xs text-muted">
                                <strong>Reviewer Relevance Verdict: </strong>
                                <span>{evalItem.relevance_explanation}</span>
                              </div>
                            )}
                            {evalItem?.why_supported_or_not && (
                              <div className="mt-1 text-xs text-secondary">
                                <strong>Reviewer Causal Assessment: </strong>
                                <span>{evalItem.why_supported_or_not}</span>
                              </div>
                            )}
                            {(h.possible_cause || (h.statement && h.statement !== title)) && (
                              <div className="hypothesis-cause-box mt-1">
                                <span className="text-xs font-bold text-amber-900">Possible Cause: </span>
                                <span className="text-xs text-amber-950 font-medium">
                                  {h.possible_cause || h.statement}
                                </span>
                              </div>
                            )}
                            {h.evidence && (
                              <div className="hypothesis-evidence-box mt-1">
                                <span className="text-xs font-semibold text-secondary">Code Reference: </span>
                                <span className="font-mono text-xs text-accent">{h.evidence}</span>
                              </div>
                            )}
                            {h.reasoning && (
                              <div className="text-xs text-secondary mt-1">
                                <strong>Reasoning: </strong>
                                <span>{h.reasoning}</span>
                              </div>
                            )}

                            {/* Rich Supporting Evidence */}
                            {h.supporting_evidence && h.supporting_evidence.length > 0 ? (
                              <div className="mt-2">
                                <span className="text-xs font-semibold text-secondary">
                                  Supporting Evidence ({h.supporting_evidence.length}):
                                </span>
                                <div className="evidence-subitems mt-1">
                                  {h.supporting_evidence.map((ev, evIdx) => (
                                    <div key={ev.evidence_id || evIdx} className="evidence-subitem p-2 bg-surface border rounded text-xs mt-1">
                                      <div className="flex items-center gap-2">
                                        <span className="font-mono font-bold text-accent">
                                          {ev.evidence_id || `E${evIdx + 1}`}
                                        </span>
                                        <span className="font-mono text-muted">
                                          {ev.source || ev.path || 'file'}:{ev.line ?? '?'}
                                        </span>
                                        <SqlTypeBadge sqlType={ev.sql_type} kind={ev.kind} />
                                        {ev.verification_status && (
                                          <span className={`badge badge-sm ${ev.verification_status === 'source_validated' ? 'badge-success' : 'badge-outline'}`}>
                                            {ev.verification_status}
                                          </span>
                                        )}
                                      </div>
                                      {(ev.snippet || ev.matched_text) && (
                                        <pre className="code-snippet font-mono text-xs mt-1">
                                          {ev.snippet || ev.matched_text}
                                        </pre>
                                      )}
                                      {ev.observed_fact && (
                                        <div className="text-muted text-xs mt-1 italic">
                                          {ev.observed_fact}
                                        </div>
                                      )}
                                    </div>
                                  ))}
                                </div>
                              </div>
                            ) : h.supporting_evidence_ids && h.supporting_evidence_ids.length > 0 ? (
                              <div className="mt-1 text-xs text-muted">
                                <span>Supporting Evidence: </span>
                                {h.supporting_evidence_ids.join(', ')}
                              </div>
                            ) : null}

                            {/* Rich Contradicting Evidence */}
                            {h.contradicting_evidence && h.contradicting_evidence.length > 0 ? (
                              <div className="hypothesis-contradict-box mt-2">
                                <span className="text-xs font-bold text-red-800">
                                  Contradicting Evidence ({h.contradicting_evidence.length}):
                                </span>
                                <div className="mt-1">
                                  {h.contradicting_evidence.map((cev, cevIdx) => (
                                    <div key={cev.evidence_id || cevIdx} className="text-xs text-red-950 mt-1">
                                      <div className="flex items-center gap-2">
                                        <span className="font-mono font-bold">
                                          {cev.evidence_id || `CE${cevIdx + 1}`}
                                        </span>
                                        <span className="font-mono">
                                          {cev.source || cev.path}:{cev.line ?? '?'}
                                        </span>
                                      </div>
                                      {(cev.snippet || cev.matched_text) && (
                                        <pre className="code-snippet font-mono text-xs mt-1">
                                          {cev.snippet || cev.matched_text}
                                        </pre>
                                      )}
                                    </div>
                                  ))}
                                </div>
                              </div>
                            ) : h.contradicting_evidence_ids && h.contradicting_evidence_ids.length > 0 ? (
                              <div className="mt-1 text-xs text-red-700">
                                <span>Contradicting Evidence: </span>
                                {h.contradicting_evidence_ids.join(', ')}
                              </div>
                            ) : null}

                            {h.unanswered_questions && h.unanswered_questions.length > 0 && (
                              <div className="mt-2 text-xs text-muted">
                                <strong>Information Needed to Validate: </strong>
                                <ul className="list-disc pl-4 mt-0.5">
                                  {h.unanswered_questions.map((q, qIdx) => (
                                    <li key={qIdx}>{q}</li>
                                  ))}
                                </ul>
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )}

                {/* Findings Preview if available */}
                {output?.findings_sample && output.findings_sample.length > 0 && (
                  <div className="findings-subgroup mt-3">
                    <span className="card-subtitle font-bold text-secondary">
                      {id === 'investigation_outcome' ? '4. Confirmed Specialist Findings' : 'Findings Generated'} ({output.findings_sample.length})
                    </span>
                    <div className="findings-list">
                      {output.findings_sample.map((f, i) => (
                        <div key={i} className="finding-card">
                          <div className="finding-header">
                            <span className="finding-cat badge-sm badge-neutral">
                              {f.category || 'Confirmed Finding'}
                            </span>
                            {f.confidence && (
                              <span className="badge badge-sm badge-outline">
                                {f.confidence} confidence
                              </span>
                            )}
                          </div>
                          <p className="finding-statement font-medium">{f.statement || f.finding || f.description}</p>
                          {f.file_path && (
                            <div className="mt-1 text-xs font-mono text-accent">
                              Location: {f.file_path}:{f.line_number ?? '?'}
                            </div>
                          )}
                          {(f.snippet || f.matched_text || f.code) && (
                            <pre className="code-snippet font-mono text-xs mt-1">
                              {f.snippet || f.matched_text || f.code}
                            </pre>
                          )}
                          {f.rationale && (
                            <p className="finding-rationale text-muted text-xs mt-1">
                              {f.rationale}
                            </p>
                          )}
                          {f.evidence_ids && f.evidence_ids.length > 0 && (
                            <div className="finding-evidence-tags mt-1">
                              <span className="text-muted text-xs">Supporting Evidence:</span>
                              {f.evidence_ids.map((eid, idx) => (
                                <span key={idx} className="evidence-pill">
                                  {eid}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Evidence items sample if available */}
                {output?.evidence_sample && output.evidence_sample.length > 0 && (
                  <div className="evidence-subgroup mt-3">
                    <span className="card-subtitle font-bold text-secondary">
                      {id === 'investigation_outcome' ? '5. Discovered Evidence Pool Sample' : 'Evidence Collected'} ({output.evidence_sample.length})
                    </span>
                    <div className="evidence-list">
                      {output.evidence_sample.map((e, i) => (
                        <div key={i} className="evidence-card">
                          <div className="evidence-header">
                            <span className="evidence-id-tag font-bold">
                              {e.evidence_id || `E${i + 1}`}
                            </span>
                            <span className="evidence-location font-mono text-xs">
                              {e.source || e.path || 'unknown'}:{e.line ?? '?'}
                            </span>
                            <SqlTypeBadge sqlType={e.sql_type} kind={e.kind} />
                            {e.verification_status && (
                              <span
                                className={`badge badge-sm ${
                                  e.verification_status === 'source_validated'
                                    ? 'badge-success'
                                    : 'badge-outline'
                                }`}
                              >
                                {e.verification_status}
                              </span>
                            )}
                          </div>
                          {(e.matched_text || e.snippet) && (
                            <pre className="code-snippet font-mono text-xs mt-1">
                              {e.matched_text || e.snippet}
                            </pre>
                          )}
                          {e.observed_fact && (
                            <p className="text-muted text-xs mt-1">
                              {e.observed_fact}
                            </p>
                          )}
                          {(e.explanation || e.rationale) && (
                            <p className="text-secondary text-xs mt-1">
                              <strong>Causal Link: </strong>{e.explanation || e.rationale}
                            </p>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* First-Class Investigation Plan Artifact */}
                {output?.plan_artifact && (
                  <div className="plan-artifact-subgroup mt-3">
                    <div className="flex items-center justify-between">
                      <span className="card-subtitle">
                        Investigation Plan Artifact
                      </span>
                      <div className="flex items-center gap-1">
                        <span className="badge badge-sm badge-info font-mono">
                          {output.plan_artifact.plan_id || output.plan_id}
                        </span>
                        <span className="badge badge-sm badge-success">
                          {output.plan_artifact.lifecycle_status || output.lifecycle_status || 'validated'}
                        </span>
                      </div>
                    </div>

                    {/* Problem Understanding */}
                    {output.plan_artifact.problem_understanding && (
                      <div className="plan-section-card mt-2 p-2 bg-base-200 rounded border border-base-300">
                        <div className="font-bold text-xs text-secondary mb-1">A. Problem Understanding</div>
                        {output.plan_artifact.problem_understanding.problem_summary && (
                          <div className="text-xs mb-1">
                            <strong>Summary: </strong>{output.plan_artifact.problem_understanding.problem_summary}
                          </div>
                        )}
                        {output.plan_artifact.problem_understanding.observed_symptoms?.length > 0 && (
                          <div className="text-xs mb-1">
                            <span className="font-semibold text-muted">Observed Symptoms: </span>
                            <div className="flex flex-wrap gap-1 mt-0.5">
                              {output.plan_artifact.problem_understanding.observed_symptoms.map((s, idx) => (
                                <span key={idx} className="badge badge-xs badge-neutral">{s}</span>
                              ))}
                            </div>
                          </div>
                        )}
                        {output.plan_artifact.problem_understanding.expected_behavior && (
                          <div className="text-xs mb-1 text-muted">
                            <strong>Expected Behavior: </strong>{output.plan_artifact.problem_understanding.expected_behavior}
                          </div>
                        )}
                        {output.plan_artifact.problem_understanding.uncertainties_and_unknowns?.length > 0 && (
                          <div className="text-xs text-amber-800">
                            <strong>Key Uncertainties: </strong>{output.plan_artifact.problem_understanding.uncertainties_and_unknowns.join('; ')}
                          </div>
                        )}
                      </div>
                    )}

                    {/* Objectives */}
                    {output.plan_artifact.objectives?.length > 0 && (
                      <div className="plan-section-card mt-2 p-2 bg-base-200 rounded border border-base-300">
                        <div className="font-bold text-xs text-secondary mb-1">
                          B. Strategic Investigation Objectives ({output.plan_artifact.objectives.length})
                        </div>
                        <div className="flex flex-col gap-2 mt-1">
                          {output.plan_artifact.objectives.map((obj, idx) => (
                            <div key={obj.objective_id || idx} className="p-2 bg-base-100 rounded border border-base-300 text-xs">
                              <div className="flex items-center gap-1 font-bold text-accent">
                                <span>{obj.objective_id}</span>
                                <span className="text-base-content">{obj.title}</span>
                              </div>
                              {obj.relevance_to_symptoms && (
                                <div className="text-muted mt-1">
                                  <strong>Relevance: </strong>{obj.relevance_to_symptoms}
                                </div>
                              )}
                              {obj.possible_explanations?.length > 0 && (
                                <div className="mt-1">
                                  <span className="font-semibold text-amber-900">Failure Directions to Examine:</span>
                                  <ul className="list-disc list-inside pl-1 text-muted">
                                    {obj.possible_explanations.map((exp, eIdx) => (
                                      <li key={eIdx}>{exp}</li>
                                    ))}
                                  </ul>
                                </div>
                              )}
                              {obj.discriminating_evidence && (
                                <div className="mt-1 text-muted">
                                  <strong>Expected Information: </strong>{obj.discriminating_evidence}
                                </div>
                              )}
                              {obj.completion_outcome && (
                                <div className="mt-1 text-emerald-700 font-medium">
                                  <strong>Completion Outcome: </strong>{obj.completion_outcome}
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Specialist Strategy */}
                    {output.plan_artifact.specialist_strategy && (
                      <div className="plan-section-card mt-2 p-2 bg-base-200 rounded border border-base-300 text-xs">
                        <div className="font-bold text-secondary mb-1">C. Specialist Strategy & Approach</div>
                        <div className="flex flex-wrap gap-1 mb-1">
                          {(output.plan_artifact.specialist_strategy.selected_specialists || []).map((sp, idx) => (
                            <span key={idx} className="badge badge-sm badge-outline font-mono">{sp}</span>
                          ))}
                        </div>
                        {output.plan_artifact.approach?.execution_paths_to_probe?.length > 0 && (
                          <div className="text-muted mb-1">
                            <strong>Paths to Probe: </strong>{output.plan_artifact.approach.execution_paths_to_probe.join(', ')}
                          </div>
                        )}
                        {output.plan_artifact.approach?.contingency_plans && (
                          <div className="text-muted text-xs">
                            <strong>Contingency: </strong>{output.plan_artifact.approach.contingency_plans}
                          </div>
                        )}
                      </div>
                    )}

                    {/* Completion Criteria */}
                    {output.plan_artifact.completion_criteria?.length > 0 && (
                      <div className="plan-section-card mt-2 p-2 bg-base-200 rounded border border-base-300 text-xs">
                        <div className="font-bold text-secondary mb-1">D. Investigation Completion Criteria</div>
                        <ul className="list-disc list-inside text-muted pl-1">
                          {output.plan_artifact.completion_criteria.map((crit, cIdx) => (
                            <li key={cIdx}>{crit}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                )}

                {/* Structured Output JSON */}
                <div className="mt-3">
                  <JsonViewer data={output} title="Full Node Output Payload" maxHeight="320px" />
                </div>
              </div>
            )}
          </div>
        )}

        {/* SECTION 4: EXECUTION DETAILS */}
        {(activeTab === 'all' || activeTab === 'execution') && (
          <div className="inspector-section">
            <div
              className="inspector-section-header"
              onClick={() => toggleSection('execution')}
            >
              <div className="section-title">
                <Code2 size={14} />
                <span>4. Execution Details & Metadata</span>
              </div>
              {expandedSections.execution ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </div>

            {expandedSections.execution && (
              <div className="inspector-section-content">
                <div className="kv-grid">
                  {investigator && (
                    <div className="kv-item">
                      <span className="kv-label">Specialist Agent:</span>
                      <span className="kv-value font-mono text-xs">{investigator}</span>
                    </div>
                  )}
                  {executionDetails?.graph_node && (
                    <div className="kv-item">
                      <span className="kv-label">Graph Node ID:</span>
                      <span className="kv-value font-mono text-xs">
                        {executionDetails.graph_node}
                      </span>
                    </div>
                  )}
                  {executionDetails?.source_module && (
                    <div className="kv-item">
                      <span className="kv-label">Backend Module:</span>
                      <span className="kv-value font-mono text-xs">
                        {executionDetails.source_module}
                      </span>
                    </div>
                  )}
                  {executionDetails?.iteration && (
                    <div className="kv-item">
                      <span className="kv-label">Orchestration Iteration:</span>
                      <span className="kv-value">#{executionDetails.iteration}</span>
                    </div>
                  )}
                  {executionDetails?.sql_evidence_count !== undefined && (
                    <div className="kv-item">
                      <span className="kv-label">SQL Statements Found:</span>
                      <span className="kv-value">
                        {executionDetails.sql_evidence_count}
                      </span>
                    </div>
                  )}
                  {executionDetails?.tool_error_count !== undefined && (
                    <div className="kv-item">
                      <span className="kv-label">Tool Errors:</span>
                      <span className="kv-value">
                        {executionDetails.tool_error_count}
                      </span>
                    </div>
                  )}
                </div>

                {executionDetails?.files_inspected && executionDetails.files_inspected.length > 0 && (
                  <div className="mt-3">
                    <span className="card-subtitle">
                      Files Inspected ({executionDetails.files_inspected.length}):
                    </span>
                    <ul className="file-tag-list mt-1">
                      {executionDetails.files_inspected.map((file, i) => (
                        <li key={i} className="file-tag font-mono text-xs">
                          {file}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                <div className="mt-3">
                  <JsonViewer
                    data={executionDetails}
                    title="Backend Metadata Payload"
                    maxHeight="250px"
                  />
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </aside>
  )
}
