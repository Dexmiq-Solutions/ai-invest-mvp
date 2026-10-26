import test from 'node:test'
import assert from 'node:assert/strict'
import { buildWorkflowNodes } from './workflowMapping.js'

test('buildWorkflowNodes returns empty-safe baseline nodes when state is empty', () => {
  const nodes = buildWorkflowNodes({})
  assert.ok(Array.isArray(nodes))
  assert.strictEqual(nodes.length, 10) // validation, context, workspace, repo, context_gathering, planning, plan_validation, aggregation, reviewer, outcome
  assert.strictEqual(nodes[0].id, 'initial_validation')
  assert.strictEqual(nodes[0].status, 'pending')
})

test('buildWorkflowNodes handles input validation failure', () => {
  const state = {
    problem: '',
    validation_errors: ['Problem description is required.'],
  }
  const nodes = buildWorkflowNodes(state)
  const valNode = nodes.find(n => n.id === 'initial_validation')
  assert.strictEqual(valNode.status, 'failed')
  assert.ok(valNode.error.includes('Problem description is required.'))
})

test('buildWorkflowNodes maps plan validation gate approved state', () => {
  const state = {
    problem: 'Email mismatch in login token',
    context_status: 'sufficient',
    workspace_path: '/workspaces/ws-1',
    repository_summary: { languages: ['JavaScript'] },
    gathered_context: { relevant_files: [{ path: 'src/auth.js' }] },
    investigation_plan: [
      {
        task_id: 'TASK-CODE-001',
        assigned_agent: 'code_investigator',
        objective: 'Inspect token creation in src/auth.js',
        status: 'pending',
      },
    ],
    plan_validation: {
      is_valid: true,
      errors: [],
      warnings: [],
      validated_task_count: 1,
    },
  }
  const nodes = buildWorkflowNodes(state)
  const valNode = nodes.find(n => n.id === 'plan_validation')
  assert.ok(valNode)
  assert.strictEqual(valNode.status, 'completed')
  assert.strictEqual(valNode.output.is_valid, true)
  assert.strictEqual(valNode.output.validated_task_count, 1)

  const taskNode = nodes.find(n => n.id === 'task_TASK-CODE-001')
  assert.ok(taskNode)
  assert.strictEqual(taskNode.investigator, 'code_investigator')
  assert.strictEqual(taskNode.name, 'Code Investigator: TASK-CODE-001')
})

test('buildWorkflowNodes maps plan validation gate failed state with zero specialists dispatched', () => {
  const state = {
    problem: 'Email mismatch in login token',
    context_status: 'sufficient',
    workspace_path: '/workspaces/ws-1',
    investigation_plan: [
      {
        task_id: 'TASK-001',
        assigned_agent: 'invalid_specialist',
        objective: 'Invalid task',
      },
    ],
    plan_validation: {
      is_valid: false,
      errors: ["Unknown agent: 'invalid_specialist'"],
      warnings: [],
      validated_task_count: 0,
    },
    execution_status: 'failed',
  }
  const nodes = buildWorkflowNodes(state)
  const valNode = nodes.find(n => n.id === 'plan_validation')
  assert.ok(valNode)
  assert.strictEqual(valNode.status, 'failed')
  assert.ok(valNode.error.includes('Zero specialists dispatched'))

  const taskNode = nodes.find(n => n.id === 'task_TASK-001')
  assert.ok(taskNode)
  assert.strictEqual(taskNode.status, 'skipped')
  assert.ok(taskNode.skipReason.includes('Plan Validation Gate'))
})

