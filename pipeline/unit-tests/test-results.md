# 单元测试运行结果（自动生成，勿手改）

- 生成时间：2026-09-05 18:45
- 命令：`python unit-tests/run_tests.py`（离线用例零 LLM / 零网络；test_llm_live 3 例为真调用冒烟，无密钥自动跳过）
- 结果：**178/178 通过**

逐用例状态与耗时（用例的输入/期望输出明细见 `TEST-CASES.md` 与 `test-cases.csv`）：

## test_annotate_levels_tech.py（9 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_parse_work_year` | 通过 | 0.01s |
| `test_parse_text_years` | 通过 | 0.00s |
| `test_years_to_level_thresholds` | 通过 | 0.00s |
| `test_resolve_level_priority` | 通过 | 0.00s |
| `test_extract_tech_mentions_boundaries` | 通过 | 14.10s |
| `test_extract_tech_mentions_version_and_suppression` | 通过 | 13.61s |
| `test_extract_tech_mentions_short_names` | 通过 | 7.17s |
| `test_stack_annotator_tiers` | 通过 | 0.14s |
| `test_stack_annotator_cache_tolerant_missing` | 通过 | 0.01s |

## test_base_builder.py（6 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_salary` | 通过 | 0.00s |
| `test_edges_math` | 通过 | 0.24s |
| `test_force_guard` | 通过 | 0.24s |
| `test_decay_chain` | 通过 | 0.42s |
| `test_merge_history_unit` | 通过 | 0.00s |
| `test_skill_prof` | 通过 | 0.20s |

## test_builder_infra.py（9 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_sampler_filter_and_dedup` | 通过 | 0.00s |
| `test_sampler_strategies_and_cap` | 通过 | 0.00s |
| `test_sampler_next_batch_consumption` | 通过 | 0.00s |
| `test_sampler_reproducible` | 通过 | 0.00s |
| `test_taxonomy_store_task_mode` | 通过 | 0.01s |
| `test_taxonomy_store_skill_mode_and_roundtrip` | 通过 | 0.01s |
| `test_supervise_type_normalization` | 通过 | 0.00s |
| `test_supervise_empty_updates` | 通过 | 0.01s |
| `test_apply_updates_full_actions` | 通过 | 0.01s |

## test_classify_cache.py（4 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_jobcls_cache_roundtrip` | 通过 | 0.04s |
| `test_merged_classification_with_llm_cache` | 通过 | 0.87s |
| `test_parse_array_tolerant` | 通过 | 0.00s |
| `test_load_progress_filters_unknown_keys` | 通过 | 0.01s |

## test_eval_metric.py（6 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_alloc_under_total_passthrough` | 通过 | 0.00s |
| `test_alloc_largest_remainder_with_floor` | 通过 | 0.00s |
| `test_norm_sp` | 通过 | 0.01s |
| `test_prf_sp_soft_pairing` | 通过 | 0.00s |
| `test_prf_exact_sets` | 通过 | 0.00s |
| `test_coverage_sp` | 通过 | 0.00s |

## test_extractor_classify.py（3 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_classify_units_dedupe_and_aggregate` | 通过 | 0.01s |
| `test_classify_units_cache_hit` | 通过 | 0.01s |
| `test_collect_misses_production_gate` | 通过 | 0.10s |

## test_extractor_text.py（8 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_split_sentences_boundaries_and_paren` | 通过 | 0.00s |
| `test_split_sentences_length_filter` | 通过 | 0.00s |
| `test_taxonomy_label_loading` | 通过 | 0.01s |
| `test_fit_name_degrades_not_drops` | 通过 | 0.00s |
| `test_validate_signal_rules` | 通过 | 0.00s |
| `test_mention_norm_and_lookup` | 通过 | 0.00s |
| `test_news_filter_settings_fallback` | 通过 | 0.08s |
| `test_news_filter_title_guide` | 通过 | 0.00s |

## test_jd_annotate_gate.py（9 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_matcher_ascii_boundary` | 通过 | 0.00s |
| `test_matcher_chinese_and_order` | 通过 | 0.00s |
| `test_rule_stacks_tiers` | 通过 | 0.00s |
| `test_jd_text_key_and_parts` | 通过 | 0.00s |
| `test_iter_rows_bom_and_embedded_newline` | 通过 | 0.01s |
| `test_collect_gate_and_dedup` | 通过 | 0.42s |
| `test_collect_presample_filter` | 通过 | 0.38s |
| `test_collect_strict_gate` | 通过 | 0.81s |
| `test_ambiguous_names_registered` | 通过 | 0.01s |

