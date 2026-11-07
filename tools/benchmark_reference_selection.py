"""Text-only local LLM trial against a copied lighthouse project, never live data."""
from pathlib import Path
import os,sys,json,shutil,hashlib,time,asyncio,statistics,argparse
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source-project',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--base-url',default='http://127.0.0.1:8090/v1')
parser.add_argument('--repeats',type=int,default=3)
args=parser.parse_args()
SOURCE=args.source_project.resolve();OUT=args.output.resolve()
if OUT==SOURCE or SOURCE in OUT.parents:raise ValueError('Output must be outside the source project')
if not 1<=args.repeats<=10:raise ValueError('Use 1 to 10 repeats')
OUT.mkdir(parents=True,exist_ok=True)
DATA=OUT/'data'
os.environ.update(DS_DATA_DIR=str(DATA),DS_LLM_PROVIDER='openai-compatible',DS_LLM_BASE_URL=args.base_url,DS_VRAM_SHARED_GPU='false')
sys.path.insert(0,str(ROOT/'backend'));sys.stdout.reconfigure(encoding='utf-8')
def hashes():return {str(p.relative_to(SOURCE)):hashlib.sha256(p.read_bytes()).hexdigest() for p in SOURCE.rglob('*') if p.is_file() and ('library' in p.parts or p.name=='project.json' or 'shots' in p.parts)}
before=hashes();dest=DATA/'projects'/SOURCE.name
dest.mkdir(parents=True,exist_ok=True)
for name in ['project.json','shots','library']:
 src=SOURCE/name;target=dest/name
 if not target.exists():
  shutil.copytree(src,target) if src.is_dir() else shutil.copy2(src,target)
from app.core.projects.store import load_project,load_shot,save_shot
from app.shot_workspace.reference_selection import suggest,catalog
from app.shot_workspace.model import WorkspaceModel
from app.core.vram.director_model import set_director_model
import httpx
model=httpx.get(args.base_url.rstrip('/')+'/models').json()['data'][0]['id']
set_director_model(model,provider_id='openai-compatible',persist=False)
project=load_project(SOURCE.name);baseline={}
for sid in project.shot_ids:
 original=json.loads((SOURCE/'shots'/(sid+'.json')).read_text(encoding='utf-8'))
 baseline[sid]={'title':original['title'],'refs':original['refs'],'description':original['script_beat']}
 shot=load_shot(project.id,sid);shot.refs=[];save_shot(shot)
(OUT/'baseline.json').write_text(json.dumps(baseline,indent=2),encoding='utf-8')
provider=WorkspaceModel();calls=[]
original_chat=provider.adapter.client.chat_response
async def measured(*args,**kwargs):
 start=time.perf_counter();r=await original_chat(*args,**kwargs)
 calls.append({'duration_s':round(time.perf_counter()-start,3),'usage':r.get('usage'), 'finish_reason':r.get('finish_reason')})
 return r
provider.adapter.client.chat_response=measured
async def main():
 records=[]
 for repeat in range(args.repeats):
  for sid in project.shot_ids:
   shot=load_shot(project.id,sid);start=time.perf_counter()
   try:
    value=await suggest(shot,[],provider)
    value['selected_names']=[next(c['name'] for c in catalog(project.id) if c['asset_id']==m['asset_id'] and c['file_key']==m['file_key']) for m in value['materials']]
    value['baseline_refs']=baseline[sid]['refs']
    value['same_asset_set_as_manual']=set(m['asset_id'] for m in value['materials'])==set(m['asset_id'] for m in baseline[sid]['refs'])
    record={'repeat':repeat+1,'shot_id':sid,'title':shot.title,'elapsed_s':round(time.perf_counter()-start,3),'result':value,'llm_call':calls[-1]}
   except Exception as e:record={'repeat':repeat+1,'shot_id':sid,'error':str(e),'elapsed_s':round(time.perf_counter()-start,3)}
   records.append(record)
   report={'model':model,'endpoint':args.base_url,'candidate_count':len(catalog(project.id)),'records':records,'source_project_unchanged':before==hashes(),'scope':f'{len(project.shot_ids)} original shots; all reference bindings cleared in copy, original manual refs withheld from model; loaded-model/repeat timings, no GPU video generation','manual_match_caveat':'Historical manual bindings are a comparison, not ground truth. Mentioned props omitted manually can be useful suggestions.'}
   times=[r['elapsed_s'] for r in records if 'result' in r]
   if times:report['summary']={'n':len(times),'min_s':min(times),'median_s':statistics.median(times),'max_s':max(times)}
   (OUT/'results.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
   print(json.dumps({k:record[k] for k in ['repeat','title','elapsed_s']} | {'selected':record.get('result',{}).get('selected_names'),'error':record.get('error')}),flush=True)
 assert before==hashes(),'Source project changed'
asyncio.run(main())
