"""
src/config/constants.py
=======================
Central configuration for the Mu-Hive hackathon aggregation pipeline.
Network and pipeline constants live here.
Import from this module — never hardcode values elsewhere.
"""

# ---------------------------------------------------------------------------
# HTTP / Network
# ---------------------------------------------------------------------------

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

CONCURRENCY_LIMIT: int = 50   # asyncio.Semaphore size
SESSION_TIMEOUT: int = 45     # aiohttp ClientTimeout (seconds)

# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

DEVFOLIO_API = "https://api.devfolio.co/api/search/hackathons"
UNSTOP_API = "https://unstop.com/api/public/opportunity/search-result"
DEVPOST_API = "https://devpost.com/api/hackathons?status[]=upcoming&status[]=open"
HACKEREARTH_API = "https://www.hackerearth.com/api/events/upcoming/"

# ---------------------------------------------------------------------------
# Pipeline Quality Rules
# ---------------------------------------------------------------------------

TOP_N: int = 5           # Maximum events selected per IG
MAX_TBA_PER_IG: int = 1  # Max events with TBA date allowed per IG slot
MAX_NON_HACK_PER_IG: int = 2  # Max non-Hackathon events per IG

# ---------------------------------------------------------------------------
# Location Keywords (for scoring & filtering)
# ---------------------------------------------------------------------------

ONLINE_KEYWORDS = ["online", "virtual", "remote", "anywhere", "web"]
KERALA_KEYWORDS = [
    "kerala", "kochi", "trivandrum", "palai", "ernakulam", "calicut", "kollam",
    "thrissur", "kozhikode", "malappuram", "kannur", "kasargod", "alappuzha",
    "idukki", "pathanamthitta", "wayanad"
]
INDIA_KEYWORDS = [
    "india", "bangalore", "mumbai", "delhi", "chennai", "hyderabad", "pune",
    "bengaluru", "noida", "gurgaon", "kolkata", "ahmedabad", "surat",
    "jaipur", "lucknow", "nagpur", "indore", "bhopal", "visakhapatnam"
]
FOREIGN_KEYWORDS = [
    "usa", "uk ", "united states", "london", "san francisco", "new york",
    "canada", "europe", "germany", "australia", "china", "japan",
    "singapore", "dubai", "sf", "boston", "france", "netherlands",
    "sweden", "switzerland", "hong kong", "taiwan", "korea"
]

# ---------------------------------------------------------------------------
# Master IG list
# ---------------------------------------------------------------------------

MASTER_IGS: list[str] = [
    "Cyber Security",
    "Generative AI",
    "Web Development",
    "Product Management",
    "Devops",
    "Game Dev",
    "No Or Low Code",
    "Entrepreneurship",
    "Ar Vr Mr",
    "UI/UX",
    "Mobile Development",
    "Data Analytics",
    "Space",
    "AI",
    "Comics",
    "Digital Marketing",
    "MuV",
    "Data Structures and Algorithm",
    "Human Resources",
    "Blockchain",
    "Data Science",
    "Project Management",
    "Quantum Computing",
    "Strategic Leadership",
    "Civil",
    "Internet Of Things (IOT) And Robotics",
    "Creative Design",
    "Beckn",
    "Quality Assurance",
    "General Tech",
]

# ---------------------------------------------------------------------------
# IG keyword mapping
# ---------------------------------------------------------------------------

