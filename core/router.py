import json, logging, re
from core.claude_client import ask_claude
from core.time_manager import get_str_now

logger = logging.getLogger("core.router")

async def analyze_message(text: str, uid: int) -> dict:
    user_time = get_str_now(uid)
    sys = f"""Ты роутер Nexus (Быт) и Arcana (Магия). Сейчас у юзера: {user_time}.
Верни JSON: {{"intent": "finance|task|note|update|session|ritual|client|set_tz", "target_system": "nexus|arcana", "data": {{...}}, "confidence": "high|low", "clarification_question": "вопрос если не понял"}}
Магия (расклады, свечи, клиенты) -> target_system: arcana. Быт (еда, зп, молоко) -> target_system: nexus.
Если не уверен, пиши вопрос в clarification_question."""
    
    raw = await ask_claude(text, system=sys)
    return _parse_json(raw)

async def parse_time_only(text: str, uid: int) -> str:
    user_time = get_str_now(uid)
    sys = f"Ты помощник по времени. Сейчас у юзера: {user_time}. Вычисли дату из фразы юзера. Верни ТОЛЬКО ISO формат (YYYY-MM-DDTHH:MM:00) или 'NONE'."
    raw = await ask_claude(text, system=sys)
    match = re.search(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})', raw)
    return match.group(1) if match else None

def _parse_json(raw: str) -> dict:
    if not raw: return {"intent": "unknown", "target_system": "nexus"}
    try:
        match = re.search(r'(\{.*\})', raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group(1))
            extra = re.sub(r'```json|```|json|JSON', '', raw.replace(match.group(1), "")).strip()
            if extra and not parsed.get("clarification_question"):
                if parsed.get("confidence") == "low": parsed["clarification_question"] = extra
            return parsed
        return {"intent": "unknown", "clarification_question": raw[:500]}
    except: return {"intent": "unknown", "target_system": "nexus"}