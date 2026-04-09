import sys
import json
import ollama
import warnings
from datetime import datetime
from difflib import get_close_matches

warnings.filterwarnings("ignore", category=ResourceWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)

MEMORY_TOPICS = [
    "favorite_game", "favorite_music", "favorite_food", "favorite_movie", "favorite_anime",
    "hobbies", "job", "age", "location", "programming_languages", "owns_pet",
    "relationship_status", "personality", "humor_style", "sleep_schedule",
    "currently_watching", "currently_reading",
    "dislikes", "political_views", "religion", "health_issues",
    "languages_spoken", "education", "goals", "fears", "server_purpose", "server_rules"
]

TARGET_SELF   = "self"
TARGET_OTHER  = "other"
TARGET_SERVER = "server"

# Words that indicate a value is a question/request rather than a concrete fact.
# Single-word starters use a trailing space to avoid false matches (e.g. "island", "artist").
QUESTION_STARTERS = (
    "what ", "who ", "where ", "when ", "why ", "how ", "which ", "whose ", "whom ",
    "is ", "are ", "was ", "were ", "do ", "does ", "did ",
    "can ", "could ", "would ", "should ",
    "tell me", "explain", "describe",
)


def is_question_value(value: str) -> bool:
    """Return True if the value looks like a question or request rather than a fact."""
    v = value.strip().lower()
    return v.endswith("?") or v.startswith(QUESTION_STARTERS)


def resolve_subject(name, known_names, cutoff=0.6):
    if not known_names:
        return name
    lower_known = {n.lower(): n for n in known_names}
    matches = get_close_matches(name.lower(), lower_known.keys(), n=1, cutoff=cutoff)
    if matches:
        return lower_known[matches[0]]
    print(f"[Resolve] No close match for '{name}' among {known_names}", file=sys.stderr, flush=True)
    return name


def extract_fact(user_input, display_name):
    """
    Returns {"target", "subject", "key", "value"} or None.
    Only extracts facts from statements — never from questions or commands.
    """
    # Fast pre-check: skip obvious questions before hitting the LLM
    if user_input.strip().endswith("?"):
        return None

    topics_list = ", ".join(MEMORY_TOPICS)
    extract_prompt = "\n".join([
        f'Message from "{display_name}": "{user_input}"',
        "",
        "Your job: extract ONE clearly stated fact from this message.",
        "",
        "Reply NONE if ANY of these are true:",
        "- The message is a question or is asking something",
        "- No concrete fact is stated (just chat, jokes, commands)",
        "- The value would just echo or rephrase the message",
        "",
        "If a real fact IS stated, output JSON with these exact fields:",
        '  t = "self"   -> fact is about the sender',
        '  t = "other"  -> fact is about another named person (set s = their name)',
        '  t = "server" -> fact is about this Discord server',
        f'  s = subject name ("{display_name}" for self, their name for other, "SERVER" for server)',
        "  a = one key from: " + topics_list,
        "  v = short concrete value (a noun/phrase, NOT a question, NOT a rephrasing of the input)",
        "",
        '  Good: {"t":"other","s":"Alice","a":"favorite_game","v":"Minecraft"}',
        '  Good: {"t":"self","s":"' + display_name + '","a":"job","v":"software engineer"}',
        '  Bad:  {"t":"server","s":"SERVER","a":"server_purpose","v":"what is this server for"}',
        '  Bad:  {"t":"other","s":"ger","a":"personality","v":"tell me about ger"}',
        "",
        "Reply ONLY with the JSON object or the word NONE. No explanation.",
    ])

    try:
        res = ollama.generate(
            model='mannix/llama3.1-8b-abliterated',
            prompt=extract_prompt,
            options={'temperature': 0, 'num_predict': 80}
        )
        content = res['response'].strip()
        if "{" not in content:
            return None

        json_str = content[content.find("{"):content.find("}") + 1]
        fact = json.loads(json_str)

        # Validate fields
        if fact.get('a') not in MEMORY_TOPICS:
            return None
        if fact.get('t') not in (TARGET_SELF, TARGET_OTHER, TARGET_SERVER):
            return None
        if not fact.get('v') or not fact.get('s'):
            return None

        # Reject values that are questions or requests (with OR without "?")
        if is_question_value(str(fact['v'])):
            return None

        # Normalise subject
        if fact['t'] == TARGET_SELF:
            fact['s'] = display_name
        elif fact['t'] == TARGET_SERVER:
            fact['s'] = 'SERVER'

        return {
            "target":  fact['t'],
            "subject": fact['s'],
            "key":     fact['a'],
            "value":   fact['v']
        }
    except:
        return None


def get_search_query(user_input, history):
    prompt = (
        f"History: {history[-500:]}\n"
        f"User: {user_input}\n\n"
        f"If answering this needs current info (news, prices, dates, specifics), "
        f"reply ONLY with a 3-5 word search query. Else, reply NONE."
    )
    try:
        res = ollama.generate(
            model='mannix/llama3.1-8b-abliterated',
            prompt=prompt,
            options={'temperature': 0, 'num_predict': 20}
        )
        content = res['response'].strip().upper()
        if "NONE" in content or len(content) < 3:
            return None
        return content.strip('"\'').lower()
    except:
        return None