IG_KEYWORDS: dict[str, list[str]] = {
    "Web Development": [
        "web", "frontend", "backend", "fullstack", "full stack",
        "react", "node", "django", "flask", "html", "css",
        "javascript", "typescript", "nextjs", "vue", "php", "api", "rest", "graphql",
        "weboreel", "web auction", "web app", "website",
        "web dev", "browser", "web application", "web3 frontend",
        "http", "web development", "frontend developer", "backend developer"
    ],

    "AI": [
        "artificial intelligence", " ai ",
        "ai challenge", "neural network", "deep learning",
        "computer vision", "ai agent", "autonomous ai",
        "ai platform", "intelligent system", "ai solution",
        "ai tool", "ai build", "ai for", "aiventra",
        "fusionhack", "synapse sprint", "code innovation",
        "ai autonomous", "ai model", "ai powered", "ai driven",
        "ai x ", "ai-powered", "ai based", "using ai",
        "machine intelligence", "machine learning", " ml ",
        "nlp", "transformer", "inference", "model fine tuning", "turing"
    ],

    "Generative AI": [
        "generative", "llm", "gpt", "diffusion",
        "text-to-image", "stable diffusion", "prompt engineering",
        "genai", "gemini", "openai", "llama", "mistral",
        "image generation", "chatbot", "foundation model",
        "pixel gemini", "gen ai", "conversational ai",
        "language model", "creative ai", "generative model",
        "multimodal", "text generation"
    ],

    "Data Science": [
        "data science", "datathon", "eda", "pandas",
        "statistics", "dataset", "jupyter", "kaggle",
        "neurologic", "nlp datathon", "data contest",
        "data driven", "big data", "etl", "data pipeline",
        "data engineer", "data analyst", "data science contest",
        "nlp", "natural language processing", "scikit learn", "feature engineering"
    ],

    "Data Analytics": [
        "data analytics", "data visualization", "analytics",
        "business intelligence", "dashboard", "tableau",
        "power bi", "decode display", "visualization challenge",
        "data insight", "reporting", "metrics", "data story",
        "chart challenge", "data challenge", "data display",
        "bi challenge", "analytics hackathon"
    ],

    "Data Structures and Algorithm": [
        "data structures", "algorithm", "competitive programming",
        "cp contest", "codeforces", "leetcode", "coding contest",
        "lockout", "1v1 programming", "programming tournament",
        "codesprint", "code race", "dsa", "dynamic programming",
        "graph algorithm", "binary search", "cp individual",
        "cp team", "invariant cp", "impulse cp",
        "algorithmic", "coding challenge"
    ],

    "Cyber Security": [
        "cyber", "security", "ctf", "capture the flag",
        "vulnerability", "pentest", "bug bounty",
        "ethical hacking", "cybersecurity", "cyberwar",
        "infosec", "hacking", "exploit", "forensics",
        "malware", "network security", "penetration",
        "scbc", "vikingshacks", "secure bharat",
        "cyber nexus", "information security", "devsecops", "owasp", "siem",
        "threat intelligence", "incident response", "soc analyst"
    ],

    "Blockchain": [
        "blockchain", "web3", "smart contract", "nft",
        "defi", "solidity", "crypto", "ethereum", "polygon",
        "aetherax", "decentralized", "token", "wallet",
        "dao", "dapp", "bitcoin", "codeblue", "box box",
        "web 3", "blockzen", "crypto finance", "fintech", "finance", "banking", "payments", "digital wallet",
        "consensus", "distributed ledger", "evm", "onchain", "layer 2"
    ],

    "Game Dev": [
        "game", "game jam", "unity", "unreal", "pygame",
        "game dev", "game design", "pixel forge", "2d game",
        "3d game", "gaming", "indie game", "game engine",
        "level design", "pihacks", "game hackathon",
        "game build", "game challenge", "gamejam"
    ],

    "UI/UX": [
        "ui", "ux", "ui/ux", "figma", "wireframe",
        "prototype", "design thinking", "user experience",
        "interface design", "designathon", "crowdera",
        "uci design", "product design", "user interface",
        "user research", "accessibility", "hci",
        "interaction design", "ux design", "visual ux",
        "usability", "design sprint"
    ],

    "Ar Vr Mr": [
        "augmented reality", "virtual reality", " ar ", " vr ",
        "ar/vr", " xr ", "metaverse", "mixed reality",
        "spatial computing", "pacific portal", "designxr",
        "immersive", "oculus", "hololens", "extended reality",
        "3d immersive", "hologram", "ar mr", "vr mr",
        "spatial experience", "immersive tech"
    ],

    "Internet Of Things (IOT) And Robotics": [
        "iot", "internet of things", "hardware", "raspberry pi",
        "arduino", "robotics", "embedded", "sensors",
        "microcontroller", "nirmith hardware", "smart device",
        "firmware", "circuit", "physical computing", "wearable",
        "automation hardware", "krithoathon", "lorri",
        "iot hackathon", "hardware hack", "hardware and software",
        "drone", "robot", "mechatronics"
    ],

    "Mobile Development": [
        "android", "ios", "flutter", "react native",
        "mobile app", "swift", "kotlin", "mobile development",
        "mobile hack", "app development", "cross platform",
        "mobile ui", "grizzly hacks",
        "zervehack", "mobile application", "mobile challenge",
        "mobile first"
    ],

    "Devops": [
        "devops", "ci/cd", "docker", "deployment",
        "pipeline", "jenkins", "gitops", "monitoring",
        "sre", "platform engineering", "helm", "ansible",
        "infrastructure as code", "site reliability",
        "build pipeline", "containerization", "kubernetes",
        "cloud infra", "cloud", "aws", "azure", "gcp", "developer week",
        "devops challenge", "cloud ops", "terraform", "observability", "prometheus", "grafana"
    ],

    "Product Management": [
        "product management", "product manager", "product strategy",
        "product thinking", "product teardown", "ai product",
        "roadmap", "product sprint",
        "product challenge", "pm challenge", "vibeflow",
        "kindly labs", "spec driven", "product build",
        "product design challenge", "product innovation"
    ],

    "Project Management": [
        "project management", "project manager", "pmp",
        "agile", "scrum", "sprint planning",
        "project challenge", "delivery management",
        "waterfall", "project strategy", "project lead",
        "project solution", "project innovation",
        "project planning", "project execution", "project dev", "project development"
    ],

    "Entrepreneurship": [
        "startup", "ideathon", "pitch", "venture",
        "business plan", "entrepreneurship", "innovation challenge",
        "katz school", "ideation", "buildathon", "founder",
        "incubation", "social enterprise", "mvp challenge",
        "smart horizon",
        "entrepreneurship simulation", "build your business",
        "startup weekend", "innovation sprint",
        "social good", "sustainable innovation", "impact startup", "climate startup",
        "startup funding", "seed", "pitch deck", "business model",
        "women in tech", "shebuilds", "she builds"
    ],

    "Digital Marketing": [
        "marketing", "seo", "campaigns", "social media",
        "content marketing", "growth hacking", "brand",
        "digital marketing", "ai marketing", "marketing intern",
        "influencer", "advertising", "email marketing",
        "marketing challenge", "market strategy",
        "digital campaign", "growth challenge"
    ],

    "No Or Low Code": [
        "no code", "low code", "nocode", "lowcode",
        "no-code", "low-code", "bubble", "webflow",
        "zapier", "airtable", "glide", "appsmith",
        "trae", "vibe coding", "drag and drop",
        "visual programming", "no or low code",
        "no code hackathon", "low code challenge",
        "citizen developer", "visual builder"
    ],

    "Human Resources": [
        "human resources", " hr ", "people management",
        "talent", "recruitment", "hiring challenge",
        "workforce", "hr challenge",
        "people ops", "employee", "hrtech",
        "talent management", "people analytics",
        "hr innovation"
    ],

    "Strategic Leadership": [
        "strategic", "leadership", "strategy", "management hack",
        "business strategy", "case study", "case competition",
        "business challenge", "consulting", "business model",
        "strategic thinking", "decision making", "corporate hack",
        "business leadership", "management challenge",
        "executive", "strategic innovation",
        "social impact", "sustainability", "climate leadership", "community impact",
        "policy", "sdg", "impact leadership"
    ],

    "Civil": [
        "civil", "construction", "structural", "geotechnical",
        "transportation engineering", "water resources",
        "civil engineering", "bridge design", "building design",
        "urban infrastructure", "environmental engineering",
        "civil hackathon", "architecture engineering",
        "hydrohackathon", "water", "sanitation",
        "eco", "sustainable", "climate", "environment", "green", "social good"
    ],

    "Quality Assurance": [
        "quality assurance", " qa ", "testing", "test automation",
        "software testing", "quality control", "bug hunt",
        "defect", "selenium", "test case", "qa hackathon",
        "quality challenge", "code quality", "qa challenge",
        "testing hackathon", "quality engineering"
    ],

    "Comics": [
        "comics", "comic", "manga", "illustration",
        "storytelling", "graphic novel", "cartoon",
        "storyboard", "visual storytelling", "comic hack",
        "animation challenge", "sketch challenge",
        "drawing challenge", "comic creation",
        "webcomic", "digital art story"
    ],

    "MuV": [
        "media", "visual arts", "short film", "reel",
        "storytelling", "content creation",
        "acting", "performance", "stage", "theatre",
        "filmmaking", "podcast", "photography challenge",
        "vlog", "screenplay", "documentary",
        "spoken word", "art performance", "video challenge",
        "film festival", "content creator"
    ],

    "Space": [
        "space", "satellite", "nasa", "aerospace", "orbital",
        "rocketry", "astrophysics", "spacetech", "cosmos",
        "space exploration", "lunar", "mars", "telescope",
        "space challenge", "isro", "space innovation", "space mission"
    ],

    "Quantum Computing": [
        "quantum", "qubit", "qiskit", "quantum circuit",
        "quantum ml", "quantum computing", "quantum hackathon",
        "superposition", "entanglement", "quantum algorithm",
        "quantum challenge", "quantum innovation",
        "quantum software", "quantum physics"
    ],

    "Creative Design": [
        "creative design", "illustration", "visual design",
        "branding", "graphic design", "motion design",
        "photography", "design challenge",
        "typography", "poster design", "logo design",
        "art challenge", "creative challenge", "design sprint",
        "visual communication", "design competition"
    ],

    "Beckn": [
        "beckn", "ondc", "open network", "interoperability",
        "decentralized commerce", "beckn protocol",
        "open commerce", "network protocol",
        "beckn challenge", "open protocol", "beckn build"
    ],

    "General Tech": []
}

