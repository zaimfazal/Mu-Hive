"""
src/agents/trend_detector.py
============================
Post-scoring trend clustering for the Mu-Hive pipeline.
Identifies items that multiple sources reported on, and marks them as trending.
"""

import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

# Minimum Jaccard similarity threshold for two titles to be considered "same topic"
SIMILARITY_THRESHOLD = 0.55

# Stopwords to strip from titles before comparison
_STOPWORDS = frozenset({
    "the", "a", "an", "is", "in", "on", "of", "and", "to", "for",
    "with", "at", "by", "from", "its", "it", "this", "that", "are",
    "was", "be", "has", "have", "had", "will", "can", "how", "what",
    "why", "new", "your", "you", "we", "our", "via",
})


def _title_words(title: str) -> set:
    """Extract meaningful lowercase word set from a title."""
    if not title:
        return set()
    words = set(title.lower().split()) - _STOPWORDS
    # Remove very short tokens (likely noise)
    return {w for w in words if len(w) > 2}


def _jaccard_similarity(set_a: set, set_b: set) -> float:
    """Compute Jaccard similarity between two word sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0


def _extract_domain(url: str) -> str:
    """Extract domain from URL for source diversity check."""
    if not url:
        return ""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        # Strip www. prefix
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


def detect_trends(digests: dict) -> dict:
    """
    Analyze planner output for trending topics.
    
    A topic is "trending" if multiple items across sources share similar titles,
    indicating the same event/news is being reported by multiple outlets.
    
    Mutates items in-place: adds 'trending' (bool) and 'trend_label' (str) keys.
    Returns the enhanced digests dict.
    """
    total_trending = 0

    for ig, categories in digests.items():
        # Collect all items across categories for this IG
        all_items = []
        for cat_name, items in categories.items():
            for item in items:
                all_items.append(item)

        if len(all_items) < 2:
            for item in all_items:
                item['trending'] = False
                item['trend_label'] = None
            continue

        # Build word sets for all titles
        title_data = []
        for item in all_items:
            words = _title_words(item.get('title', ''))
            domain = _extract_domain(item.get('link', ''))
            title_data.append((item, words, domain))

        # Find clusters of similar items
        clusters = []  # List of sets of indices
        assigned = set()

        for i in range(len(title_data)):
            if i in assigned:
                continue
            cluster = {i}
            for j in range(i + 1, len(title_data)):
                if j in assigned:
                    continue
                sim = _jaccard_similarity(title_data[i][1], title_data[j][1])
                if sim >= SIMILARITY_THRESHOLD:
                    # Only count as trending if from different domains
                    if title_data[i][2] != title_data[j][2]:
                        cluster.add(j)
            if len(cluster) > 1:
                clusters.append(cluster)
                assigned.update(cluster)

        # Mark trending items
        for cluster in clusters:
            # Build a trend label from the most common words
            all_words = set()
            sources = set()
            for idx in cluster:
                all_words.update(title_data[idx][1])
                sources.add(title_data[idx][2])

            # Pick the shortest title as the label (usually most concise)
            shortest_title = min(
                (title_data[idx][0].get('title', '') for idx in cluster),
                key=len
            )
            trend_label = f"🔥 {len(sources)} sources"

            for idx in cluster:
                title_data[idx][0]['trending'] = True
                title_data[idx][0]['trend_label'] = trend_label
                total_trending += 1

        # Mark non-trending items
        for i in range(len(title_data)):
            if i not in assigned:
                title_data[i][0]['trending'] = False
                title_data[i][0]['trend_label'] = None

    if total_trending > 0:
        logger.info(f"Trend detector found {total_trending} trending items across all IGs.")

    return digests
