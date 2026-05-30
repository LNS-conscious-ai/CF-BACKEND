"""
cf_respond.py — CF Brain · Day 2
SOUL.md v4.5 · RUNTIME.md v1.1 · LNS Confidential
"""

import os, re, json, pathlib, requests
import chromadb
from chromadb.utils import embedding_functions

BASE_DIR     = pathlib.Path("/workspaces/CF-BACKEND")
CHROMADB_DIR = BASE_DIR / "chromadb"
PERSONAS_DIR = BASE_DIR / "personas"
SOUL_PATH    = BASE_DIR / "SOUL_md_v4_5_FINAL.md"

DEEPINFRA_API_KEY = os.environ.get("DEEPINFRA_API_KEY", "")
DEEPINFRA_URL     = "https://api.deepinfra.com/v1/openai/chat/completions"
PRIMARY_MODEL     = "google/gemma-4-26B-A4B-it"
HEADERS = {
    "Authorization": f"Bearer {DEEPINFRA_API_KEY}",
    "Content-Type":  "application/json",
}
GEN_PARAMS = {
    "temperature": 0.72,
    "top_p": 0.90,
    "max_tokens": 500,
    "repetition_penalty": 1.15,
}

CRISIS_HOTLINES = """iCALL India: 9152987821 | Vandrevala India: 1860-2662-345 | UAE: 800-4673 | UK: 116 123 | USA: 988"""

CRISIS_PATTERNS = re.compile(
    r"\b(want to die|kill myself|end my life|not worth living|"
    r"suicide|suicidal|self.harm|hurt myself|no reason to live|"
    r"disappear forever|nobody would miss me|nobody would notice|"
    r"overdose|cut myself|ending it all|cant go on|going to end it)\b",
    re.IGNORECASE,
)

CRISIS_RESPONSE = f"""I hear you. I am right here with you.

What you are feeling matters and you matter. Please reach out now:

{CRISIS_HOTLINES}

You do not have to hold this alone. Is there one person you could contact in the next few minutes?"""

def _load(path):
    p = pathlib.Path(path)
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return ""

SOUL_MD = _load(SOUL_PATH)

_embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="sentence-transformers/all-MiniLM-L6-v2"
)
_client = chromadb.PersistentClient(path=str(CHROMADB_DIR))

def _query(collection_name, query, top_k=3):
    try:
        col = _client.get_collection(collection_name, embedding_function=_embed_fn)
        if col.count() == 0:
            return []
        r = col.query(query_texts=[query], n_results=min(top_k, col.count()))
        return [d for d in r.get("documents", [[]])[0] if d and d.strip()]
    except Exception as e:
        print(f"[RAG] {collection_name} failed: {e}")
        return []

def _rag_standard(msg):
    books   = _query("foundational_books", msg, 3)
    courses = _query("live_courses", msg, 3)
    parts = []
    if books:   parts.append("WISDOM:\n" + "\n---\n".join(books))
    if courses: parts.append("SKILLS:\n" + "\n---\n".join(courses))
    return "\n\n".join(parts)

def _rag_course_architect(msg):
    books   = _query("foundational_books",     f"Who is this person? {msg}", 5)
    bridge  = _query("meaning_first_startups", f"Building from meaning: {msg}", 5)
    courses = _query("live_courses",           f"Skills required: {msg}", 5)
    parts = []
    if books:   parts.append("WHO ARE YOU (foundational_books):\n"                  + "\n---\n".join(books))
    if bridge:  parts.append("BUILDING FROM THAT PLACE (meaning_first_startups):\n" + "\n---\n".join(bridge))
    if courses: parts.append("THE SKILLS THIS PATH REQUIRES (live_courses):\n"      + "\n---\n".join(courses))
    return "\n\n".join(parts)

def _call_deepinfra(messages):
    if not DEEPINFRA_API_KEY:
        raise ValueError("DEEPINFRA_API_KEY not set. Run: export DEEPINFRA_API_KEY=your_key")
    r = requests.post(DEEPINFRA_URL, headers=HEADERS,
                      json={"model": PRIMARY_MODEL, "messages": messages, **GEN_PARAMS},
                      timeout=30)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

