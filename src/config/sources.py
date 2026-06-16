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
    "https://uxplanet.org/feed",
    # --- Added: balance coverage (3 → 8 feeds) ---
    "https://www.nngroup.com/feed/rss/",          # Nielsen Norman Group — UX research gold standard
    "https://alistapart.com/main/feed/",           # A List Apart — web design standards & best practices
    "https://designmodo.com/feed/",                # Designmodo — design tools, trends, tutorials
    "https://www.interaction-design.org/literature/feed",  # IxDF — interaction design articles
    "https://uxmovement.com/feed/",                # UX Movement — UI patterns and usability
]

CYBER_SEC_RSS_FEEDS = [
    "https://krebsonsecurity.com/feed/",
    "https://thehackernews.com/feeds/posts/default",
    # --- Added: balance coverage (2 → 8 feeds) ---
    "https://www.bleepingcomputer.com/feed/",      # BleepingComputer — breach reporting & malware
    "https://www.darkreading.com/rss.xml",         # Dark Reading — enterprise security analysis
    "https://www.securityweek.com/feed/",          # SecurityWeek — CVE coverage & breach reports
    "https://feeds.feedburner.com/eset/blog",      # ESET — threat research & malware analysis
    "https://www.cisa.gov/news.xml",               # CISA — US government security advisories
    "https://blog.qualys.com/feed",                # Qualys — vulnerability research
]

DATA_SCIENCE_RSS_FEEDS = [
    "https://medium.com/feed/kaggle-blog",
    "https://towardsdatascience.com/feed",
    "https://www.kdnuggets.com/feed",
    # --- Added: balance coverage (3 → 8 feeds) ---
    "https://blog.dataiku.com/feed",               # Dataiku — enterprise data science & MLOps
    "https://databricks.com/feed",                 # Databricks — lakehouse, Spark, data engineering
    "https://aws.amazon.com/blogs/big-data/feed/", # AWS Big Data — cloud data engineering
    "https://blogs.sas.com/content/feed/",         # SAS — analytics industry insights
    "https://neptune.ai/blog/feed",                # Neptune.ai — MLOps & experiment tracking
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

# First-party domains that get an extra scoring boost.
# Items from these domains are more likely to be original announcements.
FIRST_PARTY_DOMAINS = [
    "openai.com",
    "deepmind.google",
    "blog.google",
    "blogs.nvidia.com",
    "huggingface.co",
    "anthropic.com",
    "meta.ai",
    "ai.meta.com",
    "github.blog",
    "devblogs.microsoft.com",
    "aws.amazon.com",
    "cloud.google.com",
    "figma.com",
    "cisa.gov",
]
