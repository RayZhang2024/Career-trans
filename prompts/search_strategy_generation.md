Generate diverse public-web search strategies for job discovery. Return JSON only: an array of the supplied schema.

Use the separate `search_intent` object as the authoritative user's search request. Cover its themes and requested geography directly, including sensible geographic variants. Respect its company inclusion/exclusion, title exclusion, employment-type, and remote controls when shaping strategies. This is search context only: it is not candidate evidence or an eligibility claim. `max_results` is an execution budget and is intentionally not part of this object.

Generate search queries, not job claims. Cover distinct fronts such as exact roles, adjacent roles, domain roles, career/ATS pages, specialist recruiters or job boards, and geographic variants. Keep rationale short and task-level. Do not reveal chain-of-thought. Respect the requested maximum and avoid near-duplicates.
