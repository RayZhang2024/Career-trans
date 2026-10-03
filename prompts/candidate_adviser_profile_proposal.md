Generate zero or more source-grounded structured Profile suggestions.

The only permitted basis for introducing a new factual claim is the affirmative
career evidence in `source.proposed_evidence`. Do not add facts from the question,
context summary, or current Profile. The current structured target catalogue is
provided only to decide whether an addition is useful and to identify an exact
item that may be refined. It is not evidence for unrelated claims. Items from a
section marked as truncated were not included in the catalogue and must not be
claimed as considered.

Do not invent facts or infer eligibility or preferences. Do not create negative or absence facts.
Do not turn Adviser strategy prose into Profile facts or create CareerEvidence.
Do not use Adviser history or outside knowledge.

Only propose items in employment, education, credentials, skills, projects, or
achievements. Use `add` for a genuinely new structured item and set its target
fingerprint to null. Use `replace_exact` only to refine one supplied catalogue
item in the same section. Copy that item's fingerprint exactly. Preserve
unchanged supported fields when refining an item. Never guess, fuzzy-match, or
merge targets, and do not output conflict-resolution decisions.

Return no more than six typed proposals. Returning zero proposals is valid when
the clarification does not support a useful structured Profile update. The
response must match the supplied JSON schema exactly.
