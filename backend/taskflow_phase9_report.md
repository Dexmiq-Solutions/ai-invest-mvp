# Investigation Report

## 1. Problem Summary
### Reported Issue
Task creation fails when creating tasks with due dates in taskModel.js. Parameters are passed out of order.

- **Expected Behavior:** Expected correct functional behavior without errors.
- **Observed Behavior:** Application failure or data divergence observed.

## 2. Root-Cause Diagnosis
- **Most Likely Cause:** The parameters passed to the `insertWithDue.run` function are out of order, which leads to incorrect data being inserted into the database.
- **Diagnosis Status:** Likely root cause (Confidence: Medium)
- **Causal Explanation:**
In the `create` function, the parameters are passed in the order: `task.title`, `task.description`, `task.status`, `task.userId`, `task.dueDate`. However, the SQL statement expects the parameters in the order: `title`, `description`, `status`, `due_date`, `user_id`. This mismatch causes the database to receive incorrect values, leading to task creation failure.

## 3. Supporting Evidence
#### Causal / Defect Evidence
- **`taskflow/backend/src/models/taskModel.js`** (Line 3):
  ```
  const insertWithDue = db.prepare(
  'INSERT INTO tasks (title, description, status, due_date, user_id) VALUES (?, ?, ?, ?, ?)'
);
  ```
- **`taskflow/backend/src/models/taskModel.js`** (Line 6):
  ```
  const insertPlain = db.prepare(
  'INSERT INTO tasks (title, description, status, user_id) VALUES (?, ?, ?, ?)'
);
  ```
- **`taskflow/backend/src/models/taskModel.js`** (Line 9):
  ```
  const deleteOwned = db.prepare('DELETE FROM tasks WHERE id = ? AND user_id = ?');
  ```
- **`taskflow/backend/src/models/taskModel.js`** (Line 12):
  ```
  if (task.dueDate) {
  ```
- **`taskflow/backend/src/models/taskModel.js`** (Line 13):
  ```
  return insertWithDue.run(task.title, task.description, task.status, task.userId, task.dueDate);
  ```
- **`taskflow/backend/src/models/taskModel.js`** (Line 15):
  ```
  return insertPlain.run(task.title, task.description, task.status, task.userId);
  ```
- **`taskflow/backend/src/models/taskModel.js`** (Line 19):
  ```
  let sql = 'SELECT * FROM tasks WHERE user_id = ?';
  ```
- **`taskflow/backend/src/models/taskModel.js`** (Line 27):
  ```
  sql += ' ORDER BY id ASC';
  ```
- **`taskflow/backend/src/routes/taskRoutes.js`** (Line 11):
  ```
  res.json({ tasks: taskService.listTasks(req.user.id, req.query.q) });
  ```
- **`taskflow/backend/src/routes/taskRoutes.js`** (Line 14):
  ```
  router.post('/', validateTask, (req, res, next) => {
  try {
    res.status(201).json({ task: taskService.createTask(req.user.id, req.body) });
  } catch (err) {
    next(err);
  }
});
  ```
- **`taskflow/backend/src/routes/taskRoutes.js`** (Line 24):
  ```
  taskService.deleteTask(req.user.id, Number(req.params.id));
  ```
- **`taskflow/backend/src/services/taskService.js`** (Line 5):
  ```
  function normalizeStatus(status) {
  ```
- **`taskflow/backend/src/services/taskService.js`** (Line 10):
  ```
  return taskModel.findForUser(userId, term).map(toTask);
  ```
- **`taskflow/backend/src/services/taskService.js`** (Line 22):
  ```
  const info = taskModel.create(record);
  ```
- **`taskflow/backend/src/services/taskService.js`** (Line 24):
  ```
  return toTask({
    id: info.lastInsertRowid,
    title: record.title,
    description: record.description,
    status: record.status,
    due_date: record.dueDate,
    created_at: new Date().toISOString(),
  });
  ```
- **`taskflow/backend/src/services/taskService.js`** (Line 35):
  ```
  const info = taskModel.removeOwned(taskId, userId);
  ```
- **`taskflow/backend/src/services/taskService.js`** (Line 37):
  ```
  throw new HttpError(404, 'Task not found');
  ```
