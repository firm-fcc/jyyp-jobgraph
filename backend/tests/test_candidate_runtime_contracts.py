"""Offline evidence contracts, using synthetic text and fake completion only."""
import json
import pytest

from extractor.agentic_llm_client import LLMCompletion
from extractor.evidence_extraction_agent import EvidenceExtractionAgent, ExtractionAgentError
from extractor.evidence_grounding_v4 import locate_evidence_conservatively
from extractor.extraction_reground_v4 import reground_full_resume_extraction_v4
from extractor.team_skill_registry import TeamSkillRegistry
from extractor.team_skill_verifier_v4 import EvidenceSkillVerifierV4, TeamSkillVerifierContractError
from extractor.team_skill_auditor_v4 import TeamSkillAuditorV4
from extractor.team_skill_profile_v4 import build_candidate_skill_profile
from extractor.grounded_capability_trace_v4 import build_grounded_capability_trace
from extractor.explicit_skill_mentions_v4 import extract_explicit_skill_mentions
from extractor.resume_segmentation_v4 import build_internal_segments_v4
from extractor.team_skill_fallback_selector_v4 import FallbackTeamSkillSelectorV4, FallbackSelectorContractError

TEXT = "使用PyTorch训练ResNet模型并完成评估。"


class FakeClient:
    def __init__(self, payload): self.payload=payload; self.calls=0
    def complete(self, system_prompt, user_prompt):
        self.calls+=1
        content=self.payload if isinstance(self.payload,str) else json.dumps(self.payload,ensure_ascii=False)
        return LLMCompletion(content=content,model="offline-test",usage={"total_tokens":2},elapsed_ms=1,raw_response_metadata={})
    def complete_json(self, system_prompt, user_prompt, max_tokens=None):
        return self.complete(system_prompt,user_prompt)


def extraction():
    payload={"candidates":[{"project_id":"resume_full","fact":TEXT,"behavior":TEXT,"ability":"模型训练","evidence":[TEXT],"reason":"test","confidence":0.9}]}
    return EvidenceExtractionAgent(FakeClient(payload)).extract("test_candidate",TEXT)


def test_frozen_evidence_to_profile_chain():
    extracted=extraction()
    assert extracted.diagnostics_dict()["located_evidence_count"]==1
    grounded=reground_full_resume_extraction_v4(extracted,TEXT)
    cand=grounded.candidates[0]
    registry=TeamSkillRegistry()
    model=FakeClient({"assessments":[{"team_skill_id":"T-AI-01","status":"supported","confidence":0.9,"supporting_evidence_indices":[0]}]})
    verified=EvidenceSkillVerifierV4(model).verify(candidate_id="test_candidate",evidence_candidate=cand,candidate_skills=[registry.get("T-AI-01")])
    audit=TeamSkillAuditorV4(registry).audit(cand,verified.assessments[0])
    profile=build_candidate_skill_profile(candidate_id="test_candidate",evidence_candidates=[cand],audited_assessments=[audit],registry=registry)
    assert profile.assessments[0].status=="supported"
    for e in profile.assessments[0].evidence: assert TEXT[e.start:e.end]==e.text
    trace=build_grounded_capability_trace([cand],[audit])
    assert trace[0]["grounded_evidence"][0]["text"]==TEXT
    assert trace[0]["hint_authority"]=="non_authoritative_llm_annotation"


@pytest.mark.parametrize("payload",["[]","explanation {}",'{"candidates":[],"extra":true}','{"candidates":null}','{"candidates":[{}]}','{"candidates":[],"candidates":[]}'])
def test_extraction_contract_fail_closed(payload):
    with pytest.raises(ExtractionAgentError): EvidenceExtractionAgent(FakeClient(payload)).extract("test_candidate",TEXT)


@pytest.mark.parametrize("quote,source,located",[("Python  开发","Python\n开发",True),("不存在","Python开发",False),("Python 开发","Python\n开发；Python\t开发",False),("Python开发","Python开发",True)])
def test_grounding_never_invents_spans(quote,source,located):
    evidence,stats=locate_evidence_conservatively([quote],source,"x")
    assert bool(evidence)
    assert (evidence[0].start is not None)==located
    if located: assert source[evidence[0].start:evidence[0].end]==evidence[0].text
    else: assert stats.unlocated_count==1


@pytest.mark.parametrize("status,indices",[("supported",[]),("unsupported",[0]),("supported",[4]),("supported",[True])])
def test_verifier_rejects_invalid_evidence_indices(status,indices):
    candidate=extraction().candidates[0]
    data={"assessments":[{"team_skill_id":"T-AI-01","status":status,"confidence":0.9,"supporting_evidence_indices":indices}]}
    with pytest.raises(TeamSkillVerifierContractError):
        EvidenceSkillVerifierV4._parse(json.dumps(data),["T-AI-01"],candidate.evidence)


def test_selector_allowed_ids_only():
    data={"selections":[{"source_candidate_ability_id":"s1","team_skill_ids":["T-AI-01"]}]}
    kw=dict(expected_source_ids=["s1"],allowed_skill_ids=["T-AI-01"],max_candidates=8)
    assert FallbackTeamSkillSelectorV4._parse(json.dumps(data),**kw)[0].team_skill_ids==("T-AI-01",)
    data["selections"][0]["team_skill_ids"]=["T-NOT-REAL"]
    with pytest.raises(FallbackSelectorContractError): FallbackTeamSkillSelectorV4._parse(json.dumps(data),**kw)


def test_segmentation_preserves_source_and_mentions_are_display_only():
    block="项目A\n2021.01-2022.01\n开发接口。\n项目B\n2022.02-2023.01\n训练模型。"
    text="技能\nPython、SQL\n项目经历\n"+block
    segments=build_internal_segments_v4(text,{"project_experience":[block],"research":["missing"]})
    assert len(segments)==2
    assert all(text[s.start:s.end]==s.text for s in segments)
    assert len({s.segment_id for s in segments})==2
    mentions=extract_explicit_skill_mentions(text)
    assert mentions and all("status" not in m for m in mentions)