## test_jd_dedup.py（7 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_simhash_deterministic` | 通过 | 0.01s |
| `test_simhash_near_dup_sensitivity` | 通过 | 0.01s |
| `test_jaccard_confirm_reject` | 通过 | 0.00s |
| `test_cluster_keep_earliest` | 通过 | 0.01s |
| `test_blocks_pigeonhole` | 通过 | 0.01s |
| `test_load_variants_absent` | 通过 | 0.00s |
| `test_artifact_roundtrip` | 通过 | 0.00s |

## test_jd_delta_channel.py（7 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_build_extract_prompt_context` | 通过 | 0.00s |
| `test_fit_name_and_validate` | 通过 | 0.00s |
| `test_extract_jd_signals_batch` | 通过 | 0.00s |
| `test_extract_jd_signals_llm_failure` | 通过 | 0.00s |
| `test_map_mentions_exact_hit_no_llm` | 通过 | 0.00s |
| `test_map_mentions_llm_group_and_drop` | 通过 | 0.00s |
| `test_paper_mention_units_and_mode_guard` | 通过 | 0.00s |

## test_jd_delta_v2.py（13 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_doc_tokens` | 通过 | 0.00s |
| `test_chinese_apriori_mining` | 通过 | 0.00s |
| `test_trim_context` | 通过 | 0.00s |
| `test_pool_band_and_vocab_diff` | 通过 | 0.01s |
| `test_substring_reduction` | 通过 | 0.00s |
| `test_trim_resurrection_ceiling` | 通过 | 0.01s |
| `test_task_skill_novelty_gate` | 通过 | 0.05s |
| `test_collect_evidence` | 通过 | 0.00s |
| `test_window_end_date` | 通过 | 0.00s |
| `test_confirm_channel_removed` | 通过 | 0.00s |
| `test_load_overlay_items_born_window_gate` | 通过 | 0.00s |
| `test_adjudication_hotupdate_wiring` | 通过 | 0.01s |
| `test_assembly_fingerprint` | 通过 | 0.34s |

## test_jd_pre_sample.py（3 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_select_keys` | 通过 | 0.00s |
| `test_apply_presample` | 通过 | 0.00s |
| `test_collect_filter` | 通过 | 1.26s |

## test_jd_proficiency.py（6 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_strict_contract` | 通过 | 0.05s |
| `test_flags` | 通过 | 0.15s |
| `test_chunking` | 通过 | 0.03s |
| `test_cache` | 通过 | 0.01s |
| `test_aggregate_skills_skipped` | 通过 | 0.00s |
| `test_aggregate_proficiency` | 通过 | 0.00s |

## test_jd_sample.py（3 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_full_retention_under_cap` | 通过 | 0.00s |
| `test_floor_protects_rare_strata` | 通过 | 0.01s |
| `test_determinism_and_nesting` | 通过 | 0.01s |

## test_jd_source.py（5 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_delta_store_jd` | 通过 | 0.01s |
| `test_snapshot_contrib_branches` | 通过 | 0.00s |
| `test_merge_three_sources` | 通过 | 0.00s |
| `test_participation` | 通过 | 0.01s |
| `test_confirm_anchor` | 通过 | 0.00s |

## test_jd_summary_replay.py（4 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_row_maps_codes_to_names` | 通过 | 0.00s |
| `test_write_summary_csv_filters` | 通过 | 0.01s |
| `test_replay_plan_and_dry_run` | 通过 | 0.17s |
| `test_replay_validation_guards` | 通过 | 0.01s |

## test_job_categorize.py（4 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_find` | 通过 | 0.05s |
| `test_flow` | 通过 | 0.10s |
| `test_suggest_only` | 通过 | 0.05s |
| `test_prompt` | 通过 | 0.04s |

## test_job_hot_update.py（5 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_infer_source_kind` | 通过 | 0.00s |
| `test_evidence_aggregation` | 通过 | 0.00s |
| `test_assoc_record_doc_id` | 通过 | 0.00s |
| `test_validate_candidate_rules` | 通过 | 0.00s |
| `test_dedup_links` | 通过 | 0.00s |

## test_llm_402_breaker.py（3 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_all_keys_402_circuit_breaks` | 通过 | 0.00s |
| `test_partial_402_rotates_to_healthy_key` | 通过 | 0.00s |
| `test_map_signals_propagates_breaker` | 通过 | 0.00s |

## test_llm_call.py（7 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_extract_json_tolerant` | 通过 | 0.00s |
| `test_call_llm_success` | 通过 | 0.00s |
| `test_call_llm_length_upgrade` | 通过 | 0.00s |
| `test_call_llm_402_breaker_single_key` | 通过 | 0.00s |
| `test_call_llm_402_rotates_then_breaks` | 通过 | 0.00s |
| `test_call_llm_402_then_success` | 通过 | 0.00s |
| `test_call_llm_non_retryable_http_error` | 通过 | 0.00s |