- **`taskflow/backend/src/models/userModel.js`** (Line 13):
  ```
  ORDER BY name LIMIT 20`
  ```
- **`taskflow/frontend/src/App.jsx`** (Line 19):
  ```
  path="/tasks"
  ```
- **`taskflow/frontend/src/App.jsx`** (Line 20):
  ```
  element={<ProtectedRoute><TasksPage /></ProtectedRoute>}
  ```
- **`taskflow/frontend/src/App.jsx`** (Line 30):
  ```
  <Route path="*" element={<Navigate to="/tasks" replace />} />
  ```
#### Contextual Observations
- `taskflow/backend/src/models/taskModel.js` (Line 11): inspected during investigation
- `taskflow/backend/src/models/taskModel.js` (Line 18): inspected during investigation
- `taskflow/backend/src/models/taskModel.js` (Line 31): inspected during investigation
- `taskflow/backend/src/routes/taskRoutes.js` (Line 3): inspected during investigation
- `taskflow/backend/src/routes/taskRoutes.js` (Line 4): inspected during investigation

- **Contradictory Evidence:** No contradictory evidence was found; all evidence supports the diagnosis of parameter order mismatch.

## 4. Affected Components
| Component | File Path | Failure Role |
|:---|:---|:---|
| **taskModel.js** | `taskflow/backend/src/models/taskModel.js` | defect site |
| **taskRoutes.js** | `taskflow/backend/src/routes/taskRoutes.js` | caller |
| **taskService.js** | `taskflow/backend/src/services/taskService.js` | consumer |

## 5. Suggested Fix
- **Target Location:** `taskflow/backend/src/models/taskModel.js` (create)
- **Recommended Change:** Change the order of parameters passed to `insertWithDue.run` to match the expected order in the SQL statement.
- **Correction Rationale:** This change aligns the parameters with the expected order in the SQL statement, resolving the root cause of the task creation failure.
- **Contract Invariants:** The function must still accept the same `task` object structure and maintain existing functionality for tasks without due dates.
- **Potential Regressions to Check:**
  - Ensure that task creation without a due date still functions correctly.
  - Verify that all existing tests for task creation pass after the change.

```
// Before
return insertWithDue.run(task.title, task.description, task.status, task.userId, task.dueDate);

// After
return insertWithDue.run(task.title, task.description, task.status, task.dueDate, task.userId);
```

> **Scope Restriction:** Fix recommendation only. The target application's source code was not modified.

## 6. Verification Steps
- **Verification Status:** `verification blocked`
- **Execution Blockers:** Node dependencies not installed in `taskflow` (node_modules missing).; No automated test script configured in `taskflow\package.json` (`scripts.test` is missing or unconfigured).; Node dependencies not installed in `taskflow\backend` (node_modules missing).; No automated test script configured in `taskflow\backend\package.json` (`scripts.test` is missing or unconfigured).; Node dependencies not installed in `taskflow\frontend` (node_modules missing).; No automated test script configured in `taskflow\frontend\package.json` (`scripts.test` is missing or unconfigured).
#### Proposed Actionable Checks
- **Regression Test for Argument / Column Ordering:** Create a unit or integration test calling `taskflow/backend/src/models/taskModel.js` with distinct, recognizable sentinel values (e.g., userId=9999 and dueDate='2026-12-31'). Inspect the persisted record or query binding.
  - *Expected Outcome:* Database record stores `due_date='2026-12-31'` and `user_id=9999` in their respective columns without foreign key rejection or column data corruption.
  - *Edge Cases:* Task creation with null or omitted due date.; Task creation with ISO-8601 formatted due date strings.; User ID types (numeric integer vs string representations).
- **Persistence Round-Trip Verification:** Execute an end-to-end task creation request through the API route and retrieve the created task via GET.
  - *Expected Outcome:* The retrieved task payload contains the matching `due_date` and belongs strictly to the authenticated `user_id`.
  - *Edge Cases:* Multiple concurrent tasks created with and without due dates.

## 7. Remaining Uncertainty
Are there any validation checks on the `task` object before it is passed to the `create` function? What happens if `task.dueDate` is not a valid date format? Is there any error handling in place for the database operations that could provide more insight into the failure?


- **Next Steps to Resolve:** Execute proposed verification checks in an isolated runtime environment with debug logging enabled.
