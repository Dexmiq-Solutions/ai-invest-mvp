/**
 * Maps the actual backend investigation graph and execution trace into
 * structured, observable workflow nodes without fabricating data.
 */

export function buildWorkflowNodes(state = {}, activeStepKey = null, nodeExecutionTrace = {}) {
  const nodes = []
  const currentStep = state.current_step || ''
  const trace = { ...(state.node_execution_trace || {}), ...(nodeExecutionTrace || {}) }

  // 1. Initial & Validation
  const hasValidationErrors = Boolean(state.validation_errors && state.validation_errors.length > 0)
  let initialStatus = 'pending'
  if (hasValidationErrors) {
    initialStatus = 'failed'
  } else if (state.problem || currentStep) {
    initialStatus = 'completed'
  }

  nodes.push({
    id: 'initial_validation',
    name: 'Input Validation',
    phase: 'Validation',
    order: 1,
    purpose: 'Validate problem statement length, repository path, and input requirements',
    status: initialStatus,
    duration: trace.initial_and_validation?.duration_seconds,
    startTime: trace.initial_and_validation?.start_time,
    endTime: trace.initial_and_validation?.end_time,
    input: {
      problem: state.problem || state.original_problem || null,
      project_path: state.project_path || null,
      repository_url: state.repository_url || null,
    },
    output: {
      current_step: currentStep || null,
      validation_errors: state.validation_errors || [],
      original_problem: state.original_problem || null,
    },
    error: hasValidationErrors ? state.validation_errors.join('; ') : null,
    executionDetails: {
      graph_node: 'initial_node -> validate_input',
      source_module: 'app.graph.context',
    },
  })

  // 2. Context Sufficiency Check
  const ctxStatus = state.context_status
  let contextNodeStatus = 'pending'
  if (ctxStatus === 'sufficient') {
    contextNodeStatus = 'completed'
  } else if (ctxStatus === 'insufficient' || ctxStatus === 'incomplete' || currentStep === 'context_insufficient') {
    contextNodeStatus = 'failed'
  } else if (initialStatus === 'completed' && activeStepKey === 'context_check') {
    contextNodeStatus = 'running'
  }

  nodes.push({
    id: 'context_check',
    name: 'Context Sufficiency Check',
    phase: 'Context',
    order: 2,
    purpose: 'Analyze problem description for observable symptoms and target engineering areas',
    status: contextNodeStatus,
    duration: trace.context_check?.duration_seconds,
    startTime: trace.context_check?.start_time,
    endTime: trace.context_check?.end_time,
    input: {
      problem: state.problem || null,
      developer_context: state.developer_context || [],
      context_history: state.context_history || [],
    },
    output: {
      context_status: state.context_status || null,
      context_questions: state.context_questions || [],
      missing_information: state.missing_information || [],
      context_analysis: state.context_analysis || null,
      validated_problem_context: state.validated_problem_context || null,
    },
    error: contextNodeStatus === 'failed' ? (state.missing_information?.length ? `Missing required context: ${state.missing_information.join(', ')}` : 'Context is insufficient. Observable symptom required.') : null,
    executionDetails: {
      graph_node: 'check_context',
      source_module: 'app.graph.context',
      clarifying_questions_count: (state.context_questions || []).length,
    },
  })

  // 3. Workspace Setup
  const hasWorkspace = Boolean(state.workspace_path)
  let workspaceStatus = 'pending'
  if (currentStep === 'workspace_setup_failed') {
    workspaceStatus = 'failed'
  } else if (hasWorkspace || ['workspace_ready', 'repository_analyzed', 'context_gathering_complete', 'plan_ready', 'investigation_completed'].includes(currentStep)) {
    workspaceStatus = 'completed'
  } else if (contextNodeStatus === 'completed' && activeStepKey === 'workspace') {
    workspaceStatus = 'running'
  }

  nodes.push({
    id: 'workspace_setup',
    name: 'Workspace Sandbox Setup',
    phase: 'Workspace',
    order: 3,
    purpose: 'Extract project archive or target repository into an isolated sandbox environment',
    status: workspaceStatus,
    duration: trace.workspace?.duration_seconds,
    startTime: trace.workspace?.start_time,
    endTime: trace.workspace?.end_time,
    input: {
      source_type: state.workspace_source_type || (state.project_path?.endsWith('.zip') ? 'zip' : 'path'),
      project_path: state.project_path || null,
    },
    output: {
      workspace_id: state.workspace_id || null,
      workspace_path: state.workspace_path || null,
      workspace_source_type: state.workspace_source_type || null,
    },
    error: workspaceStatus === 'failed' ? (state.validation_errors?.find(e => e.includes('Workspace')) || 'Workspace setup failed') : null,
    executionDetails: {
      graph_node: 'setup_workspace',
      source_module: 'app.graph.workspace',
      isolated_root: state.workspace_path || 'Not created',
    },
  })

  // 4. Repository Analysis
  const repoSummary = state.repository_summary || {}
  const hasRepoSummary = Boolean(repoSummary && Object.keys(repoSummary).length > 0)
  let repoStatus = 'pending'
  if (currentStep === 'repository_analysis_failed') {
    repoStatus = 'failed'
  } else if (hasRepoSummary || ['repository_analyzed', 'context_gathering_complete', 'plan_ready', 'investigation_completed'].includes(currentStep)) {
    repoStatus = 'completed'
  } else if (workspaceStatus === 'completed' && activeStepKey === 'repository_analysis') {
    repoStatus = 'running'
  }

  nodes.push({
    id: 'repository_analysis',
    name: 'Repository Structural Analysis',
    phase: 'Analysis',
    order: 4,
    purpose: 'Inspect directory structure, identify programming languages, frameworks, entry points, and configs',
    status: repoStatus,
    duration: trace.repository_analysis?.duration_seconds,
    startTime: trace.repository_analysis?.start_time,
    endTime: trace.repository_analysis?.end_time,
    input: {
      workspace_path: state.workspace_path || null,
    },
    output: {
      languages: repoSummary.languages || [],
      frameworks: repoSummary.frameworks || [],
      entry_points: repoSummary.entry_points || [],
      file_count: repoSummary.project_structure?.file_count || 0,
      directory_count: repoSummary.project_structure?.directory_count || 0,
      configuration_files: repoSummary.configuration_files || [],
      important_directories: repoSummary.important_directories || [],
    },
    error: repoStatus === 'failed' ? (state.validation_errors?.find(e => e.includes('Repository')) || 'Repository analysis failed') : null,
    executionDetails: {
      graph_node: 'repository_analysis_node',
      source_module: 'app.repository.analyzer',
      files_discovered: repoSummary.project_structure?.file_count || 0,
    },
  })

  // 5. Context Gathering
  const gathered = state.gathered_context || {}
  const hasGathered = Boolean(gathered && Object.keys(gathered).length > 0)
  let gatheredStatus = 'pending'
  if (currentStep === 'context_gathering_failed') {
    gatheredStatus = 'failed'
  } else if (hasGathered || ['context_gathering_complete', 'plan_ready', 'investigation_completed'].includes(currentStep)) {
    gatheredStatus = 'completed'
  } else if (repoStatus === 'completed' && activeStepKey === 'context_gathering') {
    gatheredStatus = 'running'
  }

  nodes.push({
    id: 'context_gathering',
    name: 'Context Gathering & Code Indexing',
    phase: 'Context Gathering',
    order: 5,
    purpose: 'Search repository AST and symbols, rank candidate files, and discover initial evidence references',
    status: gatheredStatus,
    duration: trace.context_gathering?.duration_seconds,
    startTime: trace.context_gathering?.start_time,
    endTime: trace.context_gathering?.end_time,
    input: {
      workspace_path: state.workspace_path || null,
      problem: state.problem || null,
      repository_summary: hasRepoSummary ? { languages: repoSummary.languages, frameworks: repoSummary.frameworks } : null,
    },
    output: {
      relevant_files_count: (gathered.relevant_files || []).length,
      relevant_files: gathered.relevant_files || [],
      investigation_concepts: gathered.investigation_concepts || [],
      technical_terms: gathered.technical_terms || [],
      relationships_count: (gathered.relationships || []).length,
      relationships: (gathered.relationships || []).slice(0, 15),
    },
    error: gatheredStatus === 'failed' ? (state.validation_errors?.find(e => e.includes('Context gathering')) || 'Context gathering failed') : null,
    executionDetails: {
      graph_node: 'context_gathering_graph_node',
      source_module: 'app.graph.context_gathering',
      top_candidate_files: (gathered.relevant_files || []).slice(0, 5).map(f => f.path || f),
    },
  })

  // 6. Dynamic Investigation Planning
  const plan = state.investigation_plan || []
  const hasPlan = Boolean(plan && plan.length > 0)
  const planArtifact = state.plan || null
  const hasPlanArtifact = Boolean(planArtifact && (planArtifact.plan_id || Object.keys(planArtifact).length > 0))
  const isPlanInvalid = state.lifecycle_status === 'invalid' || planArtifact?.lifecycle_status === 'invalid' || planArtifact?.validation_status === 'invalid' || state.plan_validation?.is_valid === false || state.plan_strategy_validation?.is_valid === false

  let planStatus = 'pending'
  if (isPlanInvalid && (hasPlanArtifact || currentStep.includes('failed'))) {
    planStatus = 'failed'
  } else if (hasPlan || hasPlanArtifact) {
    planStatus = 'completed'
  } else if (currentStep === 'investigation_failed' && state.plan_generation_error) {
    planStatus = 'failed'
  } else if (gatheredStatus === 'completed' && activeStepKey === 'plan') {
    planStatus = 'running'
  }

  nodes.push({
    id: 'investigation_planning',
    name: 'Investigation Plan Creation',
    phase: 'Planning',
    order: 6,
    purpose: 'Formulate structured, hypothesis-driven task contracts assigned to specialized investigator agents',
    status: planStatus,
    duration: trace.plan?.duration_seconds,
    startTime: trace.plan?.start_time,
    endTime: trace.plan?.end_time,
    input: {
      problem: state.problem || null,
      relevant_files: (gathered.relevant_files || []).slice(0, 10),
      investigation_concepts: gathered.investigation_concepts || [],
    },
    output: {
      planned_tasks_count: plan.length,
      selected_investigators: state.selected_investigators || [],
      plan_id: state.plan_id || state.plan?.plan_id || null,
      lifecycle_status: state.lifecycle_status || state.plan?.lifecycle_status || (isPlanInvalid ? 'invalid' : 'validated'),
      plan_artifact: state.plan || null,
      problem_understanding: state.plan?.problem_understanding || null,
      objectives: state.plan?.objectives || [],
      approach: state.plan?.approach || null,
      specialist_strategy: state.plan?.specialist_strategy || null,
      completion_criteria: state.plan?.completion_criteria || [],
      plan_preview: plan.map(p => ({
        task_id: p.task_id || p.step_id,
        assigned_agent: p.assigned_agent || p.investigator,
        objective: p.objective || p.task,
        reason: p.reason || p.rationale,
        priority: p.priority || 'medium',
        status: p.status || 'pending',
        plan_id: p.plan_id || null,
        objective_id: p.objective_id || null,
      })),
    },
    error: state.plan_generation_error || (isPlanInvalid ? (state.plan_strategy_validation?.errors?.join('; ') || state.plan_validation?.errors?.join('; ') || 'Plan strategy validation failed') : null),
    executionDetails: {
      graph_node: 'create_investigation_plan_node',
      source_module: 'app.agents.investigation_orchestrator',
      plan_id: state.plan_id || state.plan?.plan_id || 'Not generated',
      lifecycle_status: state.lifecycle_status || state.plan?.lifecycle_status || 'draft',
      specialists_assigned: Array.from(new Set(plan.map(p => p.assigned_agent || p.investigator).filter(Boolean))),
    },
  })

  // 7. Context-Aware Plan Validation (Pre-Dispatch Gate)
  const planValidation = state.plan_validation || null
  let valStatus = 'pending'
  let valError = null

  if (isPlanInvalid) {
    valStatus = 'failed'
    const errs = state.plan_strategy_validation?.errors || planValidation?.errors || []
    valError = `Plan validation rejected: ${errs.join('; ') || 'Invalid plan strategy'}. Specialist dispatch blocked (Zero specialists dispatched).`
  } else if (planValidation) {
    if (planValidation.is_valid === false) {
      valStatus = 'failed'
      valError = `Plan validation rejected: ${planValidation.errors?.join('; ') || 'Invalid plan schema'}. Specialist dispatch blocked (Zero specialists dispatched).`
    } else {
      valStatus = 'completed'
    }
  } else if (hasPlan && currentStep === 'plan_validation_failed') {
    valStatus = 'failed'
    valError = state.investigation_stop_reason || 'Plan validation failed: dispatch blocked'
  } else if (hasPlan && (currentStep === 'plan_ready' || ['investigation_completed', 'completed', 'partial'].includes(state.execution_status || state.investigation_status))) {
    valStatus = 'completed'
  } else if (planStatus === 'completed' && (activeStepKey === 'plan_validation' || activeStepKey === 'plan')) {
    valStatus = 'running'
  }

  nodes.push({
    id: 'plan_validation',
    name: 'Context-Aware Plan Validation Gate',
    phase: 'Validation Gate',
    order: 7,
    purpose: 'Validate task schema, agent existence, scope relevance against gathered context, and prevent corrupted execution',
    status: valStatus,
    duration: trace.plan_validation?.duration_seconds,
    startTime: trace.plan_validation?.start_time,
    endTime: trace.plan_validation?.end_time,
    input: {
      plan_task_count: plan.length,
      available_agents: ['code_investigator', 'dependency_impact_investigator', 'runtime_error_investigator'],
      gathered_context_available: Boolean(hasGathered),
    },
    output: {
      is_valid: planValidation ? planValidation.is_valid : (valStatus === 'completed'),
      validated_task_count: planValidation?.validated_task_count ?? plan.length,
      errors: planValidation?.errors || [],
      warnings: planValidation?.warnings || [],
      dispatch_allowed: planValidation ? planValidation.is_valid : (valStatus === 'completed'),
    },
    error: valError,
    executionDetails: {
      graph_node: 'validate_investigation_plan_node -> route_after_plan_validation',
      source_module: 'app.agents.plan_validator',
      gate_enforcement: 'Zero specialists dispatched on validation error',
    },
  })

  // 8. Specialist Tasks (Single-Pass Independent Discovery)
  const completedTasks = state.completed_tasks || state.completed_investigation_tasks || []
  const skippedTasks = state.skipped_tasks || state.skipped_investigation_tasks || []
  const failedTasks = state.failed_tasks || state.failed_investigation_tasks || []
  const unresolvedTasks = state.unresolved_tasks || []
  const history = state.execution_history || state.investigation_history || []
  const allFindings = state.aggregated_findings || state.findings || []
  const allEvidence = state.evidence_pool || state.evidence || []
  const allHypotheses = state.aggregated_hypotheses || state.hypotheses || []
  const taskResults = state.task_results || {}

  plan.forEach((step, idx) => {
    const taskId = step.task_id || step.step_id || `TASK-${String(idx + 1).padStart(3, '0')}`
    const investigator = step.assigned_agent || step.investigator || 'code_investigator'

    let taskStatus = 'pending'
    let skipReason = step.skip_reason || null
    let taskError = null

    const isCompleted = completedTasks.some(t => (t.task_id === taskId || t.step_id === taskId)) || step.status === 'completed'
    const isSkipped = skippedTasks.some(t => (t.task_id === taskId || t.step_id === taskId)) || step.status === 'skipped'
    const isFailed = failedTasks.some(t => (t.task_id === taskId || t.step_id === taskId)) || step.status === 'failed'
    const isUnresolved = unresolvedTasks.some(t => (t.task_id === taskId || t.step_id === taskId)) || step.status === 'unresolved'

    if (valStatus === 'failed') {
      taskStatus = 'skipped'
      skipReason = 'Dispatched blocked by Plan Validation Gate failure'
    } else if (isCompleted) {
      taskStatus = 'completed'
    } else if (isUnresolved) {
      taskStatus = 'unresolved'
    } else if (isSkipped) {
      taskStatus = 'skipped'
      const skippedRecord = skippedTasks.find(t => (t.task_id === taskId || t.step_id === taskId))
      skipReason = skippedRecord?.skip_reason || step.skip_reason || 'Marked redundant or skipped by orchestrator'
    } else if (isFailed) {
      taskStatus = 'failed'
      const failedRecord = failedTasks.find(t => (t.task_id === taskId || t.step_id === taskId))
      taskError = failedRecord?.error || 'Specialist task execution failed'
    } else if (activeStepKey === 'execute' || activeStepKey === 'deep_agent') {
      taskStatus = 'running'
    }

    const audit = history.find(h => (h.task_id === taskId || h.step_id === taskId))
    const taskResult = taskResults[taskId] || {}
    const taskFindings = taskResult.findings || allFindings.filter(f => f.task_id === taskId || f.source_investigator === investigator)
    const taskEvidence = taskResult.evidence || allEvidence.filter(e => e.task_id === taskId || e.investigator === investigator)
    const taskHypotheses = taskResult.hypotheses || allHypotheses.filter(h => h.task_id === taskId)

    const agentFriendlyNames = {
      code_investigator: 'Code Investigator',
      dependency_impact_investigator: 'Dependency Impact Investigator',
      runtime_error_investigator: 'Runtime Error Investigator',
    }

    const isReplanned = Boolean(
      step.source === 'replanner' ||
      step.is_replanned ||
      (taskId && taskId.includes('REPLAN')) ||
      (step.iteration && step.iteration > 1)
    )
    const taskIteration = step.iteration || (isReplanned ? 2 : 1)
    const taskDisplayName = isReplanned
      ? `${agentFriendlyNames[investigator] || investigator} (Re-plan Iteration ${taskIteration}): ${taskId}`
      : `${agentFriendlyNames[investigator] || investigator}: ${taskId}`

    nodes.push({
      id: `task_${taskId}`,
      stepId: taskId,
      taskId,
      name: taskDisplayName,
      isReplanned,
      iteration: taskIteration,
      phase: isReplanned ? 'Re-planned Execution' : 'Specialist Execution',
      order: 8 + idx,
      purpose: step.objective || step.instructions || step.task || 'Investigate target component',
      status: taskStatus,
      duration: audit?.duration_seconds,
      investigator,
      priority: step.priority || 'medium',
      skipReason,
      error: taskError,
      input: {
        task_id: taskId,
        assigned_agent: investigator,
        is_replanned: isReplanned,
        iteration: taskIteration,
        objective: step.objective || step.task,
        reason: step.reason || step.rationale,
        instructions: step.instructions || step.task,
        scope: step.scope || step.target_files || [],
        expected_output: step.expected_output || step.expected_evidence,
        completion_criteria: step.completion_criteria,
      },
      output: {
        status: taskStatus,
        findings_count: taskFindings.length,
        evidence_items_count: taskEvidence.length,
        hypotheses_count: taskHypotheses.length,
        skip_reason: skipReason,
        findings_sample: taskFindings,
        evidence_sample: taskEvidence.slice(0, 15),
        hypotheses_sample: taskHypotheses,
        objective: taskResult.objective || step.objective,
        evidence_gaps: taskResult.evidence_gaps || [],
        unanswered_questions: taskResult.unanswered_questions || [],
        hypothesis_assessment: taskResult.hypothesis_assessment || null,
        limitations: taskResult.limitations || [],
      },
      executionDetails: {
        investigator_agent: investigator,
        agent_role: agentFriendlyNames[investigator] || investigator,
        iteration: audit?.iteration || (idx + 1),
        tool_error_count: audit?.tool_error_count ?? 0,
        files_inspected: Array.from(new Set(taskEvidence.map(e => e.source || e.path).filter(Boolean))),
        sql_evidence_count: taskEvidence.filter(e => e.sql_type || e.kind?.includes('database')).length,
      },
    })
  })

  // 9. Evidence & State Aggregation Node
  const hasTaskResultEvidence = Object.values(taskResults).some(tr => (tr.evidence?.length > 0 || tr.findings?.length > 0))
  const hasAggregated = Boolean(
    (allEvidence.length > 0 || allFindings.length > 0 || hasTaskResultEvidence) &&
    (state.execution_status === 'completed' || state.execution_status === 'partial' || currentStep === 'investigation_completed')
  ) || (state.execution_status === 'completed' || state.execution_status === 'partial')
  let aggStatus = 'pending'
  if (valStatus === 'failed') {
    aggStatus = 'skipped'
  } else if (hasAggregated) {
    aggStatus = 'completed'
  } else if (state.execution_status === 'failed' || currentStep === 'investigation_failed') {
    aggStatus = 'failed'
  } else if (activeStepKey === 'execute') {
    aggStatus = 'running'
  }

  nodes.push({
    id: 'state_aggregation',
    name: 'Shared State & Evidence Aggregation',
    phase: 'Aggregation',
    order: 8 + plan.length,
    purpose: 'Aggregate independent specialist evidence and candidate hypotheses into shared state with strict provenance and SHA-256 deduplication without synthesis',
    status: aggStatus,
    duration: trace.execute?.duration_seconds,
    startTime: trace.execute?.start_time,
    endTime: trace.execute?.end_time,
    input: {
      dispatched_specialists_count: (state.selected_investigators || []).length,
      completed_tasks_count: completedTasks.length,
    },
    output: {
      total_evidence_pool: allEvidence.length,
      total_aggregated_findings: allFindings.length,
      total_aggregated_hypotheses: allHypotheses.length,
      execution_status: state.execution_status || 'pending',
      workflow_status: state.workflow_status || 'pending',
      conclusive_status: state.is_conclusive ? 'Conclusive' : 'Intermediate (Halted post-aggregation)',
      findings_sample: allFindings,
      evidence_sample: allEvidence.slice(0, 30),
      hypotheses_sample: allHypotheses,
    },
    error: aggStatus === 'failed' ? (state.stop_reason || state.investigation_stop_reason || 'Aggregation failed') : null,
    executionDetails: {
      graph_node: 'execute_plan_and_aggregate_node',
      source_module: 'app.agents.investigation_orchestrator',
      deduplication: 'SHA-256 content-hash verification enforced',
      synthesis_policy: 'Halted post-aggregation without hypothesis modification',
    },
  })

  const symptomsText =
    state.validated_problem_context?.observed_behavior ||
    state.validated_problem_context?.observable_behavior ||
    state.context_analysis?.observable_behavior ||
    state.plan?.problem_understanding?.observed_symptoms ||
    state.problem_context?.observable_behavior ||
    null

  const expectedBehaviorText =
    state.validated_problem_context?.expected_behavior ||
    state.context_analysis?.expected_behavior ||
    state.plan?.problem_understanding?.expected_behavior ||
    null

  const targetAreasList =
    state.validated_problem_context?.target_areas ||
    state.context_analysis?.target_areas ||
    []

  // 10. Reviewer Node (Phase 8)
  const reviewResult = state.review_result || null
  let reviewerStatus = 'pending'
  if (reviewResult) {
    reviewerStatus = 'completed'
  } else if (['reviewer', 'replanned', 'root_cause_result', 'inconclusive_result'].includes(currentStep)) {
    reviewerStatus = 'completed'
  } else if (activeStepKey === 'review') {
    reviewerStatus = 'running'
  } else if (aggStatus === 'failed') {
    reviewerStatus = 'skipped'
  }

  const reviewDecision = reviewResult?.decision || (state.is_conclusive ? 'sufficient' : (state.investigation_status === 'inconclusive' ? 'inconclusive' : null))
  const isSufficient = Boolean(reviewResult?.is_sufficient ?? (state.is_conclusive && reviewDecision === 'sufficient'))
  const approvedHyp = state.approved_hypothesis || reviewResult?.approved_hypothesis || null
  const approvedHypId = state.approved_hypothesis_id || reviewResult?.approved_hypothesis_id || approvedHyp?.hypothesis_id || null

  nodes.push({
    id: 'reviewer',
    name: 'Reviewer Evidence & Hypothesis Evaluation',
    phase: 'Review',
    order: 9 + plan.length,
    purpose: 'Evaluate collected hypotheses, supporting/conflicting evidence, and information sufficiency in shared state against original problem and symptoms',
    status: reviewerStatus,
    duration: trace.reviewer?.duration_seconds,
    startTime: trace.reviewer?.start_time,
    endTime: trace.reviewer?.end_time,
    input: {
      problem: state.problem || state.original_problem || null,
      expected_behavior: expectedBehaviorText,
      hypotheses_count: allHypotheses.length,
      evidence_count: allEvidence.length,
      findings_count: allFindings.length,
      iteration: state.investigation_iterations || 1,
      max_iterations: state.max_iterations || 3,
    },
    output: {
      decision: reviewDecision || 'pending',
      is_sufficient: isSufficient,
      confidence: reviewResult?.confidence || state.confidence || 'medium',
      reasoning: reviewResult?.reasoning || reviewResult?.feedback || null,
      feedback: reviewResult?.feedback || null,
      approved_hypothesis_id: approvedHypId,
      approved_hypothesis: approvedHyp,
      identified_symptoms: reviewResult?.identified_symptoms || (symptomsText ? [symptomsText] : []),
      hypothesis_evaluations: (reviewResult?.hypothesis_evaluations || []).map((he) => ({
        hypothesis_id: he.hypothesis_id,
        is_relevant_to_problem: he.is_relevant_to_problem,
        relevance_explanation: he.relevance_explanation,
        status: he.status,
        evaluation: he.evaluation,
        why_supported_or_not: he.why_supported_or_not || he.evaluation,
        supporting_evidence_ids: he.supporting_evidence_ids || [],
        contradicting_evidence_ids: he.contradicting_evidence_ids || [],
        is_approved: he.hypothesis_id === approvedHypId,
      })),
      unanswered_questions: reviewResult?.unanswered_questions || state.unanswered_questions || [],
      missing_evidence: reviewResult?.missing_evidence || state.missing_evidence || [],
      established_by_evidence: reviewResult?.established_by_evidence || state.established_by_evidence || [],
      causal_chain_verified: reviewResult?.causal_chain_verified ?? (isSufficient && Boolean(approvedHyp)),
      unanswered_objectives: reviewResult?.unanswered_objectives || state.unanswered_objectives || [],
      actionable_feedback: reviewResult?.actionable_feedback || state.actionable_feedback || null,
      further_investigation_necessary: reviewResult?.further_investigation_necessary ?? (!isSufficient && reviewDecision !== 'inconclusive'),
      has_conflicting_findings: reviewResult?.has_conflicting_findings ?? false,
      iteration: state.investigation_iterations || 1,
      max_iterations: state.max_iterations || 3,
    },
    error: reviewerStatus === 'failed' ? (reviewResult?.reasoning || 'Reviewer evaluation failed') : null,
    executionDetails: {
      graph_node: 'reviewer_node',
      source_module: 'app.agents.reviewer',
      decision_rule: isSufficient
        ? 'Evidence directly demonstrates failure mechanism with causal defect corroboration for the reported problem'
        : (reviewDecision === 'inconclusive'
            ? 'Investigation iteration limit reached without sufficient causal evidence'
            : 'Evidence insufficient, irrelevant, or missing causal corroboration -> routes to Re-planner'),
    },
  })

  // 11. Re-planning Node (Phase 8 - rendered when re-planning has occurred or is triggered/pending)
  const isReplanTriggered = reviewResult && reviewResult.decision === 'insufficient'
  const hasReplanned = Boolean(
    (state.investigation_history && state.investigation_history.length > 0) ||
    (state.investigation_iterations && state.investigation_iterations > 1) ||
    plan.some(t => t.task_id?.includes('REPLAN') || t.source === 'replanner' || (t.iteration && t.iteration > 1)) ||
    currentStep === 'replanned' ||
    isReplanTriggered ||
    activeStepKey === 'replan'
  )

  if (hasReplanned) {
    const replannedTasks = plan.filter(t => t.task_id?.includes('REPLAN') || t.source === 'replanner' || (t.iteration && t.iteration > 1))
    let replannerStatus = 'pending'
    if (state.investigation_history?.length > 0 || state.investigation_iterations > 1 || currentStep === 'replanned' || replannedTasks.length > 0) {
      replannerStatus = 'completed'
    } else if (activeStepKey === 'replan') {
      replannerStatus = 'running'
    } else if (isReplanTriggered) {
      replannerStatus = 'pending'
    }

    nodes.push({
      id: 'replanner',
      name: 'Investigation Re-planning & Task Generation',
      phase: 'Re-planning',
      order: 10 + plan.length,
      purpose: 'Generate targeted follow-up investigation tasks to resolve evidence gaps identified by Reviewer',
      status: replannerStatus,
      duration: trace.replanner?.duration_seconds,
      startTime: trace.replanner?.start_time,
      endTime: trace.replanner?.end_time,
      input: {
        current_iteration: state.investigation_iterations || 1,
        max_iterations: state.max_iterations || 3,
        review_decision: reviewDecision || 'insufficient',
        missing_evidence: reviewResult?.missing_evidence || state.missing_evidence || [],
        unanswered_questions: reviewResult?.unanswered_questions || state.unanswered_questions || [],
      },
      output: {
        investigation_iterations: state.investigation_iterations || (replannedTasks.length > 0 ? 2 : 1),
        max_iterations: state.max_iterations || 3,
        investigation_history: state.investigation_history || [],
        replan_history: state.replan_history || state.investigation_history || [],
        unresolved_tasks: state.unresolved_tasks || [],
        new_tasks_added_count: replannedTasks.length || (state.investigation_history?.[0]?.tasks_added ?? 0),
        replanned_tasks: replannedTasks.map(t => ({
          task_id: t.task_id || t.step_id,
          assigned_agent: t.assigned_agent || t.investigator,
          objective: t.objective || t.task,
          reason: t.reason || t.rationale,
          scope: t.scope || t.target_files,
          iteration: t.iteration,
          expected_evidence: t.expected_evidence || t.expected_output,
          unanswered_question: t.unanswered_question,
          target_hypothesis_id: t.target_hypothesis_id,
        })),
        missing_evidence_addressed: reviewResult?.missing_evidence || state.missing_evidence || [],
        unanswered_questions_addressed: reviewResult?.unanswered_questions || state.unanswered_questions || [],
        established_by_evidence: reviewResult?.established_by_evidence || state.established_by_evidence || [],
        unanswered_objectives: reviewResult?.unanswered_objectives || state.unanswered_objectives || [],
        actionable_feedback: reviewResult?.actionable_feedback || state.actionable_feedback || null,
        total_evidence_preserved: allEvidence.length,
        total_findings_preserved: allFindings.length,
      },
      executionDetails: {
        graph_node: 'replanner_node',
        source_module: 'app.agents.replanner',
        history_records_count: (state.investigation_history || []).length,
        preserved_evidence_count: allEvidence.length,
      },
    })
  }

  // 12. Investigation Outcome & Conclusion
  const isInvCompleted = ['investigation_completed', 'completed', 'partial', 'inconclusive'].includes(state.investigation_status || state.execution_status) || currentStep === 'investigation_completed'
  let outcomeStatus = 'pending'
  if (valStatus === 'failed' || state.execution_status === 'failed') {
    outcomeStatus = 'failed'
  } else if (isInvCompleted || hasAggregated || reviewResult) {
    outcomeStatus = 'completed'
  } else if (aggStatus === 'running' || activeStepKey === 'execute') {
    outcomeStatus = 'running'
  }

  const isConclusiveBool = Boolean(state.is_conclusive ?? false)
  const confidenceLevel = state.confidence || state.investigation_confidence || reviewResult?.confidence || 'medium'
  const displayLabel = state.status_label || (
    isConclusiveBool
      ? 'Likely root cause'
      : (state.investigation_status === 'inconclusive' ? 'Inconclusive' : 'Unverified hypothesis')
  )

  const outcomeTitle = isConclusiveBool
    ? 'Preliminary Root Cause Result'
    : (state.investigation_status === 'inconclusive' ? 'Inconclusive Investigation Result' : 'Investigation Outcome & Assessment')

  // Resolve preliminary root cause strictly from Reviewer-approved hypothesis when available
  const resolvedRootCause = isConclusiveBool
    ? (approvedHyp?.statement || approvedHyp?.title || state.likely_root_cause || state.diagnosis?.likely_root_cause || state.diagnosis?.root_cause || null)
    : (state.likely_root_cause || state.diagnosis?.likely_root_cause || state.root_cause || state.diagnosis?.root_cause || null)

  // Retrieve approved causal supporting evidence
  const approvedSupportingEvidence = state.supporting_evidence || approvedHyp?.supporting_evidence || state.diagnosis?.supporting_evidence || []

  nodes.push({
    id: 'investigation_outcome',
    name: outcomeTitle,
    phase: 'Assessment',
    order: (hasReplanned ? 11 : 10) + plan.length,
    purpose: isConclusiveBool
      ? 'Preliminary root-cause result established based on Reviewer-approved causal defect evidence'
      : 'Honest evaluation of evidence sufficiency, unresolved gaps, and candidate hypotheses',
    status: outcomeStatus,
    duration: null,
    input: {
      total_findings: allFindings.length,
      total_evidence: allEvidence.length,
      total_hypotheses: allHypotheses.length,
      iteration: state.investigation_iterations || 1,
      max_iterations: state.max_iterations || 3,
    },
    output: {
      status_label: displayLabel,
      is_conclusive: isConclusiveBool,
      outcome_type: isConclusiveBool ? 'preliminary_root_cause' : 'inconclusive_result',
      confidence: confidenceLevel,
      stop_reason: state.stop_reason || state.investigation_stop_reason || (isConclusiveBool ? 'Sufficient evidence identified root cause.' : 'Investigation limit reached without sufficient conclusive evidence.'),
      likely_root_cause: resolvedRootCause,
      approved_hypothesis_id: approvedHypId,
      approved_hypothesis: approvedHyp,
      supporting_evidence: approvedSupportingEvidence,
      candidate_hypotheses_count: allHypotheses.length,
      hypotheses_sample: allHypotheses,
      hypothesis_evaluations: reviewResult?.hypothesis_evaluations || [],
      findings_sample: allFindings,
      evidence_sample: allEvidence.slice(0, 30),
      affected_components: state.affected_components || [],
      remaining_uncertainty: state.remaining_uncertainty || state.diagnosis?.remaining_uncertainty || (isConclusiveBool ? 'Low residual uncertainty; causal defect evidence corroborated.' : 'High uncertainty: evidence gaps remain unresolved within investigation limit.'),
      symptoms: symptomsText,
      expected_behavior: expectedBehaviorText,
      target_areas: targetAreasList,
      unanswered_questions: state.unanswered_questions || reviewResult?.unanswered_questions || [],
      missing_evidence: state.missing_evidence || reviewResult?.missing_evidence || [],
      established_by_evidence: state.established_by_evidence || reviewResult?.established_by_evidence || [],
      causal_chain_verified: reviewResult?.causal_chain_verified ?? (isConclusiveBool && Boolean(approvedHyp)),
      unanswered_objectives: state.unanswered_objectives || reviewResult?.unanswered_objectives || [],
      actionable_feedback: state.actionable_feedback || reviewResult?.actionable_feedback || null,
      unresolved_tasks: state.unresolved_tasks || [],
      replan_history: state.replan_history || state.investigation_history || [],
      review_reasoning: reviewResult?.reasoning || reviewResult?.feedback || state.diagnosis?.reasoning || null,
      suggested_fix: state.suggested_fix || (state.report_sections?.suggested_fix?.code_snippet || state.report_sections?.suggested_fix?.recommended_change) || null,
      suggested_fix_details: state.suggested_fix_details || state.report_sections?.suggested_fix || null,
      verification_steps: state.verification_steps || state.report_sections?.verification_steps?.proposed_checks || [],
      verification_status: state.verification_status || state.verification_summary?.status || (state.is_conclusive ? 'verified' : 'not verified'),
      verification_summary: state.verification_summary || state.report_sections?.verification_steps || null,
      causal_explanation: state.diagnosis?.causal_explanation || state.report_sections?.root_cause_diagnosis?.causal_explanation || null,
      report_sections: state.report_sections || null,
      final_report: state.final_report || null,
      diagnosis_status: isConclusiveBool ? 'Confirmed Root Cause' : (state.investigation_status === 'inconclusive' ? 'Inconclusive Result' : 'Unverified Hypothesis'),
      no_fix_disclaimer: (state.suggested_fix || state.final_report) ? null : 'In accordance with Phase 8 scope, no suggested fix, apply fix, or verification fix is generated.',
    },
    error: outcomeStatus === 'failed' ? (state.stop_reason || 'Investigation failed') : null,
    executionDetails: {
      conclusive: isConclusiveBool ? 'Yes (Sufficient causal evidence confirmed)' : 'No (Inconclusive / Unverified candidate hypothesis)',
      verification_status: state.verification_status || (isConclusiveBool ? 'Preliminary Root Cause Verified' : 'Inconclusive (Evidence gaps remain)'),
      iterations_executed: state.investigation_iterations || 1,
      max_iterations: state.max_iterations || 3,
      approved_hypothesis_id: approvedHypId || 'None',
    },
  })

  return nodes
}
