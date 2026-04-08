"""Multi-agent grading system.

Each agent has one job. They work as a pipeline:
  Orchestrator → Domain Judge → Evidence Verifier → Scorer → Critic

The system is universal — works for any subject, any assessment type.
"""
from app.services.agents.orchestrator import GradingOrchestrator

__all__ = ["GradingOrchestrator"]
