# v3 Harness Controls

## 1. Data Access Control Matrix

Default rule: deny unless explicitly allowed. PII, financial data, HR data,
medical records, and domain-external private data are denied for every layer and
every action.

| Data category | Layer 1 Production | Layer 2 Reviewer/Harness | Layer 3 Meta-Harness |
| --- | --- | --- | --- |
| Patent domain input | Read sanitized only | Read sanitized only for evaluation | Read only through sanitized traces, not source intake |
| Golden set labels | Deny | Read/write through curator | Deny direct access |
| Human labels/audit records | Deny | Read/write for curation and review | Deny direct access |
| Model outputs | Write | Read | Read |
| Raw execution traces | Write sanitized raw traces | Write sanitized evaluation traces | Read |
| Protected metrics definitions | Deny | Read | Deny write; direct read only through external gate summaries |
| Build logs | Deny | Read for CI only | Read/write proposal and Triad artifacts |
| Production code | Execute only | Deny write | Read/propose mutable areas |
| Harness code | Deny | Execute only | Read/propose non-evaluation helpers |
| Sanitizer code | Deny write | Deny write | Deny read/write proposal scope changes |
| Deployment gate code | Deny write | Execute only | Deny read/write proposal scope changes |
| Hidden audit/test set | Deny | Human/external gate only | Deny existence and contents |
| PII/financial/HR/medical | Deny | Deny | Deny |

Violation behavior: fail closed, write an audit event without storing the
restricted payload, and stop the current operation.

## 2. Reward-Hacking Scenarios

1. Evaluation code modification: Layer 3 proposes changes to recall, ECE, or
   critical miss calculation.
   Guardrail: `harness/evaluator`, protected metric definitions, and
   `harness/deployment_gate.py` are immutable to Layer 3.

2. Golden label modification: Layer 3 attempts to edit labels, labeler IDs, or
   audit logs.
   Guardrail: direct golden set access is denied to Layer 3; labels are append
   only through Layer 2 curator workflows.

3. Input distribution manipulation: a proposal avoids hard patent cases or
   changes which records are evaluated.
   Guardrail: search set selection and evaluation execution belong to the
   external orchestrator, not the proposer.

4. Confidence gaming for ECE: a proposal narrows confidence values or always
   emits calibrated-looking probabilities.
   Guardrail: deployment gate checks ECE together with recall, critical misses,
   category recall, behavioral diffs, and human review.

5. Rubric mimicry: a proposal learns words that satisfy the reasoning quality
   rubric without better legal reasoning.
   Guardrail: protected rubric, raw trace review, failure clustering, and human
   sanity checks for large or semantically risky changes.

## 3. Phase 3 Entry Checklist

- Golden set has at least 500 reviewed cases with balanced categories.
- Separate hidden audit set exists outside Layer 3 visibility.
- Six months of human reviewer trust data is available.
- Filesystem mounts enforce immutable golden set, metrics, sanitizer, evaluator,
  gate, and hidden test paths.
- Deployment gate code has been reviewed by a human owner.
- Kill switch and rollback to the last human-approved version are tested.
- Skill file has named human reviewers and version history.
- Layer 3 runs in proposal-only mode initially.
- Every Layer 3 plan goes through human review during the introduction period.
- Audit logs are append-only and reviewed regularly.

## 4. Phase 1 MVP

Phase 1 should ship only the minimum safety loop:

- Dataset Curator: append sanitized golden records and human rationales.
- Eval Runner: run production outputs against golden records and compute
  recall-first metrics.
- Reviewer UI or review queue placeholder: ensure human escalation remains the
  immediate safety net.

Layer 3 stays inactive. The repository may keep a proposal-only skeleton so its
future workspace, skill file, and immutable boundaries are prepared without
allowing automatic deployment.

## 5. Triad Exemptions

Allowed exemptions are limited to:

- Typo-only documentation changes.
- Formatting-only changes with no behavior impact.
- Test expectation text updates that do not change assertions.
- Dependency metadata comments without runtime effect.

Every exemption must include an explicit exemption type and approver. Arbitrary
or silent exemptions are invalid.

## 6. CI Enforcement

For non-exempt build tasks, CI must verify:

- `build_log/<task_id>/incubation.json` exists.
- `build_log/<task_id>/plan.json` exists.
- Incubation contains at least three genuinely distinct approaches and one
  non-obvious option.
- Plan has one atomic change, explicit untouched areas, validation, rollback,
  and risk assessment.
- Changed files are inside plan scope.
- Exemptions require a registered label and approver.

## 7. Incubator Anti-Patterns

Reject incubation documents when:

- The three approaches are the same design with renamed wording.
- A non-obvious option is missing.
- The recommendation ignores Rule 0, immutable boundaries, or layer separation.
- The document includes code, pseudocode, or concrete function bodies.
- Rejected options are absent, making later audit impossible.

## 8. Planner Atomicity Checks

Reject or split plans when:

- One plan changes multiple unrelated components.
- A metric change and production behavior change are bundled.
- A sanitizer change and Layer 3 proposal change are bundled.
- The rollback plan cannot revert the change independently.
- The predicted behavior does not include concrete example inputs and outputs.
