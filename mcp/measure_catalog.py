"""Offline catalog token estimates; never starts services or sends model requests.

Run in the project Python environment with tiktoken installed:
    python mcp/measure_catalog.py --output .run/mcp-catalog-tokens.json
"""
import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    import tiktoken
    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root / 'mcp'), str(root / 'backend')]
    spec = importlib.util.spec_from_file_location('catalog_measure_bridge', root / 'mcp/server.py')
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    from app.agents.director.tool_schema import DIRECTOR_TOOL_SCHEMAS, GPT_REF_FRAME_TOOL
    native = {tool['function']['name']: tool['function'] for tool in [*DIRECTOR_TOOL_SCHEMAS, GPT_REF_FRAME_TOOL]}

    async def offline_request(method, path, **kwargs):
        if method == 'GET' and path == '/agent/tools':
            return {'tools': list(native.values())}
        raise AssertionError('Measurement must not perform network requests')
    bridge.request = offline_request
    encodings = {name: tiktoken.get_encoding(name) for name in ['o200k_base', 'cl100k_base']}

    def count(value):
        text = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
        return {name: len(encoding.encode(text)) for name, encoding in encodings.items()}

    async def measure():
        result = {'method': 'Offline compact JSON token estimate; excludes host prompts, model reasoning and later responses. Not billing or a completion-rate benchmark.', 'catalogs': {}}
        for mode in ['full', 'compact']:
            os.environ['DIRECTOR_MCP_CATALOG_MODE'] = mode
            tools = await bridge.mcp.list_tools()
            result['catalogs'][mode] = {'tool_count': len(tools),
                'tokens': count([tool.model_dump(mode='json', by_alias=True, exclude_none=True) for tool in tools])}
        result['on_demand_contract_tokens'] = {name: count(await bridge.get_tool_contract(name))
            for name in ['append_shots', 'revise_shot', 'start_h3_video', 'import_h3_workflow', 'test_h3_workflow'] if name in native or name in bridge.H3_TOOLS}
        from app.api.agent_h3_workflows import contract
        result['h3_mapping_contract_tokens'] = count(contract())
        result['initialization_instruction_tokens'] = count(bridge.mcp.instructions)
        return result
    result = asyncio.run(measure())
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
