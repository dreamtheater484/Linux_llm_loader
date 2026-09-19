import asyncio
import sys
from types import SimpleNamespace

from loader.live_output import LiveOutput
from loader.benchmarks import EvaluationManager, summarize


def test_tail_incremental_updates_coalesce_without_losing_or_duplicating_tokens():
    tail=LiveOutput()
    tail.start('one','coding','HumanEval+',{'name':'Model'})
    tail.append('model','hello')
    first=tail.read()
    tail.append('model',' world')
    delta=tail.read(first['cursor'],'one')
    assert not delta['reset'] and len(delta['blocks'])==1
    assert delta['blocks'][0]['id']==first['blocks'][-1]['id']
    assert delta['blocks'][0]['text']=='hello world'
    assert not tail.read(delta['cursor'],'one')['blocks']
    assert tail.read(delta['cursor'],'other')['reset']
    tail.finish({'id':'one','state':'complete'})
    assert tail.meta['state']=='complete'
    assert 'hello world' in ''.join(b['text'] for b in tail.read()['blocks'])


def test_tail_bounded_reset_and_aborted_not_retained():
    tail=LiveOutput()
    tail.start('one','speed','Speed test',{'name':'Model'})
    tail.append('model','x'*2_000_000)
    read=tail.read()
    assert read['truncated'] and len(read['blocks'])<=256
    assert sum(len(b['text']) for b in read['blocks'])<=256*4096
    tail.finish({'id':'one','state':'cancelled'})
    assert not tail.read()['blocks']
    tail.start('two','coding','HumanEval+',{'name':'Other model'})
    assert tail.read(read['cursor'],'one')['reset']
    assert tail.meta['state']=='running'


def test_command_output_arrives_before_process_finishes(tmp_path):
    async def check():
        manager=EvaluationManager(tmp_path,SimpleNamespace(),SimpleNamespace())
        manager.output.start('one','coding','Test',{'name':'Model'})
        task=asyncio.create_task(manager.command(sys.executable,'-u','-c',"import time;print('early output',flush=True);time.sleep(0.5);print('late output')",live=True))
        for _ in range(40):
            if 'early output' in ''.join(b['text'] for b in manager.output.read()['blocks']):
                break
            await asyncio.sleep(.01)
        assert not task.done()
        assert 'early output' in ''.join(b['text'] for b in manager.output.read()['blocks'])
        await task
        assert 'late output' in ''.join(b['text'] for b in manager.output.read()['blocks'])
    asyncio.run(check())


def test_swe_scores_are_resolved_tasks_without_partial_credit():
    def score(states):
        return summarize({'tasks':[dict(state=s,metrics=[]) for s in states]})['score']
    assert score(['failed'])==0
    assert score(['passed'])==100
    assert score(['passed','failed'])==50
    assert score(['passed','failed','failed'])==33.3
    assert score(['passed','passed','failed'])==66.7
    assert score(['passed','timed_out']) is None
