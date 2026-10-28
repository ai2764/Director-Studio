# Shot Workspace

Shot Workspace is an optional film authoring mode. Director Agent remains available as a separate project setting.

## Workflow

1. Add a shot and edit its brief, duration and camera fields.
2. Select Library references and add binding notes. New shots inherit the immediately preceding shot's image reference bindings, including selected files, picture indices and notes. Bindings can then be edited independently; the first shot starts without references.
3. Use the collapsible **Description assistant** to discuss filming or create/revise a description. Shot discussion, amendments and inline **Refine** always receive the current shot's selected reference descriptions and binding notes, plus the immediately previous shot description (up to 2,000 characters, marked when truncated). Previous-shot text is authored continuity context, not evidence of a generated video's ending. A small semantic intent decision distinguishes editing, discussion and unrelated chat; there is no exact-phrase gate. Discussion may retain one recent user/assistant pair (600 characters each), and loads the next shot description only when needed. Greetings and unrelated chat use only the latest request and current description for routing. No mode receives image pixels, generation prompts or the full project/chat history. Conversation records are stored separately per shot and restored across navigation, refreshes and service restarts; saving them does not expand model context. Discussion does not edit drafts. **Apply suggestion** explicitly requests a description change; **Undo description change** restores the previous local draft. Saving and prompt writing remain separate actions.
4. Write the prompt. The writer receives the current shot, selected Library descriptions and saved continuation settings; it does not receive chat history or inspect image pixels.
5. Review the prompt and generate video. Desktop places output beside the brief; mobile places video generation and output below it on the same Shots page, without a separate Production tab. Editing a draft immediately marks an existing prompt out of date; saved reference changes trigger a new prompt check. **Clear** clears the local brief, dialogue and camera fields while retaining references; **Save** persists the empty brief. **Write prompt** saves the current edits before writing. During preparation and writing, progress replaces stale-prompt warnings; if writing fails or is cancelled, unresolved warnings return. The previous saved prompt remains available until a new candidate passes validation.

Use one dialogue cue per line: `Speaker [Language]: exact spoken words`. Speaker attribution and wording are retained in the generated speech contract.

Five seconds is only the initial duration. The assistant can suggest timing from the action, dialogue delivery and camera movement, using the current duration as an editable baseline and honoring explicit timing requests. Applying its brief draft updates the duration field too; Undo restores the previous draft duration, and users can manually override it. Prompt edits that change timing save the revised duration before writing. Changing duration makes an existing prompt stale. Suggestions must remain positive and at most 120 seconds.

The assistant also accepts explicit requests to write or revise the six-section prompt. It saves the current brief before running the same durable, cancellable writer used by **Write prompt**. Requests that change shot action or camera intent first produce a matching brief update; wording-only prompt edits leave the brief intact. The writer receives the previous prompt and the current edit request, without replaying the conversation or re-reading images. Questions about prompts do not trigger writing. Failed or cancelled writing keeps the previous prompt, and any already saved brief remains saved.

Open **Voice references** in the shot to **Import voice**, select an imported sample, enter its **Speaker** name (matching dialogue cues), optionally add voice direction, and **Save voices**. Samples can be previewed before saving. Up to three H3-ready voices may be linked, with a combined duration of at most 15 seconds. Voice bindings and saved asset descriptions are supplied to the writer; changing bindings, descriptions or audio content invalidates its prompt. Rewrite after saving voices. Native source audio cannot be combined with these voice bindings. Save unsaved brief edits before changing voices.

Changing a brief, selected reference, Library description or continuation intent invalidates the saved prompt. Switching the generated variation or output of the same continuation source does not require rewriting. Video availability, resolution and frame compatibility are still checked before generation. Rule-based checks catch missing sections, reference indices and malformed tags; they do not certify visual quality.

## Library

Assets opens Library only. Import an image, enter its name and optional description, and click the wand to obtain an editable description suggestion. The same action is available when editing an existing asset. Only the selected image is read. Image content and guidance determine the cache key; changing the name or description requests a new refinement. Suggestions are saved only through the normal import/save action.

## Isolation

The backend implementation lives in `app/shot_workspace`; frontend components live in `features/shot-workspace`. Project authoring mode is persisted separately from production type. The normal Director Agent implementation is retained. Workspace writing uses bounded direct model calls and durable task receipts, including refresh recovery and cancellation.

Tests use synthetic images, generic objects and invented dialogue. Runtime data, personal projects and service configuration are not included.