test('buildWorkflowNodes maps specialist task results with findings, evidence, and SQL classification', () => {
  const state = {
    problem: 'Discrepancy in login token',
    investigation_plan: [
      {
        task_id: 'TASK-CODE-001',
        assigned_agent: 'code_investigator',
        objective: 'Inspect database lookup query',
        status: 'completed',
      },
    ],
    completed_tasks: [{ task_id: 'TASK-CODE-001' }],
    task_results: {
      'TASK-CODE-001': {
        task_id: 'TASK-CODE-001',
        findings: [
          {
            category: 'Confirmed Finding',
            statement: 'Observed user query by login email.',
          },
        ],
        evidence: [
          {
            evidence_id: 'EV-001',
            source: 'src/models/user.js',
            line: 12,
            matched_text: "SELECT * FROM users WHERE email = ?",
            sql_type: 'read_query',
            kind: 'database_read_query',
          },
        ],
      },
    },
    execution_status: 'completed',
    is_conclusive: false,
  }

  const nodes = buildWorkflowNodes(state)
  const taskNode = nodes.find(n => n.id === 'task_TASK-CODE-001')
  assert.ok(taskNode)
  assert.strictEqual(taskNode.status, 'completed')
  assert.strictEqual(taskNode.output.findings_count, 1)
  assert.strictEqual(taskNode.output.evidence_items_count, 1)
  assert.strictEqual(taskNode.executionDetails.sql_evidence_count, 1)

  const aggNode = nodes.find(n => n.id === 'state_aggregation')
  assert.ok(aggNode)
  assert.strictEqual(aggNode.status, 'completed')

  const outcomeNode = nodes.find(n => n.id === 'investigation_outcome')
  assert.ok(outcomeNode)
  assert.strictEqual(outcomeNode.output.is_conclusive, false)
  assert.strictEqual(outcomeNode.output.status_label, 'Unverified hypothesis')
})

test('buildWorkflowNodes maps Phase 7 candidate hypotheses into specialist and shared aggregation nodes', () => {
  const state = {
    problem: '401 Unauthorized on profile route',
    investigation_plan: [
      {
        task_id: 'TASK-CODE-001',
        assigned_agent: 'code_investigator',
        objective: 'Trace authorization middleware',
        status: 'completed',
      },
    ],
    task_results: {
      'TASK-CODE-001': {
        task_id: 'TASK-CODE-001',
        agent_name: 'code_investigator',
        investigator_name: 'Code Investigator',
        findings: [
          {
            statement: 'Token verification expects Bearer header',
            file_path: 'auth.js',
            line_number: 22,
          },
        ],
        hypotheses: [
          {
            hypothesis_id: 'HYP-001',
            hypothesis_title: 'Authorization header omitted by client',
            produced_by: 'Code Investigator',
            possible_cause: 'Client request omits Bearer prefix in header',
            evidence: 'auth.js:22',
            reasoning: 'Missing Bearer token causes 401 response.',
            status: 'needs_validation',
            confidence: 'medium',
            unanswered_questions: ['Does client include token in query parameters?'],
          },
        ],
      },
    },
    aggregated_hypotheses: [
      {
        hypothesis_id: 'HYP-001',
        hypothesis_title: 'Authorization header omitted by client',
        produced_by: 'Code Investigator',
        possible_cause: 'Client request omits Bearer prefix in header',
        status: 'needs_validation',
      },
    ],
    execution_status: 'completed',
    is_conclusive: false,
  }

  const nodes = buildWorkflowNodes(state)
  const taskNode = nodes.find(n => n.id === 'task_TASK-CODE-001')
  assert.ok(taskNode)
  assert.strictEqual(taskNode.output.hypotheses_count, 1)
  assert.strictEqual(taskNode.output.hypotheses_sample[0].hypothesis_id, 'HYP-001')
  assert.strictEqual(taskNode.output.hypotheses_sample[0].possible_cause, 'Client request omits Bearer prefix in header')

  const aggNode = nodes.find(n => n.id === 'state_aggregation')
  assert.ok(aggNode)
  assert.strictEqual(aggNode.output.total_aggregated_hypotheses, 1)
  assert.strictEqual(aggNode.output.hypotheses_sample.length, 1)
})

