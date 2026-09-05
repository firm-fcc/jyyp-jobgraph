"""Apply V4 conservative grounding to legacy full-resume extraction results."""

from __future__ import annotations

from extractor.agentic_schema import CandidateAbility
from extractor.evidence_extraction_agent import ExtractionResult
from extractor.evidence_grounding_v4 import GroundingStats, locate_evidence_conservatively


def reground_full_resume_extraction_v4(
    extraction: ExtractionResult,
    resume_text: str,
) -> ExtractionResult:
    candidates: list[CandidateAbility] = []
    stats = GroundingStats()

    for candidate in extraction.candidates:
        evidence = []
        seen: set[tuple[str, str, int | None, int | None]] = set()
        for item in candidate.evidence:
            if item.start is not None and item.end is not None:
                key = (item.text, item.project_id, item.start, item.end)
                if key not in seen:
                    seen.add(key)
                    evidence.append(item)
                    stats = stats + GroundingStats(exact_count=1)
                continue

            relocated, local_stats = locate_evidence_conservatively(
                [item.text],
                resume_text,
                candidate.project_id,
                global_offset=0,
            )
            stats = stats + local_stats
            for new_item in relocated:
                key = (new_item.text, new_item.project_id, new_item.start, new_item.end)
                if key not in seen:
                    seen.add(key)
                    evidence.append(new_item)

        payload = candidate.to_dict()
        payload["evidence"] = [item.to_dict() for item in evidence]
        candidates.append(CandidateAbility.from_dict(payload))

    warnings = list(extraction.warnings)
    if stats.normalized_count:
        warnings.append(f"post_normalized_grounding_count={stats.normalized_count}")
    if stats.ambiguous_count:
        warnings.append(f"post_ambiguous_grounding_count={stats.ambiguous_count}")

    return ExtractionResult(
        resume_id=extraction.resume_id,
        candidates=candidates,
        model=extraction.model,
        elapsed_ms=extraction.elapsed_ms,
        usage=None if extraction.usage is None else dict(extraction.usage),
        raw_candidate_count=extraction.raw_candidate_count,
        accepted_candidate_count=extraction.accepted_candidate_count,
        invalid_candidate_count=extraction.invalid_candidate_count,
        located_evidence_count=stats.exact_count + stats.normalized_count,
        unlocated_evidence_count=stats.unlocated_count,
        warnings=warnings,
    )
