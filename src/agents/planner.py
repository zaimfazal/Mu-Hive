import logging
from src.db.postgres_database import DatabaseFacade as Database

logger = logging.getLogger(__name__)

# Strictly enforced MVPs from Phase 3 Intelligence tagging
MVP_IGS = ["AI", "Data Science", "Web Development", "Cyber Security", "UI/UX"]

def plan_digests():
    """
    Queries database for the highest scored opportunities strictly mapping to each IG.
    Returns a dictionary mapping each IG to a dict of categories, each containing a list of items.
    Passes rich metadata to the communicator for proper formatting.
    """
    logger.info("Initializing Planner Agent...")
    db = Database()
    digests = {}
    
    categories = ["News", "Hackathons"]

    for ig in MVP_IGS:
        opportunities_by_cat = {}
        for cat in categories:
            rows = db.get_top_opportunities_by_ig_and_category(ig, cat, limit=5)
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
                    })
        
        if opportunities_by_cat:
            digests[ig] = opportunities_by_cat
            total_items = sum(len(cat_list) for cat_list in opportunities_by_cat.values())
            logger.info(f"Planner selected {total_items} items for {ig}.")
            
    db.close()
    return digests
