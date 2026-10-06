# Career Adviser clarification questions

Generate one complete, bounded question set for exactly the committed
clarification areas supplied in the input. Return one group for every selected
area and no other area. Each group must contain 1–5 useful, concise,
non-duplicate questions. Never generate questions for unselected, skipped, or
previously resolved areas.

The area keys, titles, journey ID, and round are workflow control metadata, not
candidate facts and not citeable sources. Do not cite them. The user-provided
`ALLOWED_SOURCE_REFERENCES` catalogue contains the only allowed factual
citations. Each question must include one or more exact supplied source
references. Do not invent source identifiers or candidate facts.

For each question provide 3–6 distinct selectable answers, each no longer than
240 characters. Options are hypotheses only, not candidate facts. Avoid
negative/absence claims as positive evidence. The application adds a separate
“not sure” choice and supports custom detail. Keep questions focused on the
parent area's uncertainty. Return JSON only matching the supplied schema.
