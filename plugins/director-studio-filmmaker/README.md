# Director Studio Filmmaker

A conversational filmmaking workflow for Director Studio JSON Production. It
helps ChatGPT move from an early idea or script through story development,
storyboarding, visual asset and Layout planning, reference casting, and validated
production JSON.

## Start in ChatGPT

Install the plugin and mention `@director-studio-filmmaker` in a new conversation.
Your first message can be as simple as:

> Let's keep this short. Mia rides a rocket with a rabbit crew. Walk me through
> making it for Director Studio JSON Production.

You do not need a fixed opening sentence, and you do not need to paste the JSON
schema or the complete workflow into the chat. The skill asks a compact group of
high-level questions, supplies director defaults, and quickly returns concrete
creative work for revision. One approved Asset Plan authorizes the listed image
generation sequence; each result is visually checked before production continues.
Keep the production in one conversation so approvals and phase state remain
available. If ChatGPT stops following the phase gates, explicitly mention
`@director-studio-filmmaker` again on the next production decision.
The Director Studio Ref2VA prompt contract is bundled with the plugin, so regular
ChatGPT does not need a separate prompt-writing skill installed for JSON export.

## Workflow

1. Develop and approve the story and script.
2. Direct and approve the shot-by-shot storyboard.
3. Audit reusable assets and shot-specific Layouts.
4. Create and approve missing visual references.
5. Cast ordered Picture and Audio slots for each shot.
6. Export production-ready Director Studio JSON on explicit request.

## Use in regular ChatGPT Chat

To use ordinary Chat rather than Work/Codex, create a ChatGPT Project and paste
[`CHATGPT_PROJECT_INSTRUCTIONS.md`](CHATGPT_PROJECT_INSTRUCTIONS.md) into Project
Instructions. Add these files as Project Sources:

- `skills/director-studio-filmmaker/references/visual-assets.md`
- `skills/director-studio-filmmaker/references/production-json-contract.md`
- `skills/director-studio-filmmaker/references/h3-ref2va-contract.md`

Start a new **Chat** conversation inside that Project. No plugin mention or fixed
opening sentence is required.
