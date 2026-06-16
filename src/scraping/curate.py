"""
src/scraping/curate.py
======================
Event processing: dedup, classification, priority scoring,
IG assignment, smart filling, and top-N selection.
"""

import re

from src.config.constants import IG_KEYWORDS, MASTER_IGS, RELATED_IGS, TOP_N

# ──────────────────────────────────────────────────────────
# 2B — RARE IGs (these and ONLY these can use extended pool)
# ──────────────────────────────────────────────────────────

RARE_IGS = [
    "Quantum Computing",
    "Beckn",
    "Space",
    "Civil",
    "Comics"
]


# IG_KEYWORDS is centralized in src/config/constants.py.

# ──────────────────────────────────────────────────────────
# 2D — assign_best_ig() WITH MuV STRICT GUARD
# ──────────────────────────────────────────────────────────

def _normalized_matching_text(event):
  """Build normalized multi-field text for IG matching."""
  tags = " ".join(event.get('tags', [])) if isinstance(event.get('tags'), list) else str(event.get('tags', ''))
  text = (
    f"{event.get('eventName', '')} "
    f"{event.get('description', '')} "
    f"{event.get('summary', '')} "
    f"{tags} "
    f"{event.get('platform', '')}"
  ).lower()
  return re.sub(r"[^a-z0-9+\s]", " ", text)


def _keyword_hit(text: str, keyword: str) -> bool:
  k = keyword.strip().lower()
  if not k:
    return False

  # Multi-word terms are checked as phrase matches; single-word terms as word boundaries.
  if " " in k or "+" in k:
    return k in text

  prefix_terms = {"eco"}
  if k in prefix_terms:
    return k in text

  return re.search(rf"\b{re.escape(k)}\b", text) is not None


def _compute_ig_scores(event, IG_KEYWORDS):
  text = _normalized_matching_text(event)
  scores = {}

  negative_signals = {
    "Cyber Security": {
      "terms": ["eco", "sustainable", "sustainability", "climate", "environment", "green", "social good"],
      "penalty": 2,
    }
  }

  for ig, keywords in IG_KEYWORDS.items():
    if ig == "General Tech":
      continue
    positive_score = sum(1 for keyword in keywords if _keyword_hit(text, keyword))
    penalty_conf = negative_signals.get(ig)
    if penalty_conf:
      negatives = penalty_conf["terms"]
      penalty = penalty_conf["penalty"] * sum(1 for term in negatives if _keyword_hit(text, term))
      positive_score = max(0, positive_score - penalty)
    scores[ig] = positive_score

  return text, scores


def _top_two_scores(scores: dict[str, int]) -> tuple[int, int]:
  ordered = sorted(scores.values(), reverse=True)
  top = ordered[0] if ordered else 0
  second = ordered[1] if len(ordered) > 1 else 0
  return top, second


def _is_confident_assignment(best_score: int, second_score: int) -> bool:
  """Keep assignments high precision: clear winner or clearly strong signal."""
  if best_score <= 0:
    return False
  if best_score >= 3:
    return True
  return (best_score - second_score) >= 1


def assign_best_ig(event, IG_KEYWORDS):
  text, scores = _compute_ig_scores(event, IG_KEYWORDS)

  best_ig = None
  best_score = 0
  for ig, score in scores.items():
    if score > best_score:
      best_score = score
      best_ig = ig

  # ── MuV STRICT GUARD ──────────────────────────────────
  # MuV = creative/media IG. Never assign tech events.
  if best_ig == "MuV":
    muv_required = [
      "media", "film", "story", "content creator",
      "creative", "video", "acting", "performance",
      "reel", "animation", "podcast", "photography",
      "stage", "theatre", "screenplay", "vlog",
      "filmmaking", "short film", "documentary"
    ]
    if not any(word in text for word in muv_required):
      best_ig = "General Tech"
      best_score = 0
  # ──────────────────────────────────────────────────────

  top_score, second_score = _top_two_scores(scores)
  if not _is_confident_assignment(top_score, second_score):
    return "General Tech", 0, scores

  if best_score <= 0:
    return "General Tech", 0, scores

  return best_ig, best_score, scores


def infer_event_type(event):
  """Infer the event type from title/description using the required priority order."""
  title = str(event.get("eventName", "")).lower()
  desc = str(event.get("description", "")).lower()
  tags = event.get("tags", [])
  if isinstance(tags, list):
    tags_text = " ".join(str(tag) for tag in tags).lower()
  else:
    tags_text = str(tags).lower()

  text = f"{title} {desc} {tags_text}"

  type_keywords = [
    ("Hackathon", ["hackathon", "hack sprint", "hackfest", "hack day", "buildathon", "sprint"]),
    ("Bootcamp", ["bootcamp", "boot camp"]),
    ("Workshop", ["workshop", "hands-on", "masterclass"]),
    ("Contest", ["contest", "competition", "challenge", "coding contest", "quiz"]),
  ]

  for event_type, keywords in type_keywords:
    if any(keyword in text for keyword in keywords):
      return event_type

  return "Hackathon"