# ---------------------------------------------------------------------------
# Reverse IG mapping for curated fallback
# ---------------------------------------------------------------------------

RELATED_IGS: dict[str, list[str]] = {
    "AI": ["Generative AI", "Data Science", "Web Development"],
    "Generative AI": ["AI", "Data Science", "Creative Design"],
    "Data Science": ["Data Analytics", "AI", "Data Structures and Algorithm"],
    "Data Analytics": ["Data Science", "AI", "Data Structures and Algorithm"],
    "Data Structures and Algorithm": ["Data Science", "Cyber Security", "General Tech"],
    "Cyber Security": ["Data Structures and Algorithm", "Devops", "General Tech"],
    "Blockchain": ["Web Development", "Cyber Security", "Devops"],
    "Game Dev": ["Creative Design", "Mobile Development", "Ar Vr Mr"],
    "UI/UX": ["Creative Design", "Web Development", "Mobile Development"],
    "Ar Vr Mr": ["Game Dev", "Creative Design", "Mobile Development"],
    "Internet Of Things (IOT) And Robotics": ["Space", "Devops", "Cyber Security"],
    "Mobile Development": ["Web Development", "UI/UX", "Game Dev"],
    "Devops": ["Web Development", "Cyber Security", "General Tech"],
    "Project Management": ["Product Management", "Strategic Leadership", "Entrepreneurship"],
    "Product Management": ["Project Management", "Entrepreneurship", "Digital Marketing"],
    "Entrepreneurship": ["Product Management", "Strategic Leadership", "Digital Marketing"],
    "Digital Marketing": ["Entrepreneurship", "Product Management", "General Tech"],
    "No Or Low Code": ["Web Development", "Mobile Development", "General Tech"],
    "Human Resources": ["Strategic Leadership", "Entrepreneurship", "General Tech"],
    "Strategic Leadership": ["Project Management", "Entrepreneurship", "General Tech"],
    "Civil": ["Space", "Internet Of Things (IOT) And Robotics", "General Tech"],
    "Quality Assurance": ["Devops", "Data Structures and Algorithm", "General Tech"],
    "Comics": ["Creative Design", "UI/UX", "General Tech"],
    "Space": ["Internet Of Things (IOT) And Robotics", "AI", "General Tech"],
    "Quantum Computing": ["AI", "Data Science", "General Tech"],
    "Creative Design": ["UI/UX", "Game Dev", "Comics"],
    "Beckn": ["Web Development", "Devops", "General Tech"],
    "Web Development": ["Mobile Development", "UI/UX", "General Tech"],
    "General Tech": [],
    "MuV": ["Creative Design", "Comics", "UI/UX"],
}

# ---------------------------------------------------------------------------
# Scoring Weights (Finalized)
# ---------------------------------------------------------------------------

SCORE_FREE_ONLINE_KERALA: int = 250  # Free + (Online or Kerala)
SCORE_FREE_INDIA: int = 150          # Free + rest of India
SCORE_PAID_INDIA: int = 100          # Paid but still India
SCORE_UNKNOWN_FEE: int = 50          # Fee unknown (TBA / None)
SCORE_FREE_BASE: int = 80            # Free anywhere
SCORE_PRIZE: int = 60                # Has a prize pool
SCORE_UPCOMING_30D: int = 100        # Starts within 30 days
SCORE_HACKATHON_TYPE: int = 100      # Hackathon
SCORE_BOOTCAMP_TYPE: int = 70        # Bootcamp
SCORE_WORKSHOP_TYPE: int = 50        # Workshop
SCORE_CONTEST_TYPE: int = 30         # Contest
SCORE_INTERNSHIP_TYPE: int = 10      # Internship
SCORE_TBA_DATE: int = 10             # Date is TBA

