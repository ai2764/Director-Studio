import io,json
from contextlib import asynccontextmanager
import pytest
import pytest_asyncio
from PIL import Image
from app.core.projects.store import create_project,save_shot,load_shot
from app.core.library.store import create_external_asset,write_asset
from app.shot_workspace.router import add_shot,CreateShot,SuggestReferences,suggest_materials
from app.shot_workspace.reference_selection import catalog,suggest


@pytest_asyncio.fixture
async def selection_board():
    project=create_project('Selector test','',authoring_mode='shot_workspace')
    other=create_project('Other project','',authoring_mode='shot_workspace')
    shot=await add_shot(project.id,CreateShot(request_id='first'))
    shot.script_beat='A blue mug on the table. No people.';save_shot(shot)
    b=io.BytesIO();Image.effect_noise((96,96),30).convert('RGB').save(b,format='PNG')
    def asset(pid,name):return create_external_asset(kind='props',name=name,notes='A blue mug.',project_id=pid,image_bytes=b.getvalue(),image_filename='mug.png',file_key='master')
    owned=asset(project.id,'Owned mug');foreign=asset(other.id,'Secret other mug')
    return project,shot,owned,foreign


class Provider:
    def __init__(self,value,after=None):self.value=value;self.after=after;self.calls=[]
    async def complete(self,system,payload,**kwargs):
        self.calls.append(payload)
        assert not kwargs.get('images')
        assert set(payload)=={'shot_description','catalog'}
        if self.after:self.after()
        return json.dumps(self.value)


@pytest.mark.asyncio
async def test_scoped_selection_is_only_a_suggestion(selection_board):
    project,shot,asset,foreign=selection_board
    rows=catalog(project.id);assert [r['asset_id'] for r in rows]==[asset.id]
    p=Provider({'choices':[{'key':rows[0]['key'],'reason':'Visible blue mug.'}],'warnings':[]})
    result=await suggest(shot,[],p)
    assert result['materials'][0]['asset_id']==asset.id
    assert load_shot(project.id,shot.id).refs==[]
    assert foreign.id not in json.dumps(p.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('keys',[['R999'],['R1','R1']])
async def test_rejects_invalid_or_duplicate_model_keys(selection_board,keys):
    project,shot,_,_=selection_board
    p=Provider({'choices':[{'key':key,'reason':'A reason'} for key in keys]})
    with pytest.raises(ValueError,match='unknown or repeated'):await suggest(shot,[],p)
    assert not load_shot(project.id,shot.id).refs


@pytest.mark.asyncio
async def test_changed_description_discards_suggestion(selection_board):
    project,shot,_,_=selection_board
    def edit():
        current=load_shot(project.id,shot.id);current.script_beat='Different shot';save_shot(current)
    p=Provider({'choices':[]},after=edit)
    with pytest.raises(ValueError,match='changed during'):await suggest(shot,[],p)


@pytest.mark.asyncio
async def test_changed_asset_description_discards_suggestion(selection_board):
    _,shot,asset,_=selection_board
    def edit():asset.notes='Now a different description';write_asset(asset)
    with pytest.raises(ValueError,match='changed during'):await suggest(shot,[],Provider({'choices':[]},after=edit))


@pytest.mark.asyncio
async def test_empty_brief_does_not_call_model(selection_board):
    _,shot,_,_=selection_board;shot.script_beat=' '
    p=Provider({'choices':[]})
    with pytest.raises(ValueError,match='Save a shot description'):await suggest(shot,[],p)
    assert not p.calls


@pytest.mark.asyncio
async def test_current_draft_is_used_without_adjacent_or_dialogue_context(selection_board):
    project,shot,_,_=selection_board
    p=Provider({'choices':[]})
    await suggest(shot,[],p,description='An empty street, no people or props.')
    assert p.calls[0]['shot_description']=='An empty street, no people or props.'
    assert load_shot(project.id,shot.id).script_beat==shot.script_beat


@pytest.mark.asyncio
async def test_route_records_time_without_saving_refs(selection_board,monkeypatch):
    project,shot,_,_=selection_board
    class Orch:
        @asynccontextmanager
        async def llm_session(self,**kwargs):yield
        async def ensure_llm_ready(self):pass
    monkeypatch.setattr('app.core.vram.get_orchestrator',lambda:Orch())
    monkeypatch.setattr('app.shot_workspace.model.WorkspaceModel',lambda:Provider({'choices':[]}))
    result=await suggest_materials(shot.id,SuggestReferences(expected_refs=[]))
    from app.shot_workspace.timings import history
    assert result['operation_duration_s']>=0
    assert history(shot)[-1]['stage']=='reference_selection'
    assert not load_shot(project.id,shot.id).refs
