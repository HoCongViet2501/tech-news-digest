You review dependency release notes for a developer with this profile:
<profile>{profile}</profile>

Each release is for a library they use; "installed" is the version they declare.
For each release, using only its "notes":
- "summary": 1-2 sentences on what changes for them when upgrading from "installed".
- "action_required": true if upgrading needs code or config changes (breaking changes, removed or renamed APIs, a new minimum runtime version); false if it is a drop-in upgrade. If the notes do not say, use true when "major" is true and false otherwise.

Rules:
- Respond with JSON only, no other text.
- Write "summary" in {output_language}. Keep technical terms, API names and version numbers in English.
- Never invent details that are not present in the notes. If the notes are empty or thin, say less.
- Use each release's "id" exactly as given.

Return exactly this shape:
{"items": [{"id": "...", "summary": "...", "action_required": true}]}

Releases:
{items_json}
