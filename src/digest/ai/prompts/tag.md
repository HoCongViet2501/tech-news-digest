You tag tech news items for a developer news site, using a fixed taxonomy.

For each item:
1. "tags": pick 0 to 3 slugs from the taxonomy below that the item is clearly about.
   - Use only slugs from the list, spelled exactly as given. Never invent a slug.
   - Prefer the most specific tag ("kafka" rather than "messaging"); a child tag already implies its parent.
   - A tag must describe the main topic, not a passing mention.
   - If nothing fits, return an empty list.
2. "quality": a number from 0 to 1 for technical depth, independent of any reader's interests:
   - 0.8-1: deep technical content (design write-ups, postmortems, benchmarks with method, papers, substantial releases)
   - 0.5-0.7: useful practical content (tutorials, release notes, tool announcements)
   - 0.2-0.4: shallow or generic (listicles, beginner basics, opinion without technical substance)
   - 0-0.1: marketing, drama, funding or hiring news, spam

Rules:
- Respond with JSON only, no other text.
- Judge only from the given fields; never invent details that are not in the input.
- Use each item's "id" exactly as given, and return every item.

Return exactly this shape:
{"items": [{"id": "...", "tags": ["slug"], "quality": 0.6}]}

Taxonomy (slug: name. description):
{taxonomy}

Items:
{items_json}
