from rapidfuzz import fuzz
import math
import os
import re

DOWNSTREAM_TOKENS = {
    "due", "secondary", "complication", "associated",
    "stage", "chronic",  "disease", "failure", "disorder"
}

def normalise_text(text):
    """
    Light normalisation to reduce token fragmentation without heavy mapping.
    """
    text = text.lower().strip()
    text = re.sub(r"[/\-]", " ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def tokenise(text):
    """
    Tokeniser: normalise, split into unigrams, add short phrases (2-3 grams).
    """
    text = normalise_text(text)
    tokens = text.split()
    unigrams = set(tokens)
    phrases = set()
    for n in (2, 3):
        for i in range(len(tokens) - n + 1):
            phrases.add(" ".join(tokens[i:i + n]))
    return unigrams, phrases

def fuzzy_token_overlap(candidate_tokens, concept_tokens, min_score=90):
    if not candidate_tokens or not concept_tokens:
        return 0.0
    matched = 0
    for candidate_token in candidate_tokens:
        best = 0
        for concept_token in concept_tokens:
            score = fuzz.ratio(candidate_token, concept_token)
            if score > best:
                best = score
                if best >= min_score:
                    break
        if best >= min_score:
            matched += 1
    return matched / max(len(candidate_tokens), 1)

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
        Penalises overly specific concepts; scaled by query length to normalise for longer inputs.
    """
    def __init__(self, concepts, threshold=70, token_match_ratio=0.3, extra_token_penalty=0.3, phrase_first=True):
        """
        Initialise resolver with concepts and matching parameters.
        
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
        phrase_first : bool, optional
            If True, prefer phrase overlap for token ratio when available.
        """
        self.threshold = threshold
        self.token_match_ratio = token_match_ratio
        self.extra_token_penalty = extra_token_penalty
        self.phrase_first = phrase_first
        self.concepts = concepts
        self.log_matches = os.getenv("LOG_RESOLVER_MATCHES", "false").lower() in {"1", "true", "yes", "on"}
        self.log_match_limit = int(os.getenv("LOG_RESOLVER_MATCH_LIMIT", 50))
        self.fuzzy_token_overlap = os.getenv("FUZZY_TOKEN_OVERLAP", "true").lower() in {"1", "true", "yes", "on"}
        self.fuzzy_token_min_score = int(os.getenv("FUZZY_TOKEN_MIN_SCORE", 85))
        self.collection_boost_weight = float(os.getenv("COLLECTION_BOOST_WEIGHT", 1.5))
        self.max_matches = None
        raw_max_matches = int(os.getenv("RESOLVER_MAX_MATCHES", 5))
        if raw_max_matches:
            try:
                parsed_max = raw_max_matches
                if parsed_max > 0:
                    self.max_matches = parsed_max
            except ValueError:
                print(f"[FuzzyConceptResolver] Invalid RESOLVER_MAX_MATCHES='{raw_max_matches}', ignoring.")

        # Pre-tokenise each concept's name first, description second
        for c in self.concepts:
            unigrams, phrases = tokenise(c.get("concept_name") or c.get("description") or "")
            c["tokens"] = unigrams
            c["phrase_tokens"] = phrases

    def resolve(self, text, threshold=None, phrase_first=None):
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
        phrase_first : bool, optional
            If True, prefer phrase overlap for token ratio when available.
            Defaults to the instance-level setting.
        
        Returns:
        --------
        list of dict
            Ranked list of matching concepts sorted by match_score (descending).
            Each concept dict includes all original fields plus a 'match_score' field.
            The score is adjusted by:
            - Token overlap ratio: requires significant overlap of tokens between input and concept
            - Extra token penalty: penalises concepts with many irrelevant tokens relative to query size
            - Downstream token penalty: penalises presence of complication/secondary language
        """
        if not text or not self.concepts:
            return []

        if threshold is None:
            threshold = self.threshold
        if phrase_first is None:
            phrase_first = self.phrase_first

        candidate_unigrams, candidate_phrases = tokenise(text)
        candidate_norm = normalise_text(text)
        candidate_token_count = len(candidate_unigrams)
        results = []
        logged = 0

        if self.log_matches:
            print(
                f"Resolving candidate '{text}' against {len(self.concepts)} concepts "
                f"(log limit={self.log_match_limit})"
            )

        for concept in self.concepts:
            concept_tokens = concept["tokens"]
            concept_phrases = concept.get("phrase_tokens", set())
            # Ensure significant overlap
            if not concept_tokens:
                continue

            if phrase_first and candidate_phrases and concept_phrases:
                overlap = candidate_phrases & concept_phrases
                token_ratio = len(overlap) / max(len(candidate_phrases), 1)
            else:
                if self.fuzzy_token_overlap:
                    token_ratio = fuzzy_token_overlap(
                        candidate_unigrams,
                        concept_tokens,
                        min_score=self.fuzzy_token_min_score,
                    )
                else:
                    overlap = candidate_unigrams & concept_tokens
                    token_ratio = len(overlap) / max(len(candidate_unigrams), 1)

            if self.log_matches and logged < self.log_match_limit:
                concept_text = concept.get("concept_name") or concept.get("description") or ""
                concept_norm = normalise_text(concept_text)
                raw_score = fuzz.WRatio(candidate_norm, concept_norm)
                if candidate_token_count <= 2:
                    raw_score = max(raw_score, fuzz.partial_ratio(candidate_norm, concept_norm))

                score = raw_score
                extra_tokens = concept_tokens - candidate_unigrams
                score -= (len(extra_tokens) / max(len(candidate_unigrams), 1)) * self.extra_token_penalty
                downstream_hits = concept_tokens & DOWNSTREAM_TOKENS
                score -= len(downstream_hits) * self.extra_token_penalty

                token_ok = token_ratio >= self.token_match_ratio
                raw_ok = raw_score >= threshold
                score_ok = score >= threshold and token_ok and raw_ok
                print(
                    f"Checking candidate '{text}' vs concept_id={concept.get('concept_id')}, "
                    f"concept_name='{concept_text}', token_ratio={token_ratio:.3f}, "
                    f"raw_score={raw_score:.2f}, final_score={score:.2f}, "
                    f"token_ok={token_ok}, raw_ok={raw_ok}, score_ok={score_ok}"
                )
                logged += 1

            if token_ratio < self.token_match_ratio:
                continue  # skip concepts that don't cover enough tokens

            concept_text = concept.get("concept_name") or concept.get("description") or ""
            concept_norm = normalise_text(concept_text)
            raw_score = fuzz.WRatio(candidate_norm, concept_norm)
            if candidate_token_count <= 2:
                # Short queries benefit from partial matching against longer concept text.
                raw_score = max(raw_score, fuzz.partial_ratio(candidate_norm, concept_norm))

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
            extra_tokens = concept_tokens - candidate_unigrams
            score -= (len(extra_tokens) / max(len(candidate_unigrams), 1)) * self.extra_token_penalty

            # Penalise downstream / complication language
            downstream_hits = concept_tokens & DOWNSTREAM_TOKENS
            score -= len(downstream_hits) * self.extra_token_penalty

            # Boost concepts that appear in multiple collections
            ncollections = concept.get("ncollections") or 0
            if ncollections > 1 and self.collection_boost_weight > 0:
                score += math.log(ncollections) * self.collection_boost_weight

            if score >= threshold:
                result = dict(concept)
                result["match_score"] = score
                results.append(result)

        # Sort by score descending
        results.sort(key=lambda x: x["match_score"], reverse=True)
        if self.max_matches:
            results = results[: self.max_matches]
        if self.log_matches and logged >= self.log_match_limit:
            print(f"Resolver concept logging truncated at {self.log_match_limit} concepts")
        return results
