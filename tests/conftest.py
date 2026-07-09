from unittest.mock import MagicMock

from app import app
from resolvers import MySQLConceptResolver


class _MinimalStore:
    synonym_map = {}
    synonym_token_index = {}
    name_token_index = {}
    acronym_index = {}
    ancestor_map = {}
    concepts_by_id = {}
    # Endpoint tests exercise the fully-warm path. Each capability has its own has_loaded_*
    # flag (the resolver gates per-flag now); reduced/partial-warm behaviour is covered
    # separately (see _ColdStore in test_resolver_performance_paths.py).
    resolver = object()
    has_loaded_core = True
    has_loaded_acronyms = True
    has_loaded_synonyms = True
    has_loaded_ancestors = True
    fully_warm = True

    async def get_resolver(self):
        return self.resolver


_store = _MinimalStore()

if not hasattr(app.state, "sql_resolver"):
    app.state.sql_resolver = MySQLConceptResolver(MagicMock(), _store)

if not hasattr(app.state, "backend"):
    app.state.backend = "sql"

app.state.resolver_store = _store
