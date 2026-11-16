"""Direct port contracts: no official node discovery or graph rewriting."""
from __future__ import annotations

import copy
import math
from collections.abc import Mapping

from .inspector import _ancestors, _graph_edges, _is_link, _load_graph, _structural_issues
from .models import H3DirectInputMapping, H3ValidationIssue, ValidationReport


def direct_ports(mapping):
    for role in ('prompt', 'width', 'height', 'frames', 'seed', 'pictures', 'audios'):
        for port in getattr(mapping.inputs, role):
            yield role, port
    if mapping.context_video is not None:
        yield 'context_video', mapping.context_video
    native = mapping.native_latent_context
    if native:
        for role in ('load_path', 'load_index', 'save_prefix', 'save_index'):
            port = getattr(native, role)
            if port is not None:
                yield role, port


def validate_direct_contract(graph, mapping) -> ValidationReport:
    issues = []
    def issue(code, message, port=None, *, node_id=None, input_name=None):
        issues.append(H3ValidationIssue(code=code, message=message,
            node_id=port.node_id if port else node_id, input_name=port.input_name if port else input_name))
    try:
        graph = _load_graph(graph)
    except (TypeError, ValueError, RecursionError) as exc:
        return ValidationReport(valid=False, issues=(H3ValidationIssue(code='invalid_structure', message=str(exc)),))
    for item in _structural_issues(graph):
        issues.append(H3ValidationIssue(code=item.code, message=item.message,
            node_id=item.node_id, input_name=item.input_name))
    if issues:
        return ValidationReport(valid=False, issues=tuple(issues))
    outgoing, incoming, _ = _graph_edges(graph)
    output = mapping.output.node_id
    if output not in graph or outgoing.get(output):
        issue('invalid_output_selection', 'Output must be an existing terminal node')
    upstream = _ancestors(output, incoming) if output in graph else set()
    native = mapping.native_latent_context
    if native:
        save_id = native.save_output_node_id
        if save_id is not None and (save_id not in graph or outgoing.get(save_id)):
            issue('invalid_native_output', 'Native cache output must be an existing terminal root')
        if graph.get(output, {}).get('class_type') != 'SaveVideo':
            issue('invalid_native_video_output', 'Native context requires a SaveVideo output')
        for role, expected_class, expected_input in (
            ('save_prefix', 'MiniMaxH3MotionContextSaveLatent', 'filename_prefix'),
            ('save_index', 'MiniMaxH3MotionContextSaveLatent', 'clip_index'),
            ('load_path', 'MiniMaxH3MotionContextLoadLatent', 'latent_path'),
            ('load_index', 'MiniMaxH3MotionContextLoadLatent', 'clip_index'),
        ):
            port = getattr(native, role)
            if port and (graph.get(port.node_id, {}).get('class_type') != expected_class
                         or port.input_name != expected_input
                         or role.startswith('save') and port.node_id != save_id):
                issue('invalid_native_port', 'Native binding must target the declared cache class and literal port', port)
        if native.load_path and native.load_path.node_id != native.load_index.node_id:
            issue('invalid_native_loader', 'Native load ports must target the same loader')
        if native.load_path and native.load_path.node_id not in upstream:
            issue('unreachable_native_loader', 'Native loader must feed the selected video output')
        if save_id in graph:
            upstream |= _ancestors(save_id, incoming)
    for node_id, node in graph.items():
        if node.get('class_type') == 'MiniMaxH3MotionContextSaveLatent':
            if native is None or node_id != native.save_output_node_id:
                issue('unmapped_native_cache', 'Every native cache output requires explicit DS save bindings')
        if node.get('class_type') == 'MiniMaxH3MotionContextLoadLatent' and node_id in upstream:
            if native is None or native.load_path is None or node_id != native.load_path.node_id:
                issue('unmapped_native_cache',
                    'Bind this loader with mapping.native_latent_context.load_path and load_index; read get_h3_workflow_contract for the current schema',
                    node_id=node_id, input_name='latent_path')
    seen = set()
    for role, port in direct_ports(mapping):
        identity = (port.node_id, port.input_name)
        if identity in seen:
            issue('duplicate_binding', 'A port cannot receive more than one application input', port)
        seen.add(identity)
        node = graph.get(port.node_id, {})
        inputs = node.get('inputs', {})
        if port.node_id not in upstream:
            issue('unreachable_mapping', 'Mapped input must be upstream of the selected output', port)
        if port.input_name not in inputs:
            issue('missing_mapped_input', 'Mapped input does not exist', port)
            continue
        value = inputs[port.input_name]
        if _is_link(value):
            issue('linked_mapping', 'Bind the upstream literal port, not a connected tensor port', port)
        elif role in ('width', 'height', 'frames', 'seed', 'load_index', 'save_index'):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                issue('invalid_numeric_input', 'Numeric bindings require a finite numeric literal', port)
        elif not isinstance(value, str):
            issue('invalid_file_or_text_input', 'Text and media bindings require a string literal', port)
    for node_id, node in graph.items():
        for name, value in node.get('inputs', {}).items():
            if _is_link(value) and str(value[0]) not in graph:
                issue('dangling_link', f'{node_id}.{name} references missing node {value[0]}')
    if not issues:
        try:
            fill_direct_graph(graph, mapping, {'prompt': 'Contract test', 'width': 864, 'height': 480,
                'frames': 56, 'seed': 42,
                'images': [f'contract-picture-{i}.png' for i in range(len(mapping.inputs.pictures))],
                'audios': [f'contract-audio-{i}.wav' for i in range(len(mapping.inputs.audios))],
                'context_video': 'contract-context.mp4' if mapping.context_video else '',
                'native_latent_context': {'load_path': 'contract/source/native', 'load_index': 1,
                    'save_prefix': 'contract/job/native/clip', 'save_index': 1}}, check=False)
        except (TypeError, ValueError, OverflowError) as exc:
            issue('synthetic_fill_failed', str(exc))
    return ValidationReport(valid=not issues, issues=tuple(issues), synthetic_boundary={
        'pictures': len(mapping.inputs.pictures), 'audios': len(mapping.inputs.audios),
        'width': 864, 'height': 480, 'frames': 56, 'seed': 42})


