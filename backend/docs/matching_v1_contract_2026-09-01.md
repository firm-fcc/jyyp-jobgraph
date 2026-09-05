# Matching v1 Contract（2026-09-01 冻结设计）

## 1. Scope

Matching v1 is a deterministic, explainable Candidate × Job requirement matcher.
It does not modify the frozen Candidate r4.3.4 pipeline, does not change the
TargetJobProfile v1.1 semantics, and does not use blind labels for tuning.

## 2. Identity and eligible skills

- Cross-module identity: `team_skill_id` only.
- Candidate side: only `status == supported` primary Team Skills enter matching.
- `partially_supported` does **not** count as possessed skill.
- Auxiliary Team Skills are excluded from graded matching.
- Job side: only `EXPLICIT_LEVEL` and `LEVEL_UNSPECIFIED` primary requirements are eligible.
- `PROFICIENCY_NOT_AVAILABLE` and `AUXILIARY_NOT_GRADED` are excluded.

## 3. U semantics

- Candidate `U`: ability exists but evidence is insufficient to reliably distinguish P1-P4.
- JD `U`: `LEVEL_UNSPECIFIED`; it is not P1.
- Candidate U against an explicit P1-P4 requirement => `EVIDENCE_INSUFFICIENT`.
- A supported Candidate skill against JD `LEVEL_UNSPECIFIED` => `SATISFIED` without level comparison.

## 4. Per-skill gap states

Matching reuses the existing deterministic `GapEngine`:

- `SATISFIED`
- `LEVEL_GAP`
- `EVIDENCE_INSUFFICIENT`
- `MISSING`

## 5. Score

For `N` eligible Job requirements:

`verified_fit = SATISFIED / N`

`match_score = 100 × verified_fit`

Diagnostics:

- `skill_coverage = (N - MISSING) / N`
- `level_gap_rate = LEVEL_GAP / N`
- `uncertainty_rate = EVIDENCE_INSUFFICIENT / N`
- `missing_rate = MISSING / N`

The score is asymmetric. Extra Candidate skills never reduce Job fit. Cosine
similarity and learned/hand-tuned weighted sums are not used in Matching v1.

## 6. Decision threshold

Release note (2026-09-03): the pre-declared Dev calibration is now frozen at `0.380952`. See `config/matching_threshold_v1.json`. The historical pre-calibration rationale below remains unchanged; this is not a formal Blind accuracy claim.

The threshold is intentionally **not defined during implementation**.

- Before calibration: `decision = NOT_CALIBRATED`.
- A threshold may be selected only on the pre-declared development set.
- After selection, the threshold is frozen before the independent blind test.
- Blind test results must not be used to revise the threshold or matching logic.

## 7. Accuracy protocol (next stage)

- Dev set: approximately 20 Candidate × JD pairs, human Gold labels `MATCH/NO_MATCH`.
- Gold annotation must be performed from source Resume + JD without model prediction visibility.
- Freeze threshold after Dev.
- Blind set: at least 100 independent Candidate × JD test cases, with balanced or explicitly reported class distribution.
- Primary metric: Accuracy.
- Also report Precision, Recall, F1, and confusion matrix.

## 8. Implementation files

New files only; no frozen existing file is modified:

- `extractor/candidate_matching_bridge_v1.py`
- `extractor/matching_engine_v1.py`
- `extractor/matching_pipeline_v1.py`
- `tests/test_candidate_matching_bridge_v1.py`
- `tests/test_matching_engine_v1.py`
- `tests/test_matching_pipeline_v1.py`

## 9. Frontend-facing result

`MatchingPipelineV1.run(...).to_dict()["match_result"]` returns
`schema_version = match_result_v1`, including:

- candidate/job identity
- match score
- calibrated decision or `NOT_CALIBRATED`
- summary counts
- diagnostic rates
- per-skill required level / candidate level / gap type
- Candidate grounded evidence and JD structured provenance

The frontend should consume this contract rather than recomputing matching.
