from app import load_concepts_from_mysql
from fuzzy_concept_resolver import FuzzyConceptResolver

class ResolverStore:
    def __init__(self, concepts_path=None):
        self.resolver = FuzzyConceptResolver(load_concepts_from_mysql())

    async def get_resolver(self):
        return self.resolver