test('buildWorkflowNodes maps investigation_outcome with symptoms, diagnosis status, findings and evidence', () => {
  const state = {
    problem: 'The email in the login token does not match the email stored for the user.',
    validated_problem_context: {
      observed_behavior: 'Discrepancy in login token email',
      expected_behavior: 'Token identity matches stored user record',
      target_areas: ['authentication', 'users'],
    },
    findings: [
      {
        category: 'Confirmed Finding',
        statement: 'authService.js calls signToken with user.email',
        file_path: 'backend/src/services/authService.js',
        line_number: 18,
      },
    ],
    evidence: [
      {
        evidence_id: 'EV-001',
        source: 'backend/src/services/authService.js',
        line: 18,
        snippet: 'const token = signToken(user);',
      },
    ],
    hypotheses: [
      {
        hypothesis_id: 'HYP-001',
        hypothesis_title: 'Email Casing Mismatch',
        status: 'needs_validation',
        possible_cause: 'Email lowercasing difference',
      },
    ],
    execution_status: 'completed',
    is_conclusive: false,
    status_label: 'Unverified hypothesis',
  }

  const nodes = buildWorkflowNodes(state)
  const outcomeNode = nodes.find(n => n.id === 'investigation_outcome')
  assert.ok(outcomeNode)
  assert.strictEqual(outcomeNode.output.is_conclusive, false)
  assert.strictEqual(outcomeNode.output.diagnosis_status, 'Unverified Hypothesis')
  assert.strictEqual(outcomeNode.output.symptoms, 'Discrepancy in login token email')
  assert.strictEqual(outcomeNode.output.expected_behavior, 'Token identity matches stored user record')
  assert.deepStrictEqual(outcomeNode.output.target_areas, ['authentication', 'users'])
  assert.strictEqual(outcomeNode.output.findings_sample.length, 1)
  assert.strictEqual(outcomeNode.output.evidence_sample.length, 1)
  assert.strictEqual(outcomeNode.output.hypotheses_sample.length, 1)
})

test('buildWorkflowNodes maps Phase 8 Reviewer node with sufficiency, reasoning, and gaps', () => {
  const state = {
    problem: 'Login returns 401 Unauthorized',
    review_result: {
      decision: 'insufficient',
      is_sufficient: false,
      reasoning: 'Evidence lacks runtime authorization trace',
      identified_symptoms: ['HTTP 401 on valid login credentials'],
      hypothesis_evaluations: [
        {
          hypothesis_id: 'HYP-001',
          status: 'unverified',
          supporting_evidence_ids: ['EV-001'],
        },
      ],
      unanswered_questions: ['Does authorization middleware check casing?'],
      missing_evidence: ['Runtime header inspection'],
      further_investigation_necessary: true,
      confidence: 'medium',
    },
    investigation_iterations: 1,
    max_iterations: 3,
    execution_status: 'completed',
  }

  const nodes = buildWorkflowNodes(state)
  const reviewerNode = nodes.find(n => n.id === 'reviewer')
  assert.ok(reviewerNode)
  assert.strictEqual(reviewerNode.status, 'completed')
  assert.strictEqual(reviewerNode.output.decision, 'insufficient')
  assert.strictEqual(reviewerNode.output.is_sufficient, false)
  assert.strictEqual(reviewerNode.output.reasoning, 'Evidence lacks runtime authorization trace')
  assert.strictEqual(reviewerNode.output.hypothesis_evaluations.length, 1)
  assert.deepStrictEqual(reviewerNode.output.missing_evidence, ['Runtime header inspection'])
  assert.strictEqual(reviewerNode.output.iteration, 1)
})

