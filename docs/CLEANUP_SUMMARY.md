# AIM-EVM Codebase Cleanup Summary

## Files Deleted (9 files + 1 directory)

### src/experiment/
- ✓ model_config.py (550 bytes) - Superseded by JSON configs
- ✓ validate_decision_algorithm.py (36KB) - Broken imports, unused
- ✓ run_fault_experiments.py (18KB) - Superseded by run_batch.py
- ✓ experiment_args/ directory - Duplicate of root experiment_args/

### src/judge/ (entire directory)
- ✓ __init__.py
- ✓ judge_prompt.py (857 bytes)
- ✓ llm_judge.py (1.5KB)
- ✓ scoring.py (1.4KB)
Total: ~4KB of abandoned LLM judging feature

### src/monitoring/
- ✓ metrics_sanitizer.py (991 bytes) - Never imported

### src/utils/
- ✓ experience_dump.py (4.9KB) - Never imported
- ✓ embedding_generator.py (2.3KB) - Never imported

### src/planner/
- ✓ remediation_decision_algorithm.py (37KB) - Research code, not in current pipeline
- ✓ llm_prompt_with_decision_algorithm.py (28KB) - Alternative prompt, unused

## Total Cleanup: 9 files removed (37 → 28 Python files)

## What Remains (Active Codebase)

### Core Modules
- src/clients/ - LLM clients (GPT, Gemini, Claude)
- src/executor/ - Ansible generation and execution
- src/experiment/ - run_experiment.py (core experiment logic)
- src/fault_injection/ - Fault injection framework
- src/feedback/ - Feedback computation and knowledge store
- src/monitoring/ - Prometheus metrics collection
- src/planner/ - LLM-based planning with retrieval
- src/utils/ - normalize_metric.py, ansi_parser.py

### Entry Points
- src/run_batch.py - Main experiment runner

All remaining files are actively used in the current remediation pipeline.
