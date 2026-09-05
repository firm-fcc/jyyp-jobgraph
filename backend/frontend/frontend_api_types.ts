/** Wire contracts from this release's Python serializers. No scoring logic. */
export type ProficiencyLevel = "P1" | "P2" | "P3" | "P4" | "U";
export type KnownLevel = Exclude<ProficiencyLevel, "U">;
export type GapType = "SATISFIED" | "LEVEL_GAP" | "EVIDENCE_INSUFFICIENT" | "MISSING";
export type Decision = "MATCH" | "NO_MATCH" | "NOT_CALIBRATED";
export type MatchDecision = Decision;
export type PathMode = "LEARN" | "DEEPEN" | "VERIFY_FIRST" | "NONE";
/** Per skill; not the top-level aggregate status. */
export type LearningPathStatus = "READY" | "GRAPH_UNAVAILABLE" | "NO_ACTION";
export type LearningPathAggregateStatus = "READY" | "PARTIAL_GRAPH_COVERAGE" | "NO_ACTION";
export type SkillStatus = "supported" | "partially_supported" | "unsupported";
export type Diagnostics = Record<string, unknown>; // intentionally extensible backend diagnostics

export interface HealthResponse {
  status: "ok";
  service: "challenge26-backend-handoff";
  candidate_runtime: "r4.3.4";
  target_job_schema: "target_job_profile_v1.1";
  matching_schema: "match_result_v1";
  matching_calibrated: boolean;
  llm_configured: boolean;
  window: "2022-10";
  limitations: {
    matching_decision: "CALIBRATED" | "NOT_CALIBRATED";
    learning_path_curated_graph_count: number;
    job_window: "2022-10";
    matching_threshold_invalid: boolean;
  };
}
export interface JobsResponse {
  schema_version: "job_catalog_response_v1";
  query: string;
  limit: number;
  items: Array<{
    jd_key: string; jobid: string; title: string; std_job: string;
    level: string; techstack: string; opentime: string; n_skills: number;
  }>;
}
export interface CandidateEvidence {
  text: string; source_experience_id: string;
  start: number | null; end: number | null;
  fact: string; behavior: string; context: string; result: string;
}
export interface CandidateSkillAssessment {
  candidate_id: string; team_skill_id: string; team_skill_name: string;
  status: SkillStatus; inference_mode: "direct_behavior" | "aggregate_signal";
  evidence: CandidateEvidence[]; reason: string; confidence: number | null;
  atomic_abilities: string[]; audit_flags: string[];
}
export interface CandidateSkillProfile {
  candidate_id: string; skill_registry_version: string;
  assessments: CandidateSkillAssessment[]; metadata: Diagnostics;
}
export interface CandidateSourceSegment {
  source_experience_id: string; section_type: string;
  start: number; end: number; text: string;
}
export interface GroundedCapabilityCandidate {
  source_candidate_ability_id: string;
  extracted_capability_hint: string; fact_hint: string; behavior_hint: string;
  hint_authority: "non_authoritative_llm_annotation";
  extraction_confidence: number; evidence_type: string;
  grounded_evidence: Array<{text: string; start: number; end: number; source_experience_id: string}>;
  non_unsupported_team_skill_outcomes: Array<{
    team_skill_id: string; status: "supported" | "partially_supported"; confidence: number | null;
  }>;
  metric_role: "diagnostic_only_not_team_skill_prediction";
}
export interface ExplicitSkillMention {
  text: string; start: number; end: number; source: "explicit_skill_section";
  mention_type: "explicit_technical_skill" | "explicit_self_claim" | "explicit_unclassified_claim";
  mapping_status: "frozen_display_only_no_team_skill_mapping" | "frozen_display_only_no_team_skill_support";
}
export interface CandidateResponse {
  schema_version: "candidate_api_response_v1_1";
  candidate_id: string; candidate_skill_profile: CandidateSkillProfile;
  explicit_skill_mentions: ExplicitSkillMention[];
  diagnostics: Diagnostics; grounded_capability_candidates: GroundedCapabilityCandidate[];
  resume_text: string; source_segments: CandidateSourceSegment[];
  experience_metadata_available: boolean; runtime_schema: string | null;
  proficiency_status: string;
}
export interface CandidatePreflightResponse {
  schema_version: "candidate_preflight_v1"; parser: string;
  quality: {
    passed: boolean; fallback_required: boolean; flags: string[];
    char_count: number; nonempty_line_count: number; readable_char_ratio: number;
    page_count: number; empty_page_count: number; empty_page_ratio: number;
  };
  team_skill_registry_version: string; team_skill_count: number;
}
export interface TaxonomyCompatibility {
  status: "PASS"; identity_rule: "team_skill_id"; semantic_fields_checked: string[];
  display_name_difference_count: number;
  display_name_differences: Array<{team_skill_id: string; canonical_name: string; provider_name: string}>;
}
export type RequirementStatus = "EXPLICIT_LEVEL" | "LEVEL_UNSPECIFIED" | "PROFICIENCY_NOT_AVAILABLE" | "AUXILIARY_NOT_GRADED";
export interface TargetJobSkill {
  team_skill_id: string; team_skill_name: string; provider_skill_name: string;
  skill_type: string; is_primary: boolean; requirement_present: true;
  required_level_raw: ProficiencyLevel | null; required_level: KnownLevel | null;
  requirement_status: RequirementStatus;
  learning_path_target_eligible: boolean; level_comparison_eligible: boolean;
  requirement_evidence_kind: "STRUCTURED_JD_SUMMARY_PROVENANCE";
  requirement_evidence_ref: string;
  market_signal: null | {
    graph_layer: string | null; is_probability: false; origin: string;
    base_weight?: number | null; delta_weight?: number | null; effective_weight?: number | null;
    gap?: number | null; weight?: number | null; lambda?: number | null;
  };
}
export interface TargetJobResponse {
  schema_version: "target_job_profile_v1.1"; source_type: "single_jd"; window: string | null;
  job: {
    job_code: string; job_name: string; jd_key: string | null; jobid: string | null;
    title: string | null; std_job: string; opentime: string | null;
    level: string | null; level_source: string | null; techstack: string | null;
  };
  taxonomy: {
    provider_version: string | null; canonical_version: string | null;
    provider_taxonomy_sha256: string; canonical_taxonomy_sha256: string;
    taxonomy_compatibility: TaxonomyCompatibility; identity_rule: "team_skill_id";
  };
  source_provenance: {
    jd_summary_sha256: string; jobs_sha256: string; job_skill_sha256: string | null;
    graph_layer: string | null; raw_jd_evidence_available: false;
  };
  semantics: {
    jd_U: "LEVEL_UNSPECIFIED"; jd_U_is_P1: false; market_weight_is_probability: false;
    market_weight_role: "advisory_only_not_ranked_in_v1.1";
  };
  skills: TargetJobSkill[];
  warnings: Array<{code: string; team_skill_id: string; message: string}>;
}
export interface AggregatedJobSkillSummary {
  team_skill_id: string; team_skill_name: string; skill_type: string; is_primary: boolean;
  jd_presence_count: number; jd_presence_rate: number;
  level_distribution: Record<ProficiencyLevel | "NOT_AVAILABLE", number>;
  market_signal: {
    graph_layer: "effective"; is_probability: false;
    base_weight: number | null; delta_weight: number | null; effective_weight: number | null;
  };
}
export interface JobSummaryResponse {
  schema_version: "aggregated_job_summary_v1"; source_type: "aggregated_job_summary"; window: "2022-10";
  job: {job_code: string; job_name: string; jd_count: number}; skills: AggregatedJobSkillSummary[];
  taxonomy: {provider_version: string | null; canonical_version: string | null; identity_rule: "team_skill_id"; taxonomy_compatibility: TaxonomyCompatibility};
  provenance: {
    aggregation: "deterministic_full_window_statistics_no_threshold";
    jd_filter: {field: "std_job"; value: string};
    jd_summary_sha256: string; jobs_sha256: string; job_skill_sha256: string;
    provider_taxonomy_sha256: string; canonical_taxonomy_sha256: string; raw_jd_evidence_available: false;
  };
  semantics: {
    matching_input: false; matching_decision_available: false; required_level_synthesized: false;
    jd_U: "LEVEL_UNSPECIFIED"; jd_U_is_P1: false; market_weight_is_probability: false;
  };
}
export interface MatchingEvidence {
  evidence_ref: string; text: string; source_id: string; start: number | null; end: number | null;
}
export interface MatchSkillItem {
  team_skill_id: string; team_skill_name: string;
  required_level: KnownLevel | null; candidate_level: ProficiencyLevel | null;
  gap_type: GapType; path_mode: PathMode; requirement_type: string;
  requirement_evidence: string[]; candidate_evidence: MatchingEvidence[]; explanation: string;
}
export interface MatchResult {
  schema_version: "match_result_v1"; candidate_id: string; job_id: string; job_title: string;
  match_score: number; decision: Decision; decision_threshold: number | null;
  summary: {required_skills: number; satisfied: number; level_gap: number; evidence_insufficient: number; missing: number};
  metrics: {verified_fit: number; skill_coverage: number; level_gap_rate: number; uncertainty_rate: number; missing_rate: number};
  skills: MatchSkillItem[];
  semantics: {
    score: "verified_satisfied_job_requirements / eligible_job_requirements";
    score_direction: "job_requirement_coverage_asymmetric"; extra_candidate_skills_penalized: false;
    cosine_used: false; jd_U: "LEVEL_UNSPECIFIED"; jd_U_is_P1: false;
    candidate_U: "EVIDENCE_INSUFFICIENT_FOR_LEVEL_COMPARISON"; partially_supported_counts_as_supported: false;
  };
}
export interface ProficiencyResult {
  ability_id: string; ability_name: string; evidence_sufficiency: "sufficient" | "partial" | "insufficient";
  dimensions: Record<"D1" | "D2" | "D3" | "D4", {level: ProficiencyLevel; reason: string}>;
  final_level: ProficiencyLevel; reason: string; uncertainty: string[]; review_required: boolean;
  validator_flags: string[]; rubric_version: string; model: string; prompt_sha256: string;
}
export interface ProficiencyBundle {
  source: "provided" | "auto_on_demand" | "not_run";
  levels: Record<string, ProficiencyLevel>; details: ProficiencyResult[];
}
/** Provide target_job_profile OR exactly one of job_id / jd_key; omit the other selectors. */
export interface MatchRequest {
  candidate_profile: CandidateSkillProfile;
  target_job_profile?: TargetJobResponse | null; job_id?: string | null; jd_key?: string | null;
  proficiency_levels?: Record<string, ProficiencyLevel> | null; auto_proficiency?: boolean;
}
export type LearningPathRequest = MatchRequest;
export interface MatchResponse {
  schema_version: "matching_pipeline_output_v1"; match_result: MatchResult;
  diagnostics: {candidate_bridge: Diagnostics; target_bridge: Diagnostics};
  target_job_profile: TargetJobResponse; proficiency: ProficiencyBundle;
}
export interface RenderedLearningStep {
  node_id: string; node_name: string; reason: string;
  evidence_task: string | null; validation_criteria: string[];
}
export interface SpecializationExtension {subskill_id: string; task_description: string; validation_criteria: string[]}
export interface VerificationGuidance {
  task_id: string; task_name: string; task_description: string | null;
  validation_criteria: string[]; source_references: string[];
}
export interface CapstoneGuidance {
  task_id: string; objective: string; task_description: string;
  specialization_extensions: SpecializationExtension[]; validation_criteria: string[]; purpose: string;
}
export interface RenderedSkillPath {
  team_skill_id: string; team_skill_name: string; gap_type: GapType;
  observed_level: ProficiencyLevel | null; required_level: KnownLevel | null;
  path_mode: PathMode; achieved_node_ids: string[]; current_state: string;
  gap_explanation: string; development_goal: string;
  learning_steps: RenderedLearningStep[]; specialization_extensions: SpecializationExtension[];
  verification_guidance: VerificationGuidance | null; capstone_guidance: CapstoneGuidance | null;
  reassessment_required: boolean; reassessment_guidance: string | null;
  path_status: LearningPathStatus; render_status: "READY";
}
export interface LearningPathResponse {
  schema_version: "learning_path_api_response_v1"; path_status: LearningPathAggregateStatus;
  gap_summary: {total_requirements: number; MISSING: number; LEVEL_GAP: number; EVIDENCE_INSUFFICIENT: number; SATISFIED: number};
  rendered: {candidate_id: string; target_job_id: string; skill_paths: RenderedSkillPath[]; render_status: "READY"};
  proficiency: ProficiencyBundle;
  diagnostics: {candidate_bridge: Diagnostics; target_bridge: Diagnostics; curated_graph_count: number};
}
export type ApiErrorBody = {detail: string | Array<{loc: Array<string | number>; msg: string; type: string; input?: unknown; ctx?: Diagnostics}>};
// Backwards-compatible import names from the source package.
export type CandidateApiResponseV11 = CandidateResponse;
export type AggregatedJobSummaryV1 = JobSummaryResponse;
export type MatchingApiResponse = MatchResponse;