test('buildWorkflowNodes maps Phase 8 Re-planning node and tags replanned tasks across iterations', () => {
  const state = {
    problem: 'Login returns 401 Unauthorized',
    investigation_iterations: 2,
    max_iterations: 3,
    investigation_history: [
      {
        iteration: 1,
        tasks_added: 1,
        review_decision: 'insufficient',
        feedback: 'Inspect auth middleware header check',
      },
    ],
    investigation_plan: [
      {
        task_id: 'TASK-001',
        assigned_agent: 'code_investigator',
        objective: 'Initial code inspection',
        iteration: 1,
        status: 'completed',
      },
      {
        task_id: 'TASK-REPLAN-1-01',
        assigned_agent: 'code_investigator',
        objective: 'Inspect token casing comparison in auth.js',
        iteration: 2,
        source: 'replanner',
        status: 'completed',
      },
    ],
    review_result: {
      decision: 'sufficient',
      is_sufficient: true,
      reasoning: 'Confirmed casing mismatch in auth header comparison.',
    },
    is_conclusive: true,
    status_label: 'Likely root cause',
    likely_root_cause: 'Casing mismatch in auth header comparison',
    execution_status: 'completed',
  }

  const nodes = buildWorkflowNodes(state)
  const replannerNode = nodes.find(n => n.id === 'replanner')
  assert.ok(replannerNode)
  assert.strictEqual(replannerNode.status, 'completed')
  assert.strictEqual(replannerNode.output.investigation_iterations, 2)
  assert.strictEqual(replannerNode.output.investigation_history.length, 1)

  const replannedTask = nodes.find(n => n.id === 'task_TASK-REPLAN-1-01')
  assert.ok(replannedTask)
  assert.strictEqual(replannedTask.isReplanned, true)
  assert.strictEqual(replannedTask.iteration, 2)
  assert.ok(replannedTask.name.includes('Re-plan Iteration 2'))

  const outcomeNode = nodes.find(n => n.id === 'investigation_outcome')
  assert.ok(outcomeNode)
  assert.strictEqual(outcomeNode.output.is_conclusive, true)
  assert.strictEqual(outcomeNode.output.status_label, 'Likely root cause')
  assert.strictEqual(outcomeNode.output.likely_root_cause, 'Casing mismatch in auth header comparison')
})

test('buildWorkflowNodes maps Phase 8 Inconclusive result when investigation limit reached', () => {
  const state = {
    problem: 'Intermittent socket disconnect',
    investigation_iterations: 3,
    max_iterations: 3,
    investigation_status: 'inconclusive',
    is_conclusive: false,
    status_label: 'Inconclusive',
    review_result: {
      decision: 'inconclusive',
      is_sufficient: false,
      reasoning: 'Evidence remains insufficient after reaching maximum investigation iterations.',
      missing_evidence: ['Network packet capture at disconnection event'],
      unanswered_questions: ['Is disconnection initiated by server timeout or client reset?'],
    },
    execution_status: 'completed',
  }

  const nodes = buildWorkflowNodes(state)
  const outcomeNode = nodes.find(n => n.id === 'investigation_outcome')
  assert.ok(outcomeNode)
  assert.strictEqual(outcomeNode.output.is_conclusive, false)
  assert.strictEqual(outcomeNode.output.status_label, 'Inconclusive')
  assert.strictEqual(outcomeNode.output.diagnosis_status, 'Inconclusive Result')
  assert.deepStrictEqual(outcomeNode.output.missing_evidence, ['Network packet capture at disconnection event'])
})

test('buildWorkflowNodes preserves Reviewer relevance assessments and approved root cause', () => {
  const state = {
    problem: 'Task creation fails when setting due dates in taskModel.js',
    review_result: {
      decision: 'sufficient',
      is_sufficient: true,
      reasoning: 'Corroborating evidence proves parameter inversion in insertWithDue.',
      approved_hypothesis_id: 'HYP-TASK-002',
      approved_hypothesis: {
        hypothesis_id: 'HYP-TASK-002',
        statement: 'insertWithDue in taskModel.js inverts dueDate and userId arguments',
      },
      hypothesis_evaluations: [
        {
          hypothesis_id: 'HYP-AUTH-001',
          is_relevant_to_problem: false,
          relevance_explanation: 'Targets auth email casing rather than taskModel creation parameters',
          status: 'supported',
          evaluation: 'Valid code finding but unrelated to task creation defect',
        },
        {
          hypothesis_id: 'HYP-TASK-002',
          is_relevant_to_problem: true,
          relevance_explanation: 'Directly addresses parameter order mismatch in insertWithDue',
          status: 'supported',
          evaluation: 'Demonstrates swapped arguments leading to SQLite constraint error',
        },
      ],
    },
    is_conclusive: true,
    status_label: 'Root cause identified',
    execution_status: 'completed',
  }

  const nodes = buildWorkflowNodes(state)
  const reviewerNode = nodes.find(n => n.id === 'reviewer')
  assert.ok(reviewerNode)
  assert.strictEqual(reviewerNode.output.approved_hypothesis_id, 'HYP-TASK-002')
  assert.strictEqual(reviewerNode.output.hypothesis_evaluations.length, 2)

  const authEval = reviewerNode.output.hypothesis_evaluations.find(e => e.hypothesis_id === 'HYP-AUTH-001')
  assert.strictEqual(authEval.is_relevant_to_problem, false)
  assert.strictEqual(authEval.is_approved, false)
  assert.ok(authEval.relevance_explanation.includes('auth email casing'))

  const taskEval = reviewerNode.output.hypothesis_evaluations.find(e => e.hypothesis_id === 'HYP-TASK-002')
  assert.strictEqual(taskEval.is_relevant_to_problem, true)
  assert.strictEqual(taskEval.is_approved, true)
  assert.ok(taskEval.relevance_explanation.includes('parameter order'))

  const outcomeNode = nodes.find(n => n.id === 'investigation_outcome')
  assert.ok(outcomeNode)
  assert.strictEqual(outcomeNode.output.is_conclusive, true)
  assert.strictEqual(outcomeNode.output.approved_hypothesis_id, 'HYP-TASK-002')
  assert.strictEqual(outcomeNode.output.likely_root_cause, 'insertWithDue in taskModel.js inverts dueDate and userId arguments')
})

