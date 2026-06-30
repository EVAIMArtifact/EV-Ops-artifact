# EV-AIM

This folder contains the EV-AIM implementation for the EV-Ops project.
EV-AIM is the execution-validated adaptive incident mitigation framework that injects faults, collects observability data, generates remediation plans, executes recovery, and validates outcomes.

## Contents

- `src/` - application source code for EV-AIM
  - `src/experiment/` - core experiment runners and models
  - `src/fault_injection/` - fault injection and controlled failure handling
  - `src/feedback/` - feedback generation and knowledge store
  - `src/monitoring/` - metrics collection and Kubernetes observation
  - `src/executor/` - remediation execution and rollout monitoring
  - `src/planner/` - planner logic and decision-making components
  - `src/clients/` - LLM client integration
  - `src/utils/` - shared helpers and utilities
  - `src/config.py` - configuration and environment settings
  - `src/run_batch.py` - batch experiment runner entrypoint

## Usage

From the `EV-AIM` folder, run batch experiments using the Python module:

```bash
cd EV-AIM
python -m src.run_batch --file path/to/experiment.json --results-dir path/to/results
```

Common options:

- `--file` - required, path to the experiment JSON file
- `--mode` - `evaim` for LLM-based mitigation or `rule_based` for the deterministic baseline
- `--results-dir` - directory where experiment results are written
- `--sleep-between` - seconds to wait between experiments

## Notes

- The EV-AIM implementation is organized around closed-loop validation: fault injection → observation → planning → execution → recovery validation → feedback.
- The top-level repository README contains broader project context, supported applications, and architecture details.

## Development

Use the `src/run_batch.py` entrypoint and inspect the `src/experiment` package for experiment workflows.

If you add new EV-AIM modules or experiments, keep the folder structure and README updated.
