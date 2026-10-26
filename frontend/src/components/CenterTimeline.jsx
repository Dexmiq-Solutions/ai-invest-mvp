import React from 'react'
import {
  CheckCircle2,
  Clock,
  AlertOctagon,
  MinusCircle,
  Loader2,
  Timer,
  Terminal,
  Layers,
  FileSearch,
  Code2,
  GitFork,
  Cpu,
  ShieldCheck,
  CheckCheck,
  Activity,
  Bug,
  ChevronRight,
  HelpCircle,
  RotateCw,
} from 'lucide-react'

function StatusBadge({ status }) {
  switch (status) {
    case 'completed':
      return (
        <span className="node-status-badge badge-completed">
          <CheckCircle2 size={12} />
          <span>Completed</span>
        </span>
      )
    case 'running':
      return (
        <span className="node-status-badge badge-running">
          <Loader2 size={12} className="spinner" />
          <span>Running</span>
        </span>
      )
    case 'failed':
      return (
        <span className="node-status-badge badge-failed">
          <AlertOctagon size={12} />
          <span>Failed</span>
        </span>
      )
    case 'skipped':
      return (
        <span className="node-status-badge badge-skipped">
          <MinusCircle size={12} />
          <span>Skipped</span>
        </span>
      )
    case 'unresolved':
      return (
        <span className="node-status-badge badge-unresolved">
          <HelpCircle size={12} />
          <span>Unresolved</span>
        </span>
      )
    case 'pending':
    default:
      return (
        <span className="node-status-badge badge-pending">
          <Clock size={12} />
          <span>Pending</span>
        </span>
      )
  }
}

function getNodeIcon(id, investigator) {
  if (id === 'initial_validation') return <Terminal size={15} />
  if (id === 'context_check') return <Activity size={15} />
  if (id === 'workspace_setup') return <Layers size={15} />
  if (id === 'repository_analysis') return <FileSearch size={15} />
  if (id === 'context_gathering') return <Code2 size={15} />
  if (id === 'investigation_planning') return <GitFork size={15} />
  if (id === 'plan_validation') return <ShieldCheck size={15} />
  if (id === 'state_aggregation') return <Layers size={15} />
  if (id === 'reviewer') return <ShieldCheck size={15} />
  if (id === 'replanner') return <RotateCw size={15} />
  if (id === 'investigation_outcome') return <CheckCheck size={15} />
  if (id === 'diagnosis_synthesis') return <Cpu size={15} />

  if (investigator === 'code_investigator') return <Code2 size={15} />
  if (investigator === 'dependency_impact_investigator') return <GitFork size={15} />
  if (investigator === 'runtime_error_investigator') return <Bug size={15} />

  return <Terminal size={15} />
}

