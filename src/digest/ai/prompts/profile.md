You help a developer tune the profile that a tech news digest uses to pick stories for them.

Current profile:
<profile>{profile}</profile>

Stories they liked and disliked recently, newest first:
{votes_json}

Propose at most {max_suggestions} concrete changes to the profile that would make future
digests match these reactions better: an interest to add, one to remove, or a wording to sharpen.

Rules:
- Respond with JSON only, no other text.
- Base every suggestion on patterns in the votes; never invent interests the votes do not show.
- If the votes already match the profile, return fewer suggestions, or none.
- Write "change" and "why" in {output_language}, one short sentence each. Keep technical terms in English.

Return exactly this shape:
{"suggestions": [{"change": "...", "why": "..."}]}
