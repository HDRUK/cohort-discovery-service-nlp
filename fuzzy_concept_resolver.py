from rapidfuzz import fuzz
import re

def tokenize(text):
    """
    Simple tokenizer: lowercase, remove punctuation, split on whitespace.
    """
    text = re.sub(r"[^\w\s]", " ", text)
    return set(text.lower().split())

class FuzzyConceptResolver:
    def __init__(self, concepts, threshold=90, token_match_ratio=0.4):
        """
        concepts: list of dicts, each having keys like concept_name, concept_id, domain_id, etc.
        threshold: minimum fuzzy similarity to consider a match
        token_match_ratio: fraction of tokens that must match candidate tokens
        """
        self.threshold = threshold
        self.token_match_ratio = token_match_ratio
        self.concepts = concepts

        # Pre-tokenize each concept's description and name
        for c in self.concepts:
            c["tokens"] = tokenize(c.get("description") or c.get("concept_name") or "")

    def resolve(self, text, threshold=None):
        """
        Returns a list of matching concepts with score.
        """
        if not text or not self.concepts:
            return []

        if threshold is None:
            threshold = self.threshold

        candidate_tokens = tokenize(text)
        results = []

        for concept in self.concepts:
            concept_tokens = concept["tokens"]
            # Ensure significant overlap
            if not concept_tokens:
                continue

            token_overlap = len(candidate_tokens & concept_tokens)
            token_ratio = token_overlap / max(len(candidate_tokens), 1)

            if token_ratio < self.token_match_ratio:
                continue  # skip concepts that don't cover enough tokens

            # Compute fuzzy score on full candidate text vs concept description/name
            concept_text = (concept.get("description") or concept.get("concept_name") or "")
            score = fuzz.WRatio(text.lower(), concept_text.lower())

            if score >= threshold:
                result = dict(concept)
                result["match_score"] = score
                results.append(result)

        # Sort by score descending
        results.sort(key=lambda x: x["match_score"], reverse=True)
        return results