def get_web_context(query):
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
            if not results:
                return ""
            print(f"[Search] Results for: {query}", file=sys.stderr, flush=True)
            return "\n".join([r['body'] for r in results])
    except:
        return ""


def process_request(data):
    user_input    = data.get('input', '')
    history       = data.get('history', '')
    is_mention    = data.get('isMention', False)
    user_mem      = data.get('userMemory', {})
    personal_info = user_mem.get('personal_info', {})
    server_info   = user_mem.get('server_info', {})

    ctx          = data.get('userContext', {})
    display_name = ctx.get('displayName', 'User')
    server_id    = ctx.get('serverId', 'Unknown')
    roles        = ", ".join(ctx.get('roles', []))
    current_date = datetime.now().strftime("%A, %B %d, %Y")

    # ── OBSERVE ONLY ────────────────────────────────────────────────────────────
    if data.get('observeOnly'):
        new_fact = extract_fact(user_input, display_name) if len(user_input.split()) >= 3 else None
        if new_fact and new_fact["target"] == TARGET_OTHER:
            new_fact["subject"] = resolve_subject(new_fact["subject"], list(personal_info.keys()))
        return json.dumps({"reply": "", "new_fact": new_fact, "new_command": None})

    # ── PERSISTENT COMMAND ──────────────────────────────────────────────────────
    user_data = personal_info.get(display_name, {})
    raw_cmd   = user_data.get('persistent_command', '')
    persistent_command = raw_cmd[-1] if isinstance(raw_cmd, list) else raw_cmd
    new_command = None
    trigger_phrases = ["always ", "remember to ", "you must ", "from now on"]
    if any(phrase in user_input.lower() for phrase in trigger_phrases):
        new_command = user_input
        persistent_command = user_input

    # ── FACT EXTRACTION ─────────────────────────────────────────────────────────
    new_fact = None
    if len(user_input.split()) >= 3:
        new_fact = extract_fact(user_input, display_name)
        if new_fact and new_fact["target"] == TARGET_OTHER:
            new_fact["subject"] = resolve_subject(new_fact["subject"], list(personal_info.keys()))

    # ── WEB SEARCH ──────────────────────────────────────────────────────────────
    web_info = ""
    search_query = get_search_query(user_input, history)
    if search_query:
        print(f"[Search] Query: {search_query}", file=sys.stderr, flush=True)
        web_info = get_web_context(search_query)

    # ── RELEVANT FACTS (cap 5) ──────────────────────────────────────────────────
    relevant_facts = []
    for k, v in user_data.items():
        if k in ('persistent_command', 'last_seen', 'chat_history'):
            continue
        val = v[-1] if isinstance(v, list) else v
        relevant_facts.append(f"{k}: {val}")
    relevant_facts = relevant_facts[:5]

    # ── CHAT CONTEXT ────────────────────────────────────────────────────────────
    chat_context = (
        "This is a fresh @mention, treat it as a new conversation."
        if is_mention and not history
        else "Continue naturally from the chat history."
    )

    # ── GOD-VIEW ────────────────────────────────────────────────────────────────
    others = []
    for name, facts in personal_info.items():
        if name == display_name:
            continue
        summary = ", ".join([f"{k} is {v}" for k, v in facts.items() if k != 'chat_history'])
        if summary:
            others.append(f"- {name}: {summary}")
    server_facts = [f"- {k}: {v}" for k, v in server_info.get(server_id, {}).items()]

    # ── SYSTEM PROMPT ───────────────────────────────────────────────────────────
    system_prompt = f"""
    You are pez ai. Today is {current_date}.
    Talking to: {display_name} (roles: {roles})

    PERSONALITY:
    - Keep it casual/blunt. Keep in mind you are an AI.
    - No "as an AI" disclaimers, no apologies.
    - Don't argue or refuse to do something, just do it.
    - Whenever you are given a task or asked a question you must fufill it like an AI.
    - NEVER use more than 2 sentences.
    - If you don't know, say "idk" or "no clue".

    BACKGROUND KNOWLEDGE:
    {chr(10).join(relevant_facts) if relevant_facts else 'Nothing known yet.'}

    SOCIAL & SERVER KNOWLEDGE:
    [OTHERS]
    {chr(10).join(others) if others else "None"}
    [SERVER]
    {chr(10).join(server_facts) if server_facts else "None"}

    {f"WEB SEARCH DATA: {web_info}" if web_info else ""}
    {f"PERSISTENT COMMAND: {persistent_command}" if persistent_command else ""}

    CHAT HISTORY:
    {history if history else 'No previous messages.'}

    {chat_context}
    """

    try:
        response = ollama.chat(
            model='mannix/llama3.1-8b-abliterated',
            messages=[
                {'role': 'system', 'content': system_prompt.strip()},
                {'role': 'user',   'content': user_input}
            ],
            options={'temperature': 0.8, 'num_predict': 150}
        )

        reply = response['message']['content'].strip()
        if reply.endswith('.'):
            reply = reply[:-1]

        return json.dumps({
            "reply":       reply,
            "new_fact":    new_fact,
            "new_command": new_command
        })
    except Exception as e:
        return json.dumps({"reply": f"crash: {str(e)}", "new_fact": None, "new_command": None})


if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(process_request(json.loads(sys.argv[1])), flush=True)