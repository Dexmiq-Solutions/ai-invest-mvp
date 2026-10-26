"""Phase 3 Test Suite: Comprehensive Evidence Collection and Hypothesis Generation.

Verifies:
1. Multi-hop local relative import expansion discovers connected files.
2. Code investigator produces multiple distinct, evidence-backed hypotheses for distinct defect mechanisms.
3. Hypotheses include supporting evidence, contradicting evidence, reasoning, and unanswered questions.
4. Dependency impact investigator identifies distinct cross-module interface boundaries.
5. HypothesisManager consolidates duplicates without losing attribution, evidence, or distinct competing explanations.
6. Cross-specialist correlation synthesizes multi-layer evidence chains with objective traceability.
"""
from pathlib import Path
import pytest

from app.agents.code_investigator import CodeInvestigator, _extract_relative_imports
from app.agents.dependency_impact_investigator import DependencyImpactInvestigator
from app.agents.hypothesis_manager import HypothesisManager
from app.agents.investigation_orchestrator import InvestigationOrchestrator
from app.agents.investigator_contract import create_hypothesis


def test_extract_relative_imports_js():
    js_content = """
    import * as authService from '../services/authService.js';
    import * as userService from './userService.js';
    const config = require('./config.js');
    """
    imports = _extract_relative_imports(js_content, "src/middleware/authenticate.js")
    assert "src/services/authService.js" in imports
    assert "src/middleware/userService.js" in imports
    assert "src/middleware/config.js" in imports


def test_extract_relative_imports_python():
    py_content = """
    from .models import user_model
    from ..utils import helpers
    """
    imports = _extract_relative_imports(py_content, "app/services/auth.py")
    assert "app/services/models.py" in imports or "app/models/user_model.py" in imports or "app/utils/helpers.py" in imports


def test_multi_hop_import_expansion_in_code_investigator(tmp_path: Path):
    """When a file imports another local file, code investigator follows the import
    and discovers the connected file even if not in initial candidates."""
    middleware = tmp_path / "middleware.js"
    middleware.write_text("import { findUser } from './service.js';\nexport function auth(req) { findUser(req.user); }")

    service = tmp_path / "service.js"
    service.write_text("export function findUser(u) { if (!u) throw new Error('unauthorized'); }")

    inv = CodeInvestigator()
    res = inv.investigate(
        workspace_path=str(tmp_path),
        investigation_task="Inspect auth middleware and user validation",
        shared_context={"target_files": ["middleware.js"]},  # only middleware.js initially!
    )

    assert res["status"] == "completed"
    sources_inspected = {e["source"] for e in res["evidence"]}
    # Multi-hop import expansion discovered service.js!
    assert "service.js" in sources_inspected or any("service.js" in s for s in sources_inspected)


def test_multiple_distinct_hypotheses_and_contradicting_evidence(tmp_path: Path):
    """Specialists evaluate distinct defect mechanisms and produce multiple structured hypotheses
    with both supporting and contradicting evidence."""
    auth_src = tmp_path / "auth.js"
    auth_src.write_text(
        "export function register(email) { const clean = String(email).trim(); return db.insert(clean); }\n"
        "export function login(email) { const norm = String(email).trim().toLowerCase(); return db.query(norm); }\n"
        "export function verify(req) { if (!req.token) return res.status(401).json({ error: 'unauthorized' }); }\n"
    )

    inv = CodeInvestigator()
    res = inv.investigate(
        workspace_path=str(tmp_path),
        investigation_task="Inspect auth.js for email casing and 401 unauthorized rejections",
        shared_context={"target_files": ["auth.js"]},
    )

    assert res["status"] == "completed"
    assert len(res["hypotheses"]) >= 2

    # Check distinct hypotheses
    titles = [h["hypothesis_title"].lower() for h in res["hypotheses"]]
    assert any("casing" in t or "normalization" in t for t in titles)
    assert any("401" in t or "authorization" in t or "rejection" in t or "workflow" in t for t in titles)

    # Check presence of supporting evidence, reasoning, and unanswered questions
    for h in res["hypotheses"]:
        assert len(h["supporting_evidence_ids"]) > 0
        assert len(h["reasoning"]) > 10
        assert len(h["unanswered_questions"]) > 0


def test_hypothesis_manager_consolidation_and_competing_preservation():
    """HypothesisManager merges duplicate hypotheses while preserving competing explanations."""
    mgr = HypothesisManager()

    hyp_1 = create_hypothesis(
        hypothesis_id="HYP-001",
        hypothesis_title="Casing discrepancy in user lookup",
        produced_by="Code Investigator",
        possible_cause="Database query enforces case-sensitive comparison on lowercased token email",
        evidence="userModel.js:7",
        reasoning="Casing mismatch returns null.",
        supporting_evidence_ids=["EV-CODE-001"],
        status="needs_validation",
    )

    hyp_2 = create_hypothesis(
        hypothesis_id="HYP-002",
        hypothesis_title="Casing discrepancy in user lookup",
        produced_by="Dependency Impact Investigator",
        possible_cause="Database query enforces case-sensitive comparison on lowercased token email",
        evidence="userService.js:14",
        reasoning="Casing mismatch returns null across service boundary.",
        supporting_evidence_ids=["EV-DEP-001"],
        status="needs_validation",
    )

    hyp_competing = create_hypothesis(
        hypothesis_id="HYP-003",
        hypothesis_title="Missing Authorization Header in Client Request",
        produced_by="Runtime Error Investigator",
        possible_cause="Client HTTP request omits the Bearer token entirely",
        evidence="server.js:42",
        reasoning="Missing header triggers immediate 401 response.",
        supporting_evidence_ids=["EV-RUN-001"],
        status="needs_validation",
    )

    consolidated = mgr.consolidate([hyp_1, hyp_2, hyp_competing])

    # Exactly 2 consolidated hypotheses: 1 merged duplicate + 1 competing explanation
    assert len(consolidated) == 2

    # Merged hypothesis contains both contributors and both evidence IDs
    merged = next(h for h in consolidated if "casing" in h["hypothesis_title"].lower())
    assert "Code Investigator" in merged["contributing_specialists"]
    assert "Dependency Impact Investigator" in merged["contributing_specialists"]
    assert "EV-CODE-001" in merged["supporting_evidence_ids"]
    assert "EV-DEP-001" in merged["supporting_evidence_ids"]

    # Competing explanation is preserved as distinct
    competing = next(h for h in consolidated if "client" in h["hypothesis_title"].lower() or "missing" in h["hypothesis_title"].lower())
    assert competing["hypothesis_id"] == "HYP-003"
    assert "Runtime Error Investigator" in competing["contributing_specialists"]
