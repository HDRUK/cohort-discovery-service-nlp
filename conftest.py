import os

# Tests inject a local FuzzyConceptResolver via app.state.resolver_store.
# Force fuzzy mode so the extract endpoint uses that mock resolver instead of
# trying to open a real MySQL connection.
os.environ.setdefault("RESOLVER_BACKEND", "fuzzy")