export default function CenterTimeline({
  nodes = [],
  selectedNodeId,
  onSelectNode,
  activeStepKey,
}) {
  const counts = {
    total: nodes.length,
    completed: nodes.filter((n) => n.status === 'completed').length,
    skipped: nodes.filter((n) => n.status === 'skipped').length,
    unresolved: nodes.filter((n) => n.status === 'unresolved').length,
    failed: nodes.filter((n) => n.status === 'failed').length,
    running: nodes.filter((n) => n.status === 'running').length,
    pending: nodes.filter((n) => n.status === 'pending').length,
  }

  return (
    <section className="panel-center">
      <div className="panel-header">
        <div className="timeline-header-info">
          <h2 className="panel-title">Node Execution Timeline</h2>
          <span className="text-muted text-xs">
            Observable backend workflow sequence
          </span>
        </div>

        {/* Status metrics bar */}
        <div className="status-metrics-bar">
          <span className="metric-pill metric-completed" title="Completed Nodes">
            {counts.completed} completed
          </span>
          {counts.unresolved > 0 && (
            <span className="metric-pill metric-unresolved" title="Unresolved Tasks">
              {counts.unresolved} unresolved
            </span>
          )}
          {counts.skipped > 0 && (
            <span className="metric-pill metric-skipped" title="Skipped Nodes">
              {counts.skipped} skipped
            </span>
          )}
          {counts.failed > 0 && (
            <span className="metric-pill metric-failed" title="Failed Nodes">
              {counts.failed} failed
            </span>
          )}
          {counts.running > 0 && (
            <span className="metric-pill metric-running" title="Running Nodes">
              {counts.running} running
            </span>
          )}
          {counts.pending > 0 && (
            <span className="metric-pill metric-pending" title="Pending Nodes">
              {counts.pending} pending
            </span>
          )}
        </div>
      </div>

      <div className="timeline-container">
        {nodes.length === 0 ? (
          <div className="timeline-empty">
            <Clock size={32} className="text-muted" />
            <p>No investigation started yet.</p>
            <span className="text-muted text-sm">
              Enter a problem statement and click "Start Investigation" to observe the node-by-node execution.
            </span>
          </div>
        ) : (
          <div className="timeline-list">
            {nodes.map((node, index) => {
              const isSelected = selectedNodeId === node.id
              const isCurrent = node.status === 'running' || (activeStepKey && node.id.includes(activeStepKey))
              const hasDuration = typeof node.duration === 'number' && node.duration >= 0

              return (
                <div
                  key={node.id}
                  className={`timeline-card ${isSelected ? 'selected' : ''} ${
                    isCurrent ? 'current-pulse' : ''
                  } status-${node.status}`}
                  onClick={() => onSelectNode(node.id)}
                >
                  {/* Timeline connector track */}
                  <div className="timeline-connector">
                    <div className={`timeline-node-icon icon-${node.status}`}>
                      {getNodeIcon(node.id, node.investigator)}
                    </div>
                    {index < nodes.length - 1 && <div className="timeline-line" />}
                  </div>

                  {/* Main card content */}
                  <div className="timeline-card-content">
                    <div className="timeline-card-header">
                      <div className="timeline-card-title-group">
                        <span className="timeline-order-badge">#{node.order}</span>
                        <h3 className="timeline-node-name">{node.name}</h3>
                        {node.phase && (
                          <span className="phase-pill">{node.phase}</span>
                        )}
                      </div>

                      <div className="timeline-card-status-group">
                        {hasDuration && (
                          <span className="duration-tag" title="Backend Execution Duration">
                            <Timer size={12} />
                            <span>{node.duration}s</span>
                          </span>
                        )}
                        <StatusBadge status={node.status} />
                      </div>
                    </div>

                    <div className="timeline-purpose">{node.purpose}</div>

                    {/* Skipped reason callout */}
                    {node.status === 'skipped' && node.skipReason && (
                      <div className="skip-reason-box">
                        <span className="skip-label">Skipped Reason:</span>
                        <span className="skip-text">{node.skipReason}</span>
                      </div>
                    )}

                    {/* Error details callout */}
                    {node.status === 'failed' && node.error && (
                      <div className="node-error-box">
                        <AlertOctagon size={14} />
                        <span>{node.error}</span>
                      </div>
                    )}

                    {/* Quick outputs footer */}
                    <div className="timeline-card-footer">
                      <div className="timeline-stats">
                        {node.output?.findings_count !== undefined && (
                          <span className="timeline-stat">
                            <strong>{node.output.findings_count}</strong> findings
                          </span>
                        )}
                        {node.output?.evidence_items_count !== undefined && (
                          <span className="timeline-stat">
                            <strong>{node.output.evidence_items_count}</strong> evidence
                          </span>
                        )}
                        {node.output?.file_count !== undefined && (
                          <span className="timeline-stat">
                            <strong>{node.output.file_count}</strong> files analyzed
                          </span>
                        )}
                        {node.output?.relevant_files_count !== undefined && (
                          <span className="timeline-stat">
                            <strong>{node.output.relevant_files_count}</strong> candidate files
                          </span>
                        )}
                        {node.output?.planned_tasks_count !== undefined && (
                          <span className="timeline-stat">
                            <strong>{node.output.planned_tasks_count}</strong> planned tasks
                          </span>
                        )}
                        {node.output?.status_label && (
                          <span className="timeline-stat text-highlight">
                            {node.output.status_label}
                          </span>
                        )}
                      </div>

                      <button
                        type="button"
                        className="btn-inspect-link"
                        onClick={(e) => {
                          e.stopPropagation()
                          onSelectNode(node.id)
                        }}
                      >
                        <span>Inspect Node</span>
                        <ChevronRight size={13} />
                      </button>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </section>
  )
}
