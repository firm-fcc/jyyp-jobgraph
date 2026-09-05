"""One authorized smoke; public metadata plus private, local-only replay data."""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def summarize_events(events):
    totals = {}
    for row in events:
        if row.get('event') == 'end' and 'elapsed_seconds' in row:
            totals[row['stage']] = round(totals.get(row['stage'], 0) + row['elapsed_seconds'], 6)
    transport = [r for r in events if r['stage'] in ('llm_transport','proficiency_llm_transport') and r['event']=='end']
    starts = [r for r in events if r['stage'] in ('llm_transport','proficiency_llm_transport') and r['event']=='start']
    logical = [r for r in events if r['stage'] in ('llm_logical','proficiency_llm_logical') and r['event']=='start']
    fingerprints = Counter(r.get('request_fingerprint') for r in logical)
    durations = [r['elapsed_seconds'] for r in transport]
    return {'stage_seconds':totals,'logical_api_calls':len(logical),
            'transport_attempts':len(starts),'transport_retries':max(0,len(starts)-len(logical)),
            'transport_completed':len(transport),'transport_errors':sum(r.get('status')=='FAIL' for r in transport),
            'timeout_errors':sum(r.get('error_type')=='AgenticLLMTimeoutError' for r in transport),
            'identical_logical_request_repeats':sum(n-1 for n in fingerprints.values() if n>1),
            'verifiable_tokens':sum(r.get('usage',{}).get('total_tokens',0) for r in transport),
            'average_completed_transport_seconds':round(sum(durations)/len(durations),6) if durations else None,
            'slowest_completed_transport_seconds':max(durations) if durations else None}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--allow-real-api',action='store_true')
    parser.add_argument('--env-file',type=Path,required=True)
    parser.add_argument('--resume',type=Path,required=True)
    parser.add_argument('--expected-sha256',required=True)
    args=parser.parse_args()
    if not args.allow_real_api:
        raise SystemExit('Explicit real API authorization required')
    pdf=args.resume.resolve()
    assert pdf.suffix.lower()=='.pdf'
    assert hashlib.sha256(pdf.read_bytes()).hexdigest()==args.expected_sha256
    out=ROOT/'validation/e2e_runtime_acceptance_final'
    assert not out.exists(),'Existing attempt must not be overwritten or rerun'
    private=ROOT.parent/'tmp/backend_e2e_replay_candidate_0068_final'
    assert not private.exists(),'Existing private replay data must not be overwritten'
    from dotenv import dotenv_values
    values=dotenv_values(args.env_file)
    for name in ('LLM_API_KEY','LLM_MODEL','LLM_API_URL','LLM_API_BASE','LLM_TIMEOUT'):
        if values.get(name):os.environ[name]=values[name]
    from urllib.parse import urlsplit
    assert urlsplit(os.getenv('LLM_API_URL') or os.getenv('LLM_API_BASE','')).hostname=='api.deepseek.com'
    assert os.getenv('LLM_MODEL')=='deepseek-v4-flash'
    os.environ['PYTHONDONTWRITEBYTECODE']='1'
    os.environ['MATCHING_DECISION_THRESHOLD']='0.380952'
    os.environ['CANDIDATE_REQUEST_TIMEOUT']='900'
    out.mkdir()
    private.mkdir(parents=True)
    def save_private(name, value):
        text=json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)
        assert os.environ['LLM_API_KEY'] not in text
        (private/(name+'.json')).write_text(text+'\n',encoding='utf-8')
    os.environ['BACKEND_PROFICIENCY_TIMING_FILE']=str(out/'proficiency_timing.jsonl')
    from fastapi.testclient import TestClient
    from app import app
    from backend.config import OUTPUT_DIR
    from backend.runtime_observability import read_events
    from extractor.team_skill_schema_v3 import CandidateSkillProfile
    from extractor.target_job_profile_learning_bridge import TargetJobProfileLearningBridge
    from backend.services import learning_path_service
    from backend.schemas import LearningPathRequest
    original_render=learning_path_service.render_api_learning_result
    def capture_render(result):
        save_private('planner_output',result.to_dict())
        save_private('renderer_input',result.to_dict())
        try:
            return original_render(result)
        except Exception:
            save_private('backend_error',{'traceback':traceback.format_exc()})
            raise
    learning_path_service.render_api_learning_result=capture_render
    save_private('learning_path_request_schema',LearningPathRequest.model_json_schema())
    initial_logs=set(OUTPUT_DIR.glob('*.timing.jsonl'))
    result={'schema_version':'backend_real_e2e_runtime_acceptance_v1',
            'candidate_id':'candidate_0068','input_sha256':args.expected_sha256,
            'input_origin':'existing_real_anonymized_Pilot_privacy_only_PDF_conversion',
            'test_pdf_in_public_archive':False,'model':os.environ['LLM_MODEL'],
            'job_id':'133663124','job_window':'2022-10','threshold':0.380952,
            'http_mode':'local_ASGI_TestClient_real_services_real_LLM_not_mock',
            'gold_used':False,'formal_accuracy_evaluated':False,
            'status':'RUNNING','endpoints':{},'checks':{}}
    def save():
        text=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)
        assert os.environ['LLM_API_KEY'] not in text
        (out/'validation_result.json').write_text(text+'\n',encoding='utf-8')
    save()
    began=time.perf_counter()
    client=TestClient(app,raise_server_exceptions=False)
    def call(name,method,url,**kwargs):
        if time.perf_counter()-began>=900:raise TimeoutError('runtime_budget_exhausted')
        print(name+' START',flush=True);start=time.perf_counter()
        r=getattr(client,method)(url,**kwargs)
        try:data=r.json()
        except ValueError:data={'non_json_response':r.text}
        if name in ('candidate','target_job','match','learning_path'):
            save_private(name+'_response',{'http_status':r.status_code,'body':data})
        result['endpoints'][name]={'http_status':r.status_code,'seconds':round(time.perf_counter()-start,6),
            'schema_version':data.get('schema_version') if isinstance(data,dict) else None}
        save()
        print(name+' HTTP_'+str(r.status_code),flush=True)
        if r.status_code!=200:raise RuntimeError(name+'_http_failure')
        return data
    try:
        health=call('health','get','/health')
        assert health['llm_configured'] and health['matching_calibrated']
        # Local parse/PII validation happened before this request, not inside it.
        c=call('candidate','post','/api/candidate',data={'candidate_id':'candidate_0068'},
               files={'file':('candidate_0068.pdf',pdf.read_bytes(),'application/pdf')})
        profile=c['candidate_skill_profile'];text=c['resume_text']
        CandidateSkillProfile.from_dict(profile)
        assert c['schema_version']=='candidate_api_response_v1_1' and text.strip()
        evidence=[e for a in profile['assessments'] for e in a['evidence']]
        assert evidence and all(type(e.get('start')) is int and type(e.get('end')) is int and
          0<=e['start']<e['end']<=len(text) and text[e['start']:e['end']]==e['text'] for e in evidence)
        assert c['grounded_capability_candidates']
        result['checks'].update({'candidate_schema':True,'resume_text_nonempty':True,'evidence_offsets_exact':True,
                                  'grounded_candidates_present':True})
        result['candidate_skill_count']=len(profile['assessments'])
        result['grounded_capability_count']=len(c['grounded_capability_candidates'])
        result['evidence_count']=len(evidence)
        result['candidate_proficiency_status']=c['proficiency_status']
        # Existing diagnostic counters only, no candidate labels or response text.
        link=c['diagnostics'].get('team_skill_linking',{})
        result['candidate_contract_counters']={k:v for k,v in link.items() if type(v) is int and 'count' in k}
        target=call('target_job','get','/api/target-job/133663124')
        TargetJobProfileLearningBridge().build(target)
        assert target['schema_version']=='target_job_profile_v1.1'
        m=call('match','post','/api/match',json={'candidate_profile':profile,
              'target_job_profile':target,'auto_proficiency':True})
        match=m['match_result'];levels=m['proficiency']['levels'];summary=match['summary']
        assert levels and set(levels.values())<={'P1','P2','P3','P4','U'}
        assert match['schema_version']=='match_result_v1'
        assert 0<=match['match_score']<=100 and 0<=match['metrics']['verified_fit']<=1
        assert match['decision'] in {'MATCH','NO_MATCH'} and match['decision_threshold']==0.380952
        gaps={k:summary[k] for k in ('satisfied','level_gap','evidence_insufficient','missing')}
        assert sum(gaps.values())==summary['required_skills']
        result.update({'proficiency_count':len(levels),'required_skill_count':summary['required_skills'],
                       'match_score':match['match_score'],'verified_fit':match['metrics']['verified_fit'],
                       'decision':match['decision'],'gap_counts':gaps})
        result['checks'].update({'target_schema':True,'proficiency_generated':True,'match_ranges':True,'decision_calibrated':True,'gap_sum':True})
        learning_request={'candidate_profile':profile,'target_job_profile':target,
                          'proficiency_levels':levels,'auto_proficiency':False}
        save_private('learning_path_request',learning_request)
        l=call('learning_path','post','/api/learning-path',json=learning_request)
        assert l['schema_version']=='learning_path_api_response_v1'
        assert l['proficiency']['source']=='provided' and l['proficiency']['levels']==levels
        statuses=Counter(p['path_status'] for p in l['rendered']['skill_paths'])
        assert statuses
        for path in l['rendered']['skill_paths']:
            if path['path_status']=='GRAPH_UNAVAILABLE':
                assert not path['learning_steps'] and path['verification_guidance'] is None
                assert path['capstone_guidance'] is None
                assert path['path_mode'] in {'LEARN','DEEPEN','VERIFY_FIRST'}
        result['checks']['graph_unavailable_has_no_fabricated_steps']=True
        result['learning_path_status_counts']=dict(statuses)
        result['checks'].update({'learning_schema':True,'proficiency_reused':True})
        result['status']='PASS'
    except Exception as exc:
        result['status']='FAIL'
        result['error_type']=type(exc).__name__
        # No error body, no fabricated completion estimate.
        result['estimated_total_completion_seconds']=None
    finally:
        learning_path_service.render_api_learning_result=original_render
        events=[]
        for p in sorted(set(OUTPUT_DIR.glob('*.timing.jsonl'))-initial_logs):events.extend(read_events(p))
        events.extend(read_events(out/'proficiency_timing.jsonl'))
        result['runtime']=summarize_events(events)
        result['runtime']['total_seconds']=round(time.perf_counter()-began,6)
        (out/'stage_timing.json').write_text(json.dumps(events,indent=2)+'\n',encoding='utf-8')
        save()
    print(json.dumps({'status':result['status'],'endpoints':result['endpoints'],'runtime':result['runtime']},ensure_ascii=False),flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