test('buildWorkflowNodes maps preliminary root cause strictly from Reviewer-approved hypothesis over higher-score candidate', () => {
  const state = {
    problem: 'Task creation fails with column constraint violation',
    aggregated_hypotheses: [
      {
        hypothesis_id: 'HYP-AUTH-HIGH-SCORE',
        statement: 'Token signature expiration algorithm is misconfigured',
        confidence: 'high',
        status: 'supported',
      },
      {
        hypothesis_id: 'HYP-TASK-CORRECT',
        statement: 'insertWithDue parameter order mismatch',
        confidence: 'medium',
        status: 'supported',
      },
    ],
    review_result: {
      decision: 'sufficient',
      is_sufficient: true,
      reasoning: 'Reviewer confirmed parameter order mismatch matches task creation defect.',
      approved_hypothesis_id: 'HYP-TASK-CORRECT',
      approved_hypothesis: {
        hypothesis_id: 'HYP-TASK-CORRECT',
        statement: 'insertWithDue parameter order mismatch',
      },
      hypothesis_evaluations: [
        {
          hypothesis_id: 'HYP-AUTH-HIGH-SCORE',
          is_relevant_to_problem: false,
          relevance_explanation: 'Auth token expiration does not address task creation column errors',
          status: 'supported',
        },
        {
          hypothesis_id: 'HYP-TASK-CORRECT',
          is_relevant_to_problem: true,
          relevance_explanation: 'Swapped parameters match reported constraint failure',
          status: 'supported',
        },
      ],
    },
    supporting_evidence: [
      {
        evidence_id: 'EV-TASK-001',
        source: 'backend/src/models/taskModel.js',
        line: 13,
        matched_text: 'insertWithDue.run(task.title, task.description, task.status, task.userId, task.dueDate)',
        relationship_to_hypothesis: 'causal',
      },
    ],
    is_conclusive: true,
    execution_status: 'completed',
  }

  const nodes = buildWorkflowNodes(state)
  const outcomeNode = nodes.find(n => n.id === 'investigation_outcome')
  assert.ok(outcomeNode)
  assert.strictEqual(outcomeNode.output.is_conclusive, true)
  assert.strictEqual(outcomeNode.output.approved_hypothesis_id, 'HYP-TASK-CORRECT')
  assert.strictEqual(outcomeNode.output.likely_root_cause, 'insertWithDue parameter order mismatch')
  assert.strictEqual(outcomeNode.output.supporting_evidence.length, 1)
  assert.strictEqual(outcomeNode.output.supporting_evidence[0].evidence_id, 'EV-TASK-001')
})

