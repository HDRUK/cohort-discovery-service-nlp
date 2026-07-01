from app import app
from resolvers import FallbackResolver, FuzzyConceptResolver, MySQLConceptResolver


class _MinimalStore:
    synonym_map = {}
    acronym_index = {}
    resolver = None

    async def get_resolver(self):
        return self.resolver


_store = _MinimalStore()
_store.resolver = FuzzyConceptResolver([])

if not hasattr(app.state, "sql_resolver"):
    app.state.sql_resolver = MySQLConceptResolver({}, _store)

if not hasattr(app.state, "fallback_resolver"):
    app.state.fallback_resolver = FallbackResolver(app.state.sql_resolver, _store)
