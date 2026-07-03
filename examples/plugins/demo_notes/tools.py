"""Demo notes plugin tools."""


def search_notes(arguments: dict) -> dict:
    """Return deterministic note matches for local plugin testing."""
    query = str(arguments.get("query", "")).strip()
    return {
        "query": query,
        "matches": [
            {
                "title": "Jordan meeting",
                "body": "Discuss project timeline and open questions.",
            }
        ],
    }