## test_llm_client_cache.py（5 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_keyring_round_robin` | 通过 | 0.00s |
| `test_keyring_thread_safety` | 通过 | 0.00s |
| `test_extract_json_array_tolerant` | 通过 | 0.00s |
| `test_gather_batch_fault_tolerance` | 通过 | 0.00s |
| `test_sentence_cache_roundtrip` | 通过 | 0.01s |

## test_llm_client_post.py（4 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_client_post_success_and_tokens` | 通过 | 0.00s |
| `test_client_post_429_retry_then_ok` | 通过 | 3.48s |
| `test_client_post_non_retryable_raises` | 通过 | 0.00s |
| `test_builder_call_llm_success_and_length_upgrade` | 通过 | 0.00s |

## test_llm_live.py（3 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_live_call_llm_json_contract` | 通过 | 1.07s |
| `test_live_classify_sentences_skill` | 通过 | 2.69s |
| `test_live_merged_extraction` | 通过 | 2.56s |

## test_mapper_job_gate.py（2 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_job_gate_verdicts` | 通过 | 0.00s |
| `test_job_gate_llm_failure_keeps` | 通过 | 0.00s |

## test_mapper_near_recheck.py（2 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_near_recheck_same_forces_map` | 通过 | 0.00s |
| `test_near_recheck_llm_failure_keeps` | 通过 | 0.00s |

## test_mapper_recheck.py（1 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_recheck_keeps_revisions` | 通过 | 0.00s |

## test_news_lazy_parse.py（3 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_scan_news_metadata_sync_and_fallback` | 通过 | 0.17s |
| `test_parse_news_selected` | 通过 | 0.01s |
| `test_news_delta_window_lazy_wiring` | 通过 | 1.80s |

## test_news_sampling.py（2 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_news_sample_cap_deterministic` | 通过 | 0.01s |
| `test_news_sample_cap_noop_within_cap` | 通过 | 0.01s |

## test_overlay_participation.py（7 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_build_overlay_labels` | 通过 | 0.00s |
| `test_classify_merged_overlays` | 通过 | 0.00s |
| `test_merge_delta_remap_effective_window` | 通过 | 0.00s |
| `test_merge_delta_rename_retroactive` | 通过 | 0.00s |
| `test_participating_items_remap_skip` | 通过 | 0.00s |
| `test_job_overlay_prompt_and_split` | 通过 | 0.00s |
| `test_merge_delta_graduated_window_gate` | 通过 | 0.00s |

## test_paper_delta_mention.py（4 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_strengthen_and_idempotent` | 通过 | 0.01s |
| `test_cross_paper_noisy_or` | 通过 | 0.00s |
| `test_evidence_cap` | 通过 | 0.00s |
| `test_empty_mention` | 通过 | 0.00s |

## test_promotion.py（3 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_dry_run` | 通过 | 0.03s |
| `test_scan_grade_not_counted` | 通过 | 0.02s |
| `test_promote_and_mark` | 通过 | 0.09s |

## test_skillpoint_norm.py（6 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_l1_folding` | 通过 | 0.00s |
| `test_l2_alias_semantic` | 通过 | 0.00s |
| `test_distinct_not_merged` | 通过 | 0.00s |
| `test_l3_llm_first_seen` | 通过 | 0.01s |
| `test_normalize_skillpoint_map_dedup` | 通过 | 0.00s |
| `test_expansion_and_retired` | 通过 | 0.00s |

## test_snapshot.py（5 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_merge_and_filter` | 通过 | 0.00s |
| `test_job_links` | 通过 | 0.01s |
| `test_empty_delta` | 通过 | 0.00s |
| `test_build_and_idempotent` | 通过 | 0.53s |
| `test_keep_base_edges` | 通过 | 0.73s |

## test_stage_helpers.py（5 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_build_universe_dedup` | 通过 | 0.01s |
| `test_make_presample_trigger_and_not` | 通过 | 0.01s |
| `test_make_sample_end_to_end` | 通过 | 0.01s |
| `test_build_variants_star_cluster` | 通过 | 0.02s |
| `test_iter_annotated` | 通过 | 0.10s |

## test_synthesis.py（5 例，全部通过）

| 用例 | 结果 | 耗时 |
| --- | --- | ---: |
| `test_gaps` | 通过 | 0.00s |
| `test_synthesis_math` | 通过 | 0.00s |
| `test_ts_cap` | 通过 | 0.00s |
| `test_empty_delta` | 通过 | 0.00s |
| `test_integration_independent` | 通过 | 0.68s |