def compute_score(event):
    score = 0
    days  = event.get("days_remaining", 999)

    # Date proximity
    if   days <= 3:  score += 100
    elif days <= 7:  score += 70
    elif days <= 10: score += 40
    else:            score += 20   # extended pool events

    # Event type
    etype = str(event.get("_event_type", event.get("eventType", ""))).lower()
    if   "hackathon" in etype: score += 80
    elif "bootcamp"  in etype: score += 50
    elif "contest"   in etype: score += 30
    elif "workshop"  in etype: score += 20

    # Location
    loc = event.get("location", "").lower()
    if "online" in loc or "virtual" in loc: score += 40
    elif "kerala" in loc:                   score += 35

    # Cost
    if "free" in event.get("cost", "").lower(): score += 30

    return score

def curate(primary_events, extended_events, IG_KEYWORDS):
  # ──────────────────────────────────────────────────────────
  # 2E — GLOBAL DEDUPLICATION (run before anything else)
  # ──────────────────────────────────────────────────────────

  unique_events = {}
  for event in list(primary_events or []) + list(extended_events or []):
    link = str(event.get("registrationLink", "")).strip()
    if not link or link in unique_events:
      continue
    unique_events[link] = event

  all_events = list(unique_events.values())

  # ──────────────────────────────────────────────────────────
  # 2F — SINGLE-PASS SCORING AND IG ASSIGNMENT
  # ──────────────────────────────────────────────────────────

  grouped = {ig: [] for ig in MASTER_IGS}

  for event in all_events:
    link = str(event.get("registrationLink", "")).strip()
    if not link:
      continue

    event_type = infer_event_type(event)
    event["eventType"] = event_type
    event["_event_type"] = event_type
    event["score"] = compute_score(event)
    event["_score"] = event["score"]
    if "days_remaining" in event and "_days_remaining" not in event:
      event["_days_remaining"] = event.get("days_remaining")
    if "days_remaining" in event and "_days_away" not in event:
      event["_days_away"] = event.get("days_remaining")

    ig, match_score, ig_scores = assign_best_ig(event, IG_KEYWORDS)

    event["_best_ig"] = ig
    event["_match_score"] = match_score
    event["_ig_scores"] = ig_scores
    grouped[ig].append(event)

  # ──────────────────────────────────────────────────────────
  # 2G — CONTROLLED RELATED-IG FALLBACK FOR EMPTY GROUPS
  # ──────────────────────────────────────────────────────────

  for target_ig in MASTER_IGS:
    if grouped[target_ig] or target_ig == "General Tech":
      continue

    for source_ig in RELATED_IGS.get(target_ig, []):
      source_events = grouped.get(source_ig, [])
      if len(source_events) <= 1:
        continue

      candidates = sorted(
        source_events,
        key=lambda event: (
          -event.get("_ig_scores", {}).get(target_ig, 0),
          event.get("days_remaining", 999),
          -event.get("score", 0),
        ),
      )

      moved = False
      for candidate in candidates:
        target_score = candidate.get("_ig_scores", {}).get(target_ig, 0)
        candidate_scores = candidate.get("_ig_scores", {})
        candidate_best = max(candidate_scores.values()) if candidate_scores else 0
        if target_score <= 0:
          continue
        if target_score < candidate_best:
          continue

        grouped[source_ig].remove(candidate)
        candidate["_best_ig"] = target_ig
        candidate["_match_score"] = target_score
        grouped[target_ig].append(candidate)
        moved = True
        break

      if moved:
        break

  # Secondary fallback: for still-empty IGs, borrow the closest relevant event globally.
  for target_ig in MASTER_IGS:
    if grouped[target_ig] or target_ig == "General Tech":
      continue

    global_candidates = []
    for source_ig, source_events in grouped.items():
      if source_ig != "General Tech":
        continue
      for event in source_events:
        target_score = event.get("_ig_scores", {}).get(target_ig, 0)
        candidate_scores = event.get("_ig_scores", {})
        candidate_best = max(candidate_scores.values()) if candidate_scores else 0
        if target_score > 0 and target_score == candidate_best:
          global_candidates.append((source_ig, event, target_score))

    if not global_candidates:
      continue

    source_ig, candidate, _ = sorted(
      global_candidates,
      key=lambda item: (
        -item[2],
        item[1].get("days_remaining", 999),
        -item[1].get("score", 0),
      ),
    )[0]

    grouped[source_ig].remove(candidate)
    grouped[target_ig].append(candidate)

  # ──────────────────────────────────────────────────────────
  # 2H — SORT AND CAP AT TOP 5 PER IG
  # ──────────────────────────────────────────────────────────

  for ig in MASTER_IGS:
    grouped[ig].sort(
      key=lambda x: (
        -x.get("_match_score", 0),
        -x.get("score", 0),
        x.get("days_remaining", 999),
      )
    )
    grouped[ig] = grouped[ig][:TOP_N]

  return grouped


def process_events(events):
  """Backward-compatible entry point for tests and older scripts."""
  from src.scraping.data_cleaner import clean_events

  primary, extended = clean_events(events)
  return curate(primary, extended, IG_KEYWORDS)
