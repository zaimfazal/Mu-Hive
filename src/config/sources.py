# src/config/sources.py
# Curated list of trusted AI & tech RSS feeds for the Scout Agent.
# Note: feedparser.USER_AGENT is configured globally in the agents to prevent
# blocking from sites like openai.com and blog.google.

AI_RSS_FEEDS = [
    # --- Major Tech & AI News ---
    "https://openai.com/news/rss.xml",
    "https://huggingface.co/blog/feed.xml",
    "https://blog.google/technology/ai/rss/",
    "https://deepmind.google/blog/rss.xml",
    "https://blogs.nvidia.com/feed/",
    "https://blogs.nvidia.com/blog/category/deep-learning/feed/",
    "https://www.theverge.com/rss/index.xml",
    "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "https://techcrunch.com/tag/artificial-intelligence/feed/",
    "https://www.technologyreview.com/topic/artificial-intelligence/feed/",
    "https://planet-ai.net/rss.xml",

    # --- Research & Academic ---
    "https://rss.arxiv.org/rss/cs.AI",
    "https://rss.arxiv.org/rss/cs.LG",
    "https://rss.arxiv.org/rss/cs.CL",
    "https://www.amazon.science/index.rss",
    "https://blog.ml.cmu.edu/feed/",
    "https://lilianweng.github.io/lil-log/feed.xml",
    "https://jalammar.github.io/feed.xml",

    # --- Aggregated / Community ---
    "https://hnrss.org/frontpage?q=AI",
    "https://hnrss.org/best",  # Community-curated best stories — high signal
]


WEB_DEV_RSS_FEEDS = [
    "https://blog.pragmaticengineer.com/rss/",
    "https://github.blog/engineering/feed/",
    "https://slack.engineering/feed",
    "https://devblogs.microsoft.com/feed/",
    "https://aws.amazon.com/blogs/aws/feed/",
    "https://www.producthunt.com/feed",
    "https://hnrss.org/launches",
    "https://hnrss.org/show",
    "https://www.ycombinator.com/blog/feed",
    "https://techcrunch.com/feed/",
    "https://css-tricks.com/feed/",
    "https://smashingmagazine.com/feed/",
    "https://frontendfoc.us/rss",
    "https://davidwalsh.name/feed"
]

UI_UX_RSS_FEEDS = [
    "https://uxdesign.cc/feed",
    "https://www.awwwards.com/blog/feed/",
    "https://uxplanet.org/feed"
]

CYBER_SEC_RSS_FEEDS = [
    "https://krebsonsecurity.com/feed/",
    "https://thehackernews.com/feeds/posts/default"
]

DATA_SCIENCE_RSS_FEEDS = [
    "https://medium.com/feed/kaggle-blog",
    "https://towardsdatascience.com/feed",
    "https://www.kdnuggets.com/feed"
]

ALL_RSS_FEEDS = {
    "AI": AI_RSS_FEEDS,
    "Web Development": WEB_DEV_RSS_FEEDS,
    "UI/UX": UI_UX_RSS_FEEDS,
    "Cyber Security": CYBER_SEC_RSS_FEEDS,
    "Data Science": DATA_SCIENCE_RSS_FEEDS,
}

# Source priority boost for quality scoring.
# Added to the LLM quality_score (capped at 10) to prioritize trusted sources.
SOURCE_PRIORITY = {
    "RSS": 2,           # Curated RSS feeds — highest trust
    "Tavily": 0,        # Tavily search — moderate trust
    "DuckDuckGo": 0,    # DuckDuckGo search — no boost
    "API": 1,           # Hackathon APIs — direct source
    "Firecrawl": 1,     # Firecrawl scraped — direct source
}