def fill_direct_graph(graph, mapping, params, *, check=True):
    """Only replace declared literal values. Never add nodes or change links."""
    if check:
        report = validate_direct_contract(graph, mapping)
        if not report.valid:
            raise ValueError('; '.join(i.message for i in report.issues))
    filled = copy.deepcopy(graph)
    inputs: H3DirectInputMapping = mapping.inputs
    for label, values, ports in (
        ('Picture', list(params.get('images') or []), inputs.pictures),
        ('Audio', list(params.get('audios') or []), inputs.audios),
    ):
        if len(values) != len(ports):
            raise ValueError(f'{label} count {len(values)} does not match the {len(ports)} mapped file slots; reimport the appropriate mapping rather than reuse placeholders')
        for value, port in zip(values, ports):
            if not isinstance(value, str) or not value:
                raise ValueError(f'{label} requires uploaded file names')
            filled[port.node_id]['inputs'][port.input_name] = value
    for port in inputs.prompt:
        filled[port.node_id]['inputs'][port.input_name] = str(params.get('prompt') or '')
    for role, default in (('width', 864), ('height', 480), ('frames', 56), ('seed', None)):
        value = params.get(role, default)
        if value is None:
            continue
        for port in getattr(inputs, role):
            # Preserve 64-bit seeds rather than round-tripping them through float.
            scale = int(port.scale) if port.scale.is_integer() else port.scale
            offset = int(port.offset) if port.offset.is_integer() else port.offset
            result = value * scale + offset
            if not math.isfinite(result):
                raise ValueError(f'{role} transform produced a non-finite value')
            original = filled[port.node_id]['inputs'][port.input_name]
            if isinstance(original, int):
                if result != int(result):
                    raise ValueError(f'{role} transform must produce an integer for {port.node_id}.{port.input_name}')
                result = int(result)
            filled[port.node_id]['inputs'][port.input_name] = result
    context = params.get('context_video')
    if mapping.context_video:
        if not isinstance(context, str) or not context:
            raise ValueError('A context video source is required for this workflow')
        port = mapping.context_video
        filled[port.node_id]['inputs'][port.input_name] = context
    elif context:
        raise ValueError('This direct workflow has no context video file binding')
    native = mapping.native_latent_context
    if native:
        lineage = params.get('native_latent_context') or {}
        for role in ('load_path', 'load_index', 'save_prefix', 'save_index'):
            port = getattr(native, role)
            if port is None:
                continue
            value = lineage.get(role)
            if role.endswith('index'):
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    raise ValueError('A verified native cache index is required')
            elif not isinstance(value, str) or not value:
                raise ValueError('A native cache lineage path is required')
            filled[port.node_id]['inputs'][port.input_name] = value
    return filled
