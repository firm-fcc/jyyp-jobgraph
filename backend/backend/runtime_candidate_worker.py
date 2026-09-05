"""Execute the original frozen CLI with observational timing, not a new pipeline."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / 'candidate_core'
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(CORE))
from backend.runtime_observability import RuntimeTrace


def main():
    trace = RuntimeTrace(os.environ.get('BACKEND_STAGE_TIMING_FILE'))
    spec = importlib.util.spec_from_file_location('frozen_candidate_entry', CORE / 'run_v3.py')
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    parsed_result = []
    entry.parse_file_v3 = trace.wrap('document_parse', entry.parse_file_v3,
                                    on_result=parsed_result.append)
    entry.build_internal_segments_v4 = trace.wrap('segmentation', entry.build_internal_segments_v4)
    for cls in (entry.EvidenceExtractionAgent, entry.SegmentedEvidenceExtractionAgentV4):
        cls.extract = trace.wrap('evidence_extraction', cls.extract)
    entry.TeamSkillLinkingPipelineV3.link = trace.wrap('team_skill_pipeline', entry.TeamSkillLinkingPipelineV3.link)
    for name in ('build_candidate_skill_profile', 'resolve_occupation_warrants_v431',
                 'build_grounded_capability_trace', 'extract_explicit_skill_mentions'):
        setattr(entry, name, trace.wrap('candidate_profile_assembly', getattr(entry, name)))
    entry.AgenticLLMClient.complete = trace.wrap('llm_transport', entry.AgenticLLMClient.complete, llm=True, unbound=True)
    entry.ReliableCompletionClient.complete = trace.wrap('llm_logical', entry.ReliableCompletionClient.complete, llm=True, unbound=True)
    trace.emit('segmentation', 'policy', status='FROZEN_DOCUMENT_FULL_RESUME_PATH')
    trace.emit('proficiency', 'not_run', status='SEPARATE_DOWNSTREAM_ENDPOINT')
    result = 1
    try:
        # Do not relay raw model/contract errors or input text to application logs.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = entry.main()
        if result == 0 and parsed_result:
            args = entry.build_arg_parser().parse_args()
            output = Path(args.output)
            # Ephemeral transport sidecar, not a timing log; deleted by the parent.
            output.with_suffix('.source.json').write_text(
                json.dumps({'resume_text': parsed_result[-1].text}, ensure_ascii=False), encoding='utf-8')
        trace.emit('worker', 'end', return_code=result)
        return result
    except Exception as exc:
        trace.emit('worker', 'end', return_code=1, error_type=type(exc).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
