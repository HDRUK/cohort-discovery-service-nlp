from rapidfuzz import fuzz
import re

DOWNSTREAM_TOKENS = {
    "due", "secondary", "complication", "associated",
    "stage", "chronic",  "disease", "failure", "disorder"
}

def tokenize(text):
    """
    Simple tokeniser: lowercase, remove punctuation, split on whitespace.
    """
    text = re.sub(r"[^\w\s]", " ", text)
    return set(text.lower().split())

class FuzzyConceptResolver:
    """
    Fuzzy matcher for resolving natural language text to standardised concepts.
    
    Performs multi-stage matching combining token-based overlap and fuzzy string similarity,
    with adjustable penalties for irrelevant tokens and domain-specific language patterns.
    
    Attributes:
    -----------
    concepts : list of dict
        Reference list of concepts to match against. Each dict should contain:
        - concept_name or description: text to match (one or both required)
        - concept_id, domain_id, etc.: additional metadata preserved in results
        - tokens (added during init): pre-tokenised form of concept text for fast lookup
    threshold : float
        Minimum fuzzy similarity score (0-100) required for a match (default: 70).
        Applied after penalty adjustments; can be overridden per-query.
    token_match_ratio : float
        Minimum fraction of input tokens that must overlap with concept tokens (default: 0.3).
        Controls token coverage requirement; higher values enforce stricter overlap.
    extra_token_penalty : float
        Penalty per extra concept token relative to query size (default: 0.3).
        Penalizes overly specific concepts; scaled by query length to normalize for longer inputs.
    """
    def __init__(self, concepts, threshold=70, token_match_ratio=0.3, extra_token_penalty=0.3):
        """
        Initialize resolver with concepts and matching parameters.
        
        Parameters:
        -----------
        concepts : list of dict
            List of concept dictionaries to match against.
        threshold : float, optional
            Minimum fuzzy similarity score (0-100) to consider a match. Default: 70.
        token_match_ratio : float, optional
            Fraction of tokens that must match (0.0-1.0). Default: 0.3.
        extra_token_penalty : float, optional
            Penalty per extra concept token. Default: 0.3.
        """
        self.threshold = threshold
        self.token_match_ratio = token_match_ratio
        self.extra_token_penalty = extra_token_penalty
        self.concepts = concepts

        # Pre-tokenise each concept's name first, description second
        for c in self.concepts:
            c["tokens"] = tokenize(c.get("concept_name") or c.get("description") or "")

    def resolve(self, text, threshold=None):
        """
        Fuzzy match input text against concepts and return ranked matches.
        
        Parameters:
        -----------
        text : str
            The input text to resolve against the concept list.
            Tokenised internally to extract key terms for matching.
        threshold : float, optional
            Minimum fuzzy similarity score (0-100) to consider a match.
            If None, defaults to the instance-level threshold set during initialization.
            Controls how strict the matching criteria are; lower values increase recall but decrease precision.
        
        Returns:
        --------
        list of dict
            Ranked list of matching concepts sorted by match_score (descending).
            Each concept dict includes all original fields plus a 'match_score' field.
            The score is adjusted by:
            - Token overlap ratio: requires significant overlap of tokens between input and concept
            - Extra token penalty: penalizes concepts with many irrelevant tokens relative to query size
            - Downstream token penalty: penalizes presence of complication/secondary language
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

            overlap = candidate_tokens & concept_tokens
            token_ratio = len(overlap) / max(len(candidate_tokens), 1)

            if token_ratio < self.token_match_ratio:
                continue  # skip concepts that don't cover enough tokens

            concept_text = concept.get("concept_name") or concept.get("description") or ""
            raw_score = fuzz.WRatio(text.lower(), concept_text.lower())

            if raw_score < threshold:
                continue

            score = raw_score
            
            """
            The following two blocks adjust the raw fuzzy score based on token analysis:
            1. Extra Token Penalty: If the concept contains many tokens not present in the
               input text, it suggests the concept is overly specific or contains irrelevant
               information. This penalty scales with the number of extra tokens relative to
               the size of the input text to normalise for longer queries.
            2. Downstream Token Penalty: Certain tokens indicate that the concept may be
               related to complications or secondary conditions rather than the primary
               concept being queried. Each occurrence of such tokens results in a fixed
               penalty to the score, discouraging matches that may not be directly relevant.

            This may need to be tuned, if we find it is over/under penalising certain concepts,
            as this can dramatically affect which concepts are returned as top matches, as well
            as alterantive matches, which may be significantly reduced by these penalties.
            """
            # Penalise surplus specificity gently, scaled by query size
            extra_tokens = concept_tokens - candidate_tokens
            score -= (len(extra_tokens) / max(len(candidate_tokens), 1)) * self.extra_token_penalty

            # Penalise downstream / complication language
            downstream_hits = concept_tokens & DOWNSTREAM_TOKENS
            score -= len(downstream_hits) * 1.5

            if score >= threshold:
                result = dict(concept)
                result["match_score"] = score
                results.append(result)

        # Sort by score descending
        results.sort(key=lambda x: x["match_score"], reverse=True)
        return results
