# Shot Workspace

Shot Workspace is an optional film authoring mode. Director Agent remains available as a separate project setting.

## Workflow

1. Add a shot and edit its brief, duration and camera fields.
2. Select Library references and add binding notes. References are never selected automatically.
3. Use the collapsible **Description assistant** to create or revise a description. Each request sends only the current description and the latest user instruction, plus the clip duration as a host constraint. Earlier conversation, Library references and other shots are excluded. The latest instruction takes priority over conflicting old content. Results update the editable draft; **Undo description change** restores its prior version. This assistant never writes generation prompts or saves a draft automatically.
4. Write the prompt. The writer receives the current shot, selected Library descriptions and saved continuation settings; it does not receive chat history or inspect image pixels.
5. Review the prompt and generate video. Desktop places output beside the brief; mobile retains the Production view.

Use one dialogue cue per line: `Speaker [Language]: exact spoken words`. Speaker attribution and wording are retained in the generated speech contract.

Changing a brief, selected reference, Library description or continuation intent invalidates the saved prompt. Switching the generated variation or output of the same continuation source does not require rewriting. Video availability, resolution and frame compatibility are still checked before generation. Rule-based checks catch missing sections, reference indices and malformed tags; they do not certify visual quality.

## Library

Assets opens Library only. Import an image, enter its name and optional description, and click the wand to obtain an editable description suggestion. The same action is available when editing an existing asset. Only the selected image is read. Image content and guidance determine the cache key; changing the name or description requests a new refinement. Suggestions are saved only through the normal import/save action.

## Isolation

The backend implementation lives in `app/shot_workspace`; frontend components live in `features/shot-workspace`. Project authoring mode is persisted separately from production type. The normal Director Agent implementation is retained. Workspace writing uses bounded direct model calls and durable task receipts, including refresh recovery and cancellation.

Tests use synthetic images, generic objects and invented dialogue. Runtime data, personal projects and service configuration are not included.