test('buildWorkflowNodes shows pending replanner when Reviewer marks investigation insufficient without losing evidence', () => {
  const state = {
    problem: 'Task creation parameter order defect',
    investigation_iterations: 1,
    max_iterations: 3,
    evidence_pool: [
      {
        evidence_id: 'EV-001',
        source: 'backend/src/models/taskModel.js',
        line: 13,
        matched_text: 'insertWithDue.run(...)',
      },
    ],
    aggregated_findings: [
      {
        finding: 'Task model calls insertWithDue',
      },
    ],
    review_result: {
      decision: 'insufficient',
      is_sufficient: false,
      reasoning: 'Evidence only shows call site; SQL column definition statement missing.',
      missing_evidence: ['SQLite table schema for tasks table with column positions'],
      unanswered_questions: ['What is the defined position of user_id vs due_date in insertWithDue SQL?'],
      further_investigation_necessary: true,
    },
    execution_status: 'completed',
    is_conclusive: false,
  }

  const nodes = buildWorkflowNodes(state)
  const replannerNode = nodes.find(n => n.id === 'replanner')
  assert.ok(replannerNode)
  assert.strictEqual(replannerNode.status, 'pending')
  assert.strictEqual(replannerNode.output.total_evidence_preserved, 1)
  assert.strictEqual(replannerNode.output.total_findings_preserved, 1)
  assert.deepStrictEqual(replannerNode.output.missing_evidence_addressed, [
    'SQLite table schema for tasks table with column positions',
  ])

  const outcomeNode = nodes.find(n => n.id === 'investigation_outcome')
  assert.ok(outcomeNode)
  assert.strictEqual(outcomeNode.output.is_conclusive, false)
  assert.strictEqual(outcomeNode.output.diagnosis_status, 'Unverified Hypothesis')
})

test('buildWorkflowNodes maps Phase 9 Diagnosis and Verification results with suggested fix and verification status', () => {
  const state = {
    problem: 'Task creation fails when creating tasks with due dates in taskModel.js',
    execution_status: 'completed',
    is_conclusive: true,
    status_label: 'Likely root cause',
    likely_root_cause: 'Parameter order mismatch in insertWithDue swaps userId and dueDate',
    diagnosis: {
      root_cause: 'Parameter order mismatch in insertWithDue swaps userId and dueDate',
      status_label: 'Likely root cause',
      confidence: 'medium',
      causal_explanation: 'insertWithDue.run passes (userId, dueDate) while SQL expects (due_date, user_id).',
    },
    suggested_fix: 'return insertWithDue.run(task.title, task.description, task.status, task.dueDate, task.userId);',
    suggested_fix_details: {
      target_location: 'taskflow/backend/src/models/taskModel.js (insertWithDue)',
      recommended_change: 'Swap userId and dueDate arguments in insertWithDue.run',
      rationale: 'Aligns argument positions with SQL placeholder columns',
      contract_invariants: 'Task creation without due dates remains unchanged',
      code_snippet: 'return insertWithDue.run(task.title, task.description, task.status, task.dueDate, task.userId);',
    },
    verification_steps: [
      'Call taskModel.create with sentinel userId=9999 and dueDate=2026-12-31 and verify column bindings.',
    ],
    verification_status: 'verification blocked',
    verification_summary: {
      status: 'verification blocked',
      blockers: ['node_modules missing in workspace', 'package.json lacks test script'],
    },
    final_report: '# Investigation Report\n\n## 1. Problem Summary\n...',
    report_sections: {
      problem_summary: { reported_issue: 'Task creation fails' },
      root_cause_diagnosis: { most_likely_cause: 'Parameter order mismatch' },
    },
  }

  const nodes = buildWorkflowNodes(state)
  const outcomeNode = nodes.find(n => n.id === 'investigation_outcome')
  assert.ok(outcomeNode)
  assert.strictEqual(outcomeNode.output.is_conclusive, true)
  assert.strictEqual(outcomeNode.output.verification_status, 'verification blocked')
  assert.strictEqual(outcomeNode.executionDetails.verification_status, 'verification blocked')
  assert.strictEqual(outcomeNode.output.suggested_fix, state.suggested_fix)
  assert.strictEqual(outcomeNode.output.suggested_fix_details.target_location, 'taskflow/backend/src/models/taskModel.js (insertWithDue)')
  assert.strictEqual(outcomeNode.output.verification_steps.length, 1)
  assert.strictEqual(outcomeNode.output.causal_explanation, 'insertWithDue.run passes (userId, dueDate) while SQL expects (due_date, user_id).')
  assert.strictEqual(outcomeNode.output.no_fix_disclaimer, null)
})


