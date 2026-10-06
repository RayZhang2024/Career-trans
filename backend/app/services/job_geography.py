"""Small, reviewed geography vocabulary for deterministic discovery filters."""

import re


UK_COUNTRY_ALIASES = {
    "uk", "united kingdom", "great britain",
}
UK_REGIONS = {"england", "scotland", "wales", "northern ireland"}
UK_LOCALITIES = {"london", "oxford", "cambridge", "bristol", "manchester", "reading"}
US_LOCALITIES = {"new york", "san francisco", "boston", "chicago", "seattle", "austin"}
INDIA_LOCALITIES = {"bengaluru", "bangalore", "mumbai", "delhi", "hyderabad"}
COUNTRY_ALIASES = {
    "uk": UK_COUNTRY_ALIASES,
    "us": {"us", "usa", "united states", "united states of america"},
    "india": {"india"},
}
GEOGRAPHY_ALIASES = {
    alias: country
    for country, aliases in COUNTRY_ALIASES.items()
    for alias in aliases
} | {locality: "uk" for locality in UK_LOCALITIES} | {region: f"uk_{region.replace(' ', '_')}" for region in UK_REGIONS}
GEOGRAPHY_ALIASES.update({locality: "us" for locality in US_LOCALITIES})
GEOGRAPHY_ALIASES.update({locality: "india" for locality in INDIA_LOCALITIES})


def tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.casefold())


def contains_phrase(haystack: list[str], phrase: list[str]) -> bool:
    return bool(phrase) and any(
        haystack[index:index + len(phrase)] == phrase
        for index in range(max(0, len(haystack) - len(phrase) + 1))
    )


def has_phrase(value: str, phrase: str) -> bool:
    return contains_phrase(tokens(value), tokens(phrase))


def detected_groups(value: str) -> set[str]:
    words = tokens(value)
    groups = {
        country
        for alias, country in GEOGRAPHY_ALIASES.items()
        if contains_phrase(words, tokens(alias))
    }
    if any(contains_phrase(words, tokens(region)) for region in UK_REGIONS):
        groups.add("uk")
    return groups


def is_uk_locality(value: str) -> bool:
    words = tokens(value)
    return any(contains_phrase(words, tokens(locality)) for locality in UK_LOCALITIES)


def geography_status(location: str | None, requested_locations: list[str]) -> str:
    """Return compatible, incompatible, or unknown without arbitrary substring guesses."""
    if not requested_locations:
        return "compatible"
    actual = location or ""
    if not tokens(actual):
        return "unknown"
    if any(has_phrase(actual, marker) for marker in ("worldwide", "global", "anywhere")):
        return "compatible"
    for requested in requested_locations:
        if has_phrase(actual, requested):
            return "compatible"
    requested_groups = set().union(*(detected_groups(item) for item in requested_locations))
    actual_groups = detected_groups(actual)
    # A country umbrella explicitly permits the reviewed UK locality-only set.
    uk_country_requested = any(has_phrase(item, alias) for item in requested_locations for alias in UK_COUNTRY_ALIASES)
    if uk_country_requested and ("uk" in actual_groups or is_uk_locality(actual)):
        return "compatible"
    if requested_groups and actual_groups:
        shared = requested_groups & actual_groups
        if not shared:
            return "incompatible"
        if shared == {"uk"}:
            requested_places = {place for place in UK_LOCALITIES if any(has_phrase(item, place) for item in requested_locations)}
            actual_places = {place for place in UK_LOCALITIES if has_phrase(actual, place)}
            requested_regions = {region for region in UK_REGIONS if any(has_phrase(item, region) for item in requested_locations)}
            actual_regions = {region for region in UK_REGIONS if has_phrase(actual, region)}
            if requested_places and actual_places:
                return "incompatible"
            if requested_regions and actual_regions:
                return "compatible" if requested_regions & actual_regions else "incompatible"
            return "unknown"
        return "compatible"
    return "unknown"


def result_location_affinity(text: str, requested_locations: list[str]) -> int:
    """Return a ranking hint from search snippets, never an eligibility decision."""
    if not requested_locations:
        return 0
    if any(has_phrase(text, requested) for requested in requested_locations):
        return 2
    requested_groups = set().union(*(detected_groups(item) for item in requested_locations))
    actual_groups = detected_groups(text)
    if "uk" in requested_groups and "uk" in actual_groups:
        return 1
    if requested_groups and actual_groups and not requested_groups & actual_groups:
        return -2
    return 0
