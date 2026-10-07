import logging
from src.db.postgres_database import DatabaseFacade as Database
from src.config.interest_groups import registry, apply_ig_cap
from src.config.settings import MAX_IGS_PER_RUN

logger = logging.getLogger(__name__)

# Kept for backward compatibility (imported by legacy modules/tests).
# Active production paths enumerate registry.all_active_names() instead.
MVP_IGS = ["AI", "Data Science", "Web Development", "Cyber Security", "UI/UX"]

# Curated terminal preview limit per IG per category (unchanged).
PLANNER_PER_IG_LIMIT = 5

def plan_digests():
    """
    Queries database for the highest scored opportunities for every active
    configured Interest Group (primary and secondary memberships).
    Returns a dictionary mapping each IG to a dict of categories, each containing a list of items.
    Passes rich metadata to the communicator for proper formatting.
    """
    logger.info("Initializing Planner Agent...")
    configured_igs = registry.all_active_names()
    if not configured_igs:
        logger.error(
            "Planner found zero active Interest Groups in the registry; "
            "returning no digests (no defaults invented)."
        )
        return {}

    processed_igs, skipped_igs = apply_ig_cap(configured_igs, MAX_IGS_PER_RUN)
    if skipped_igs:
        logger.warning(
            "Planner capped to %d/%d IGs (MAX_IGS_PER_RUN=%s); skipped: %s",
            len(processed_igs), len(configured_igs), MAX_IGS_PER_RUN,
            ", ".join(skipped_igs),
        )
    logger.info(
        "Planner configured=%d eligible=%d capped=%d skipped=%d.",
        len(configured_igs), len(configured_igs),
        len(processed_igs), len(skipped_igs),
    )

    db = Database()
    digests = {}

    categories = ["News", "Hackathons"]

    for ig in processed_igs:
        opportunities_by_cat = {}
        for cat in categories:
            rows = db.get_top_opportunities_by_ig_and_category(ig, cat, limit=PLANNER_PER_IG_LIMIT)
            if rows:
                opportunities_by_cat[cat] = []
                for r in rows:
                    opportunities_by_cat[cat].append({
                        "title": r.get("title", "No Title"),
                        "summary": r.get("summary", ""),
                        "link": r.get("link", ""),
                        "score": r.get("quality_score", 0),
                        "source_engine": r.get("source_engine", r.get("source", "")),
                        "category": cat,
                        "created_at": r.get("created_at", None),
                        "structured_metadata": r.get("structured_metadata") or {
                            "Platform": r.get("data", {}).get("platform", "Unknown"),
                            "Location": r.get("data", {}).get("location", "Online"),
                            "Deadline": r.get("data", {}).get("endDate", "TBA")
                        },
                    })
        
        if opportunities_by_cat:
            digests[ig] = opportunities_by_cat
            total_items = sum(len(cat_list) for cat_list in opportunities_by_cat.values())
            logger.info(f"Planner selected {total_items} items for {ig}.")
            
    db.close()
    return digests
