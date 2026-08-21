# Job Search Criteria

## Purpose

This file defines job-search filters, preferences, and evaluation criteria.

Some items are hard constraints. Others are preferences and should influence scoring rather than automatically reject a role.

This file should be updated whenever career priorities change.

---

# 1. Geography

## Preferred

- United Kingdom
- London
- Oxford
- Oxfordshire
- Reading
- Cambridge
- Hybrid roles within reasonable travel distance
- Remote UK roles

## Consider

- Other UK locations for sufficiently strong opportunities
- International roles where relocation / visa situation is attractive and practical

Career Agent should not automatically reject a role outside the preferred area if strategic value is unusually high.

---

# 2. Work Arrangement

Preferred:

1. Hybrid
2. Remote / flexible
3. On-site where the role is sufficiently attractive

The candidate has significant experience with laboratory/experimental environments and can consider on-site technical roles, but flexibility is valuable.

---

# 3. Role Families

## High Priority

- AI Solutions Engineer
- Applied AI Engineer
- Forward-Deployed Engineer
- Deployed Engineer
- Scientific Software Engineer
- Engineering Software Engineer
- Data / AI Engineer
- Technical Solutions Engineer
- Technical implementation roles
- AI/data roles in finance

## Medium / Conditional

- Structural Integrity Engineer
- Simulation Engineer
- Digital Engineering
- Project Manager
- Technical Product roles
- Technical Consultant
- Research Software Engineer
- Data Analyst / AI Analyst

## Lower Priority

- Narrow academic postdoctoral roles
- Pure laboratory roles
- Routine research roles with limited software/data growth
- General junior developer roles that fail to value prior experience
- Primarily administrative project roles

---

# 4. Industry Preferences

Attractive industries include:

- AI / software
- engineering technology
- advanced manufacturing
- energy
- fusion
- aerospace
- defence technology where eligible
- scientific computing
- financial services / investment
- industrial software
- data / analytics
- deep tech
- R&D technology companies

Industry is a preference rather than an absolute constraint.

---

# 5. Technical Content Preferences

Prefer roles that include several of:

- Python
- AI / LLMs
- agents
- data
- machine learning
- software engineering
- APIs
- customer-facing technical work
- deployment
- cloud
- engineering problem solving
- scientific computing
- simulation
- product development

High strategic value if the role provides production experience in areas currently developing:

- React
- FastAPI
- cloud
- deployment
- Docker
- CI/CD
- databases
- authentication
- production AI

---

# 6. Seniority

Preferred target level:

- experienced individual contributor;
- senior technical specialist where domain transfer is credible;
- solutions / applied engineering roles valuing prior professional experience;
- technical leadership roles.

Use caution with:

- graduate roles;
- internships;
- roles requiring many years of production software engineering where no equivalent transfer is accepted;
- very senior software architecture roles without sufficient production software evidence.

Do not infer seniority solely from title.

---

# 7. Compensation

Compensation should be evaluated when information is available.

Career Agent should consider:

- base salary;
- bonus;
- equity;
- pension;
- flexibility;
- future compensation trajectory;
- skill development;
- brand / market value.

No hard salary threshold is encoded yet.

If salary is absent, do not invent it. Research current market information if needed.

---

# 8. Work Authorisation and Security

The candidate currently holds UK Global Talent immigration status.

Career Agent must separately check for:

- citizenship requirements;
- security-clearance eligibility;
- export-control restrictions;
- residency requirements;
- nationality restrictions;
- role-specific government/defence requirements.

A potentially incompatible requirement should be labelled as:

**Eligibility risk — verify**

unless confirmed as a hard blocker.

---

# 9. Desired Job Characteristics

High-value characteristics:

- technical ownership;
- meaningful problem solving;
- real customer/user impact;
- software or AI delivery;
- measurable outcomes;
- cross-functional work;
- learning opportunity;
- strong technical colleagues;
- scope to build reusable systems;
- exposure to deployment / production;
- increasing responsibility;
- marketable skills.

---

# 10. Undesirable Job Characteristics

Penalise roles that appear dominated by:

- repetitive operations;
- narrow laboratory execution;
- limited ownership;
- low technical complexity;
- excessive bureaucracy with little delivery;
- very narrow specialisation with weak external marketability;
- project coordination without technical involvement;
- roles whose main attraction is title rather than work content.

---

# 11. Application Thresholds

Initial heuristic:

### APPLY — Priority

Typical characteristics:

- fit score >= 80; or
- fit score >= 70 with very high career alignment;
- no confirmed hard blocker;
- strong evidence for several core requirements.

### CONSIDER

Typical characteristics:

- fit score ~60–79;
- moderate-to-high strategic value;
- gaps appear learnable or positioning-related.

### SKIP

Typical characteristics:

- fit score < 60;
- major essential requirements unsupported;
- hard eligibility blocker;
- low career alignment;
- poor opportunity cost.

These thresholds are starting heuristics and should later be calibrated using real application outcomes.

---

# 12. Gap Classification

Each gap should be assigned one category.

## Hard blocker

Examples:

- mandatory citizenship not held;
- required professional licence not available;
- mandatory language requirement absent;
- essential clearance condition cannot be met.

## Meaningful capability gap

Example:

- role requires extensive production Kubernetes experience and candidate has none.

## Learnable gap

Example:

- one framework or cloud service that can reasonably be learned.

## Evidence gap

Candidate may possess transferable capability but the CV/resources do not yet provide strong evidence.

## Positioning gap

Experience exists but is described in language that does not map well to the target role.

---

# 13. Search Strategy

Job discovery should eventually search across:

- company career pages;
- LinkedIn;
- specialist recruiters;
- technology companies;
- engineering/deep-tech employers;
- finance employers;
- public research/technology organisations;
- relevant job boards.

The system should:

1. discover;
2. deduplicate;
3. apply hard filters;
4. perform fast screening;
5. deeply analyse only promising roles;
6. rank;
7. produce a shortlist.

---

# 14. Decision Principle

Career Agent should optimise for:

> probability of success × quality of opportunity × future career value

rather than simply:

> similarity to current CV.
