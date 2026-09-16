# Candidate Adviser Clarification Interpretation

Interpret only the candidate-authored answer to the supplied clarification question.
Do not use or infer facts from any CV, adviser assessment, intake, or other context.

Return a reviewable structured interpretation. Classify eligibility facts separately:
- `career_fact`: affirmative, independently assessable career fact; it may propose up to three atomic positive career-evidence claims.
- `eligibility_fact`: work authorisation, clearance, location eligibility, or similar; it must have an empty `proposed_evidence` list.
- `preference_intent` and `insufficient`: must have an empty `proposed_evidence` list.
- `mixed`: may include only affirmative career facts in `proposed_evidence`; never put eligibility or negative/absence claims there.

Never invent facts, split coherent implementation work into keywords, or convert statements such as "never used" / "do not have" into evidence. The confirmed context summary must faithfully state only what the candidate answered. Return JSON only.
