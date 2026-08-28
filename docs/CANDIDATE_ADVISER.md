# Candidate Understanding & Adviser Intake V1

## Purpose

Career Agent must understand more than an uploaded CV. Candidate Understanding & Adviser Intake V1 adds authenticated candidate-authored career information plus a reviewable semantic adviser assessment while preserving evidence-first job matching.

## Source hierarchy

```text
Confirmed CV/profile data
        +
Confirmed candidate intake
        |
        v
Semantic adviser assessment
        |
        +--> professional identity
        +--> strengths / transferable capabilities
        +--> development gaps
        +--> role hypotheses
        +--> open questions
        +--> career/search strategy summaries
```

The three layers have different authority:

1. **CV/profile/evidence** — factual candidate evidence used by requirement matching.
2. **Candidate intake** — candidate-authored goals, preferences, constraints, self-assessment and motivations.
3. **Adviser assessment** — reviewed semantic interpretation. It is never promoted into career evidence.

## Evidence boundary

`CandidateMatchingProfile` deliberately excludes adviser context. Semantic requirement matching continues to receive only the compact factual profile, skills and validated career evidence.

Confirmed adviser context is projected only into `CandidateSearchProfile` and `CandidateCareerProfile`, where it can improve search strategy and strategic career-alignment reasoning.

Candidate intake can also populate deterministic `CandidateEligibility` fields such as work authorisation, security clearances and valid work locations.

## Bounded semantic input

The adviser does not receive an unbounded persisted candidate record. Application code builds a typed `CandidateAdviserSourceContext` with bounded profile/strategy text, skills, eligibility and at most 40 career-evidence records. When more evidence exists, V1 retains a stable early-career and recent-career slice. Individual evidence text and skill lists are bounded as well.

Candidate-authored intake remains stored in full, while a deterministic bounded copy is supplied to the semantic adviser. This keeps persistence faithful to the user's source data while preventing accumulated CV/evidence history or unusually long intake fields from growing the model prompt indefinitely.

Only evidence IDs actually present in the bounded adviser payload are allow-listed for semantic source citations.

## Persistence

V1 adds one user-owned intake record and one user-owned adviser assessment record:

```text
CandidateIntakeProfile
- user_id (unique)
- structured_json
- revision
- confirmed
- confirmed_at

CandidateAdviserAssessmentRecord
- user_id (unique)
- structured_json
- input_fingerprint
- state: review_ready | confirmed
```

The intake API is idempotent for unchanged content: saving the same typed intake again preserves its revision and confirmation state. A material edit increments the revision and returns the intake to an unconfirmed state.

A deterministic SHA-256 fingerprint covers the exact **bounded semantic source payload** supplied to the adviser, together with the bounded confirmed intake. If a material candidate change alters what the adviser would actually see, the existing assessment is exposed as `stale` and is excluded from candidate context until reassessed and reconfirmed. Changes outside a deliberately truncated semantic budget do not invalidate an assessment because they did not change that assessment's input.

## API

```text
GET  /api/v1/career-adviser/intake
PUT  /api/v1/career-adviser/intake
POST /api/v1/career-adviser/intake/confirm

POST /api/v1/career-adviser/assess
GET  /api/v1/career-adviser/assessment
PATCH /api/v1/career-adviser/assessment
POST /api/v1/career-adviser/assessment/confirm
```

All routes are authenticated and derive ownership from `current_user.id`.

## Semantic contract

The adviser receives bounded confirmed candidate source context and bounded intake plus explicit allow-lists of career-evidence IDs and non-empty intake field paths. Returned source references are validated deterministically. Unknown references or unsupported positive findings fail closed rather than becoming candidate knowledge.

## V1 non-goals

- free-form persistent chat history;
- autonomous long-running adviser workflows;
- application-outcome learning;
- market-wide role-family analytics;
- automatic skills-development programmes;
- changes to requirement-matching or fit-score semantics;
- vector/embedding infrastructure.

A conversational adviser can be added later on top of these source, provenance and lifecycle contracts rather than replacing them.
