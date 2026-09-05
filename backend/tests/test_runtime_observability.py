import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from backend.runtime_observability import RuntimeTrace, read_events
from backend.services import candidate_service as cs


def test_timing_delegates_without_changing_request_or_result(tmp_path):
    seen=[]
    result=SimpleNamespace(usage={'total_tokens':5,'prompt_tokens':3,'completion_tokens':2})
    def original(a,b,**kwargs):
        seen.append((a,b,kwargs));return result
    path=tmp_path/'timing.jsonl'
    wrapped=RuntimeTrace(path).wrap('llm_transport',original,llm=True)
    assert wrapped('private prompt','private resume',max_tokens=23) is result
    assert seen==[('private prompt','private resume',{'max_tokens':23})]
    text=path.read_text()
    assert 'private prompt' not in text and 'private resume' not in text
    rows=read_events(path)
    assert rows[0]['event']=='start' and rows[1]['usage']['total_tokens']==5


def test_failure_type_only_no_exception_body(tmp_path):
    path=tmp_path/'timing.jsonl'
    def original():raise ValueError('PRIVATE_RESPONSE')
    with pytest.raises(ValueError,match='PRIVATE_RESPONSE'):
        RuntimeTrace(path).wrap('stage',original)()
    assert 'PRIVATE_RESPONSE' not in path.read_text()
    assert read_events(path)[-1]['error_type']=='ValueError'


def test_partial_final_log_line_is_safe(tmp_path):
    path=tmp_path/'timing.jsonl'
    path.write_text('{"event":"start"}\n{"event":')
    assert read_events(path)==[{'event':'start'}]
    assert read_events(tmp_path/'missing')==[]


def test_worker_real_frozen_parser_preflight_only(tmp_path):
    root=Path(__file__).resolve().parents[1]
    core=root/'candidate_core/run_v3.py'
    before=hashlib.sha256(core.read_bytes()).hexdigest()
    timing=tmp_path/'timing.jsonl'
    import os
    env=dict(os.environ,BACKEND_STAGE_TIMING_FILE=str(timing))
    out=tmp_path/'unused.json'
    p=subprocess.run([sys.executable,'-B',str(root/'backend/runtime_candidate_worker.py'),
                      '--resume',str(root/'examples/anonymous_dev_resume.pdf'),
                      '--output',str(out),'--preflight'],cwd=root/'candidate_core',env=env,
                     capture_output=True,timeout=30)
    assert p.returncode==0
    events=read_events(timing)
    assert sum(r['stage']=='document_parse' and r['event']=='start' for r in events)==1
    assert not any('llm_' in r['stage'] for r in events)
    assert hashlib.sha256(core.read_bytes()).hexdigest()==before


def test_service_consumes_child_source_without_second_parse(monkeypatch,tmp_path):
    text='child canonical text'
    path=tmp_path/'input.txt';path.write_text(text)
    async def fake(*args,**kwargs):
        output=Path(args[args.index('--output')+1])
        output.write_text(json.dumps({'candidate_skill_profile':{},'diagnostics':{}}))
        output.with_suffix('.source.json').write_text(json.dumps({'resume_text':text}))
        class P:
            returncode=0
            async def communicate(self):return b'',b''
        return P()
    monkeypatch.setattr(cs.asyncio,'create_subprocess_exec',fake)
    monkeypatch.setattr(cs,'parse_file_v3',lambda *_:pytest.fail('parent duplicate parse'))
    assert asyncio.run(cs.extract_candidate(path))['resume_text']==text


def test_unknown_timing_fields_refused(tmp_path):
    with pytest.raises(ValueError):RuntimeTrace(tmp_path/'x').emit('x','x',prompt='private')