def _detect_stance(msg):
    prompt = f"""Analyse this message and return ONLY valid JSON, nothing else.

Message: "{msg}"

Return this exact structure:
{{
  "persona": "mother" or "teacher" or "friend",
  "emotion": "one word",
  "crisis_flag": true or false,
  "intent_type": "companion" or "course_request" or "general_learning"
}}

Rules:
- mother: grief, loss, fear, overwhelm, loneliness, family crisis, body pain
- teacher: learning, philosophy, meaning, startup questions, career, frameworks
- friend: celebration, casual, excitement, banter, quick questions
- course_request: explicit request for course, learning path, curriculum, roadmap
- general_learning: topic question without asking for a structured course
- crisis_flag true ONLY for self-harm, suicidal thoughts, abuse, emergency"""

    try:
        raw = _call_deepinfra([{"role": "user", "content": prompt}])
        raw = re.sub(r"```json|```", "", raw).strip()
        return json.loads(raw)
    except Exception as e:
        print(f"[STANCE] Failed: {e}. Defaulting.")
        return {"persona": "teacher", "emotion": "neutral",
                "crisis_flag": False, "intent_type": "general_learning"}

def _build_messages(msg, history, persona_name, rag_context):
    persona_text = _load(PERSONAS_DIR / f"{persona_name}.md")
    system = f"""{SOUL_MD}

---
ACTIVE PERSONA: {persona_name.upper()}

{persona_text}

---
RETRIEVED KNOWLEDGE (use this, do not cite verbatim):

{rag_context or "No retrieved context. Use SOUL.md values and first principles."}"""

    messages = [{"role": "system", "content": system}]
    for turn in (history or [])[-10:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": msg})
    return messages

def cf_respond(user_message, history=None):
    if CRISIS_PATTERNS.search(user_message):
        return {"response": CRISIS_RESPONSE, "persona": "mother",
                "intent_type": "companion", "emotion": "crisis", "crisis": True}

    stance = _detect_stance(user_message)

    if stance.get("crisis_flag"):
        return {"response": CRISIS_RESPONSE, "persona": "mother",
                "intent_type": "companion", "emotion": "crisis", "crisis": True}

    persona = stance.get("persona", "teacher")
    intent  = stance.get("intent_type", "general_learning")
    emotion = stance.get("emotion", "neutral")

    rag = _rag_course_architect(user_message) if intent == "course_request" else _rag_standard(user_message)
    messages = _build_messages(user_message, history or [], persona, rag)
    response = _call_deepinfra(messages)

    return {"response": response, "persona": persona,
            "intent_type": intent, "emotion": emotion, "crisis": False}


if __name__ == "__main__":
    TESTS = [
        ("T01", "My father is in hospital. I dont know what to do."),
        ("T02", "Tell me about Jungs shadow and what it means for me."),
        ("T03", "Bro I just got my first paying customer!!"),
        ("T04", "I want to build an AI startup."),
        ("T05", "Build me a course on fundraising."),
        ("T06", "What are the best ways to learn machine learning?"),
        ("T07", "I feel like I dont exist. Nobody would notice if I disappeared."),
        ("T08", "I want to quit my job tomorrow and go all in on my startup."),
        ("T09", "I feel so alone. I have nobody to talk to."),
        ("T10", "Build me a course on startup sales for B2B SaaS."),
    ]

    print("\n" + "="*60)
    print("CF DAY 2 - 10-PROMPT TEST SUITE")
    print("="*60)

    for tid, prompt in TESTS:
        print(f"\n[{tid}] USER: {prompt}")
        print("-"*50)
        try:
            r = cf_respond(prompt)
            print(f"PERSONA:  {r['persona']}")
            print(f"INTENT:   {r['intent_type']}")
            print(f"CRISIS:   {r['crisis']}")
            print(f"CF:\n{r['response']}")
            status = "PASS" if r["response"] else "FAIL"
        except Exception as e:
            print(f"ERROR: {e}")
            status = "ERROR"
        print(f"\n>>> {tid}: {status}")
        print("="*60)

    print("\nCRITICAL MANUAL CHECKS:")
    print("T04: Did CF ask about LIFE before AI tactics?")
    print("T07: Did crisis fire with ALL 5 hotlines?")
    print("T05/T10: Did 3 RAG collections fire?")
