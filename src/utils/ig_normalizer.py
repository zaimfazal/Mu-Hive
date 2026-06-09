# src/utils/ig_normalizer.py

IG_CANONICAL = {
    "cyber security": "Cyber Security",
    "cybersecurity": "Cyber Security",
    "generative ai": "Generative AI",
    "gen ai": "Generative AI",
    "genai": "Generative AI",
    "web development": "Web Development",
    "web dev": "Web Development",
    "product management": "Product Management",
    "devops": "Devops",
    "game dev": "Game Dev",
    "no or low code": "No Or Low Code",
    "no code": "No Or Low Code",
    "low code": "No Or Low Code",
    "entrepreneurship": "Entrepreneurship",
    "ar vr mr": "Ar Vr Mr",
    "ar/vr": "Ar Vr Mr",
    "ar vr": "Ar Vr Mr",
    "xr": "Ar Vr Mr",
    "ui/ux": "UI/UX",
    "ui ux": "UI/UX",
    "mobile development": "Mobile Development",
    "mobile dev": "Mobile Development",
    "data analytics": "Data Analytics",
    "space": "Space",
    "ai": "AI",
    "comics": "Comics",
    "digital marketing": "Digital Marketing",
    "muv": "MuV",
    "data structures and algorithm": "Data Structures and Algorithm",
    "dsa": "Data Structures and Algorithm",
    "human resources": "Human Resources",
    "human resource": "Human Resources",
    "blockchain": "Blockchain",
    "data science": "Data Science",
    "project management": "Project Management",
    "quantum computing": "Quantum Computing",
    "strategic leadership": "Strategic Leadership",
    "civil": "Civil",
    "internet of things (iot) and robotics": "Internet Of Things (IOT) And Robotics",
    "internet of things": "Internet Of Things (IOT) And Robotics",
    "iot": "Internet Of Things (IOT) And Robotics",
    "robotics": "Internet Of Things (IOT) And Robotics",
    "creative design": "Creative Design",
    "beckn": "Beckn",
    "quality assurance": "Quality Assurance",
    "qa": "Quality Assurance",
    "general tech": "General Tech"
}

def normalize_ig(raw: str) -> str | None:
    if not raw:
        return None
    key = raw.strip().lower()
    return IG_CANONICAL.get(key, None)
