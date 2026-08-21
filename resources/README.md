# Resources

## Purpose

The `resources/` directory contains repository-level development resources.

It is **not** the storage location for production registered-user data.

---

# Directory Structure

```text
resources/
|
|-- README.md
|
|-- templates/
|
`-- examples/
    `-- ray_demo/
        |-- 01_candidate_profile.md
        |-- 02_master_career_evidence.md
        |-- 03_master_cv.md
        |-- 04_projects_portfolio.md
        |-- 05_skills_matrix.md
        |-- 06_career_strategy.md
        `-- 07_job_search_criteria.md
```

---

# `templates/`

Contains generic templates that can help define:

- candidate profile structure;
- career evidence;
- CV representation;
- project portfolio;
- skills matrix;
- career strategy;
- job-search criteria.

Templates must be user-agnostic.

Do not include real private user data in templates.

---

# `examples/`

Contains demo, fixture, or evaluation candidate profiles.

These are useful for:

- development;
- tests;
- manual evaluation;
- examples;
- regression checks.

Example profiles must never become hidden global assumptions in the product.

---

# Demo Candidate Policy

`examples/ray_demo/` contains one realistic development candidate.

It exists only to exercise the application.

The application must also work for users with very different profiles, such as:

- graduates;
- software engineers;
- career switchers;
- project managers;
- data professionals;
- researchers;
- senior leaders.

Do not tune shared scoring rules or prompts solely to the demo candidate.

---

# Production User Data

Real registered-user data must not be stored in this directory.

Production data should eventually be stored in:

```text
PostgreSQL
+
private file/object storage
```

depending on data type.

Examples:

### PostgreSQL

- candidate profiles;
- employment history;
- education;
- structured skills;
- career evidence;
- preferences;
- jobs;
- assessments;
- applications.

### Private file storage

- uploaded CVs;
- supporting documents;
- generated files where appropriate.

---

# User Ownership

Every user-owned production record should be associated with an authenticated user identifier.

Conceptually:

```text
user_id
```

must scope:

- profile;
- evidence;
- skills;
- projects;
- documents;
- jobs;
- assessments;
- applications.

---

# Suggested Generic Templates

The `templates/` directory may later include:

```text
candidate_profile.md
career_evidence.md
master_cv.md
projects_portfolio.md
skills_matrix.md
career_strategy.md
job_search_criteria.md
```

These templates should describe fields and expected structure rather than one person's information.

---

# Example Profile Structure

A demo candidate may have:

```text
examples/
`-- demo_candidate/
    |-- candidate_profile.md
    |-- career_evidence.md
    |-- master_cv.md
    |-- projects_portfolio.md
    |-- skills_matrix.md
    |-- career_strategy.md
    `-- job_search_criteria.md
```

Additional demo profiles are encouraged for evaluation.

---

# Evaluation Profiles

Over time, create synthetic or anonymised fixtures representing different user types.

Examples:

```text
examples/
|-- experienced_engineer/
|-- software_engineer/
|-- graduate/
|-- career_switcher/
`-- project_manager/
```

This helps detect accidental bias toward one profile shape.

---

# Important Rule

Repository resources are development assets.

They must never be confused with authenticated production user storage.
