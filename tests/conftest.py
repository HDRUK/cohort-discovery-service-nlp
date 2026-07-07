from unittest.mock import MagicMock

from app import app
from resolvers import MySQLConceptResolver


class _MinimalStore:
    synonym_map = {}
    acronym_index = {}
    ancestor_map = {}
    concepts_by_id = {}
    resolver = None

    async def get_resolver(self):
        return self.resolver


_store = _MinimalStore()

if not hasattr(app.state, "sql_resolver"):
    app.state.sql_resolver = MySQLConceptResolver(MagicMock(), _store)

if not hasattr(app.state, "backend"):
    app.state.backend = "sql"

app.state.resolver_store = _store
