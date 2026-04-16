"""
query_understanding_agent.py
=============================
Analyses the raw user query to produce a structured understanding:
  - Detects language, translates to English
  - Identifies if it is a greeting / small-talk
  - Detects follow-up questions
  - Classifies intent (concise vs detailed)
  - Detects explicit language requests ("answer in Tamil")
  - Detects translation-only requests
  - Expands abbreviations for better retrieval
  - Rewrites follow-up queries into standalone form
  - Extracts geographic context (target region)
  - Detects mentioned employee names (for manager queries)

Optimised: merges multiple LLM calls into ONE combined prompt to cut latency.
"""

import re
import json
from google.adk.agents import Agent
from vertexai.generative_models import GenerativeModel
from google.cloud import translate_v2 as translate
from config import LLM_MODEL, get_llm_generation_config
import logging

logger = logging.getLogger("HD_SKYE_AGENT")
_translate_client = None

# ─── Singleton LLM model for query understanding ─────────────────────────────
_qu_model = None


def _get_qu_model():
    global _qu_model
    if _qu_model is None:
        _qu_model = GenerativeModel(
            LLM_MODEL, generation_config=get_llm_generation_config()
        )
    return _qu_model


def _get_translate_client():
    global _translate_client
    if _translate_client is None:
        _translate_client = translate.Client()
    return _translate_client


# ─── Language Detection & Translation ────────────────────────────────────────


def detect_and_translate(text: str) -> dict:
    """Detect language and translate to English if needed."""
    try:
        client = _get_translate_client()
        detection = client.detect_language(text)
        lang_code = detection["language"]
        if lang_code != "en":
            translation = client.translate(text, target_language="en")
            translated = translation["translatedText"]
            has_foreign = any(ord(c) > 0x024F for c in text)
            if has_foreign:
                return {
                    "translated_text": translated,
                    "original_language_code": lang_code,
                    "is_translated": True,
                }
            has_latin = bool(re.search(r"[a-zA-Z]", text))
            if not has_latin:
                return {
                    "translated_text": translated,
                    "original_language_code": lang_code,
                    "is_translated": True,
                }
            eng_words = {
                "abot",
                "what",
                "about",
                "how",
                "when",
                "where",
                "why",
                "who",
                "the",
                "for",
                "this",
                "that",
            }
            tokens = set(re.findall(r"\b\w+\b", text.lower()))
            if tokens.intersection(eng_words):
                return {
                    "translated_text": text,
                    "original_language_code": "en",
                    "is_translated": False,
                }
            clean_orig = re.sub(r"[^a-zA-Z]", "", text).lower()
            clean_trans = re.sub(r"[^a-zA-Z]", "", translated).lower()
            if len(text) > 0 and len(clean_orig) / len(text) > 0.5:
                if clean_orig in clean_trans or clean_trans in clean_orig:
                    return {
                        "translated_text": text,
                        "original_language_code": "en",
                        "is_translated": False,
                    }
            return {
                "translated_text": translated,
                "original_language_code": lang_code,
                "is_translated": True,
            }
        return {
            "translated_text": text,
            "original_language_code": "en",
            "is_translated": False,
        }
    except Exception:
        return {
            "translated_text": text,
            "original_language_code": "en",
            "is_translated": False,
        }


def translate_text(text: str, target_language: str) -> str:
    try:
        return _get_translate_client().translate(text, target_language=target_language)[
            "translatedText"
        ]
    except Exception:
        return text


def identify_explicit_language(text: str) -> str | None:
    """Detect if user explicitly requested a specific response language (regex-based, no LLM)."""
    _LANG_MAP = {
        "english": "en",
        "tamil": "ta",
        "hindi": "hi",
        "japanese": "ja",
        "telugu": "te",
        "kannada": "kn",
        "malayalam": "ml",
        "marathi": "mr",
        "bengali": "bn",
        "german": "de",
        "french": "fr",
        "spanish": "es",
        "chinese": "zh",
        "korean": "ko",
        "thai": "th",
        "vietnamese": "vi",
        "arabic": "ar",
        "portuguese": "pt",
        "italian": "it",
        "dutch": "nl",
        "russian": "ru",
        "turkish": "tr",
        "polish": "pl",
        "czech": "cs",
        "swedish": "sv",
        "danish": "da",
    }
    lower = text.lower()
    # Patterns like "answer in Tamil", "respond in Hindi", "reply in French", "in Japanese please"
    patterns = [
        r"(?:answer|respond|reply|tell me|explain|write|give|say)\s+(?:me\s+)?in\s+(\w+)",
        r"(?:i\s+)?want\s+(?:it|this|that|the\s+(?:above|answer|response))\s+in\s+(\w+)",
        r"(?:give|show|send)\s+(?:me\s+)?(?:(?:it|this|that)\s+)?in\s+(\w+)",
        r"convert\s+(?:(?:it|this|that)\s+)?(?:to|into)\s+(\w+)",
        r"in\s+(\w+)\s+(?:please|pls|plz|language)",
        r"(\w+)\s+(?:mei|mein|me|m[eé])\s+(?:bata|batao|jawab|bol)",  # Hindi patterns
    ]
    for pat in patterns:
        m = re.search(pat, lower)
        if m:
            lang_name = m.group(1)
            if lang_name in _LANG_MAP:
                return _LANG_MAP[lang_name]
    return None


def is_translation_request(text: str) -> bool:
    """Detect if user is PURELY asking to translate a previous response (regex-based, no LLM).
    Returns False if the text contains a substantive new question alongside a language request."""
    lower = text.lower().strip()

    # Step 1: Check if the text contains a new substantive question.
    # Strip away common language-request suffixes/phrases first.
    _lang_names = r"(?:tamil|hindi|japanese|french|german|spanish|chinese|korean|thai|telugu|kannada|malayalam|marathi|bengali|arabic|portuguese|italian|dutch|russian|turkish|vietnamese|english)"
    lang_request_patterns = [
        rf"\.\s*(?:give|answer|respond|reply|tell|explain|write|say|show|send|convert)\s+(?:me\s+)?(?:(?:it|this|that|the\s+(?:above|answer|response))\s+)?(?:in\s+{_lang_names}|(?:to|into)\s+{_lang_names})\s*[?.!]*\s*$",
        rf"\?\s*(?:give|answer|respond|reply|tell|explain|write|say|show|send|convert)\s+(?:me\s+)?(?:(?:it|this|that|the\s+(?:above|answer|response))\s+)?(?:in\s+{_lang_names}|(?:to|into)\s+{_lang_names})\s*[?.!]*\s*$",
        rf"(?:give|answer|respond|reply|tell|explain|write|say|show|send)\s+(?:me\s+)?(?:(?:it|this|that|the\s+(?:above|answer|response))\s+)?in\s+{_lang_names}\s*[?.!]*\s*$",
        rf"\bin\s+{_lang_names}\s*(?:please|pls|plz)?\s*[?.!]*\s*$",
    ]
    stripped = lower
    for pat in lang_request_patterns:
        stripped = re.sub(pat, "", stripped).strip()

    # If after removing language request there's still a meaningful question (>15 chars with a question word),
    # this is a NEW question with a language preference — NOT a translation-only request.
    question_words = r"\b(?:what|who|where|when|why|how|which|tell|explain|list|describe|can|do|does|is|are|should|could|would)\b"
    if len(stripped) > 15 and re.search(question_words, stripped):
        return False

    # Step 2: Now check if the original text matches translation-request patterns.
    patterns = [
        r"\btranslat(?:e|ion)\b",
        r"\b(?:say|tell|give|write|show|convert)\s+(?:it|this|that|the\s+(?:above|answer|response))\s+in\s+\w+",
        r"\b(?:i\s+)?want\s+(?:it|this|that|the\s+(?:above|answer|response))\s+in\s+\w+",
        rf"\b(?:give|show|send)\s+(?:me\s+)?(?:(?:it|this|that|the\s+(?:above|answer|response))\s+)?in\s+{_lang_names}",
        r"\bconvert\s+(?:(?:it|this|that|the\s+(?:above|answer|response))\s+)?(?:to|into)\s+\w+",
        rf"\bin\s+{_lang_names}\s*(?:please|pls)?\s*$",
        r"\b(?:mein|mei|me)\s+(?:bata|batao|jawab)\b",
    ]
    return any(re.search(p, lower) for p in patterns)


# ─── Query Analysis ──────────────────────────────────────────────────────────


def is_small_talk(query: str) -> bool:
    """Detect greetings, thank-you messages, and system messages.

    To avoid false-positives on messages that happen to contain polite
    phrases (e.g. Japanese business emails with "Thank you for your
    assistance"), greeting/thank-you patterns only trigger when the message
    is SHORT (≤ 12 words) **and** does NOT contain a real question.
    System-message patterns always apply regardless of length.
    """
    q_clean = re.sub(r"[?.!,]", "", query.lower().strip())
    q_raw = query.lower().strip()
    word_count = len(q_clean.split())

    # ── Always-match patterns (system messages, whole-line matches) ───
    always_patterns = [
        r"your response was sent",
        r"this is not a question",
        r"^thanks?[!.\s]*$",
        r"^(ok|okay)\s*(thanks?|thank you)[!.\s]*$",
    ]
    if any(re.search(p, q_clean) for p in always_patterns):
        return True

    # ── Check if the message contains a real question ────────────────
    # If there's a question mark or interrogative words with substantive
    # content, it's NOT small talk even if it starts with "Hi" / "Thanks".
    # Whitelist known small-talk phrases that look like questions.
    _SMALL_TALK_PHRASES = {"what can you do", "who are you", "how are you"}
    if q_clean in _SMALL_TALK_PHRASES:
        return True
    _has_question_mark = "?" in q_raw
    _INTERROGATIVES = (
        r"\b(what|where|when|which|why|how much|how many|how do|how can|how to)\b"
    )
    _REQUEST_WORDS = r"\b(can|could|do|does|did|is|are|should|would|tell|share|show|guide|explain|please|need|want)\b"
    _has_interrogative = bool(re.search(_INTERROGATIVES, q_clean))
    _has_request = bool(re.search(_REQUEST_WORDS, q_clean)) and word_count > 5
    _has_real_question = _has_question_mark or _has_interrogative or _has_request

    # ── Short-message-only patterns (greetings + thank-you) ──────────
    # These only fire when the query is ≤ 12 words AND does not contain
    # a real question, to prevent false positives.
    if word_count <= 12 and not _has_real_question:
        short_patterns = [
            r"\bhi\b",
            r"\bhello\b",
            r"\bhey\b",
            r"\bhow are you\b",
            r"\bgood morning\b",
            r"\bgood afternoon\b",
            r"\bgood evening\b",
            r"\bwhat can you do\b",
            r"\bwho are you\b",
            r"\bthanks?\b",
            r"\bthank you\b",
            r"\bthx\b",
            r"\bappreciate\b",
            r"\bthanks? (a lot|so much|for)",
        ]
        if any(re.search(p, q_clean) for p in short_patterns):
            return True

    return False


EXPANSIONS = {
    # ── MOST SPECIFIC first (multi-word phrases) ─────────────────────
    # IT support & Teams-related queries → IT Service Desk
    "teams account": "IT Service Desk contact support technical issue AskNow",
    "teams photo": "IT Service Desk contact support profile photo technical issue",
    "teams profile": "IT Service Desk contact support profile photo update technical",
    "it support": "IT Service Desk contact phone number technical issue AskNow",
    "technical issue": "IT Service Desk contact phone number technical support AskNow",
    # Manager / work location corrections → HiNext
    "manager incorrect": "HiNext manager correction change People Direct self-service AskNow",
    "manager wrong": "HiNext manager correction change People Direct self-service AskNow",
    "manager showing": "HiNext manager correction change People Direct self-service AskNow",
    "manager change": "HiNext manager correction People Direct self-service",
    "incorrect manager": "HiNext manager correction change People Direct self-service AskNow",
    "wrong manager": "HiNext manager correction change People Direct self-service AskNow",
    "correct manager": "HiNext manager correction change People Direct self-service AskNow",
    "showing incorrect": "HiNext correction change People Direct self-service AskNow",
    "work location": "HiNext work location change correction self-service manager AskNow",
    "location wrong": "HiNext work location change correction self-service AskNow",
    "location incorrect": "HiNext work location change correction self-service AskNow",
    # Personal details update → HiNext
    "personal details": "HiNext personal details update change self-service employee",
    "update details": "HiNext personal details update change self-service",
    # Request time off / how to apply (process, not entitlement) — BEFORE "holiday"
    "request time off": "request time off HR Smart Platform portal process approval apply leave",
    "time off": "request time off leave absence HR Smart Platform portal process",
    # Absence management
    "absence management": "absence management system tool leave application HiNext portal link URL",
    # HiNext link requests
    "hinext": "HiNext SSO login portal link URL employee resources",
    # ── Onboarding ───────────────────────────────────────────────────
    "day 1": "day one first day new joiner onboarding pre-employment documents checklist",
    "first day": "day one first day new joiner onboarding pre-employment documents checklist",
    "onboarding": "new joiner onboarding first day day one pre-employment documents checklist orientation",
    # ── Business cards ────────────────────────────────────────────────
    "business card": "business cards order request corporate identity branding name card print",
    "name card": "business cards order request corporate identity branding name card print",
    # ── P-Cards ────────────────────────────────────────────────────────
    "p-card": "Corporate Card Program Policy eligible authorized cardholder pcard",
    "pcard": "Corporate Card Program Policy eligible authorized cardholder pcard",
    # ── Abbreviations ────────────────────────────────────────────────
    "wfh": "work from home remote work",
    "pto": "paid time off vacation leave",
    "ooo": "out of office",
    "fmla": "family medical leave act",
    # ── Leave ────────────────────────────────────────────────────────
    "mark leave": "apply leave HiNext Workday portal mark leave request time off absence management",
    "apply leave": "apply leave HiNext Workday portal leave request time off absence management",
    "leave portal": "HiNext Workday portal apply leave mark leave request",
    "leave request": "apply leave HiNext Workday portal leave request time off",
    "mark the tickets": "apply leave HiNext Workday portal mark leave request time off absence management",
    "carry forward": "carry forward leave entitlement annual vacation rollover carryover unused holiday next year",
    "carried forward": "carry forward leave entitlement annual vacation rollover carryover unused holiday next year",
    "carryover": "carry forward leave entitlement annual vacation rollover carryover unused holiday next year",
    "casual leave": "casual leave earned leave sick leave annual leave entitlement carryforward balance types paid leave policy India leave 18 12",
    "how many leaves": "leave entitlement earned leave sick leave days per year annual leave types policy how many total",
    "how many leave": "leave entitlement earned leave sick leave days per year annual leave types policy how many total",
    "leaves do i have": "leave entitlement earned leave sick leave days per year annual leave types policy balance",
    "leave do i have": "leave entitlement earned leave sick leave days per year annual leave types policy balance",
    "remaining leave": "leave balance remaining days entitlement HiNext Workday portal check view",
    "leave balance": "leave balance remaining days entitlement HiNext Workday portal check view",
    "bereavement": "bereavement leave compassionate leave death family member funeral days off immediate indirect",
    "compassionate leave": "bereavement leave compassionate leave death family member funeral days off immediate indirect",
    "funeral": "bereavement leave compassionate leave death family member funeral days off immediate indirect",
    "passed away": "bereavement leave compassionate leave death family member funeral days off immediate indirect",
    "fpto": "flexible paid time off FPTO leave absence vacation annual leave entitlement policy",
    "sabbatical": "sabbatical leave extended leave career break leave of absence types",
    "leave cycle": "leave cycle financial year April March annual leave calculation period earned leave policy",
    "leave policy": "leave policy earned leave sick leave maternity paternity bereavement marriage compensatory annual entitlement",
    "paternity leave": "paternity leave paternal leave male employees days father childbirth adoption parental",
    "paternal leave": "paternity leave paternal leave male employees days father childbirth adoption parental",
    "retroactive leave": "retroactive leave apply after emergency advance application inform manager 2 working days",
    "apply leave after": "retroactive leave apply after emergency advance application inform manager 2 working days",
    "holiday": "vacation leave pto public holiday calendar",
    # ── Travel & Expense ─────────────────────────────────────────────
    "t&e": "travel and expense reimbursement business travel policy",
    "travel expense": "travel and expense reimbursement business travel policy",
    "reimbursement": "travel and expense reimbursement business travel claim submit Amex GBT concur Excelity portal",
    "travel reimbursement": "travel reimbursement expense claim submit Amex GBT concur Excelity portal process policy global",
    "travel": "travel and expense business travel reimbursement",
    "relocation": "relocation transfer move accommodation housing allowance assignment policy support",
    "accommodation": "relocation accommodation housing allowance transfer assignment policy support",
    # ── IT tools ─────────────────────────────────────────────────────
    "outlook": "IT Service Desk contact support email technical issue AskNow",
    "vpn": "IT Service Desk contact support VPN remote access technical",
    # ── General ──────────────────────────────────────────────────────
    "eligible": "eligibility criteria authorized requirements entitlement policy",
    "employment letter": "employment letter verification certificate proof job confirmation HR request AskNow",
    "employment verification": "employment letter verification certificate proof job confirmation HR request AskNow",
    "employment certificate": "employment letter verification certificate proof job confirmation HR request AskNow",
    "offer letter": "offer letter onboarding new joiner documents AskNow GPS ticket pre-2019",
    "resignation": "resignation resign notice period termination separation exit process submit letter end employment HiNext",
    "final paycheck": "final paycheck settlement last salary full and final payment termination separation payroll",
    "full and final": "final paycheck settlement last salary full and final payment termination separation payroll",
    "payroll contact": "payroll contact email phone number team HR department enquiries pay salary",
    "salary range": "salary range compensation pay grade band structure total rewards",
    "salary structure": "salary range compensation pay grade band structure total rewards",
    "compensation": "salary range compensation pay grade band structure total rewards benefits",
    "working hours": "working hours office timing shift schedule work schedule start end time flexible",
    "work hours": "working hours office timing shift schedule work schedule start end time flexible",
    "vip payout": "Variable Incentive Plan VIP bonus payout schedule timeline payment cycle",
    "variable incentive": "Variable Incentive Plan VIP bonus payout schedule timeline payment cycle",
    "bonus payout": "Variable Incentive Plan VIP bonus payout schedule timeline payment cycle",
    "hrbp": "HRBP HR Business Partner people partner contact support escalation",
    "hr business partner": "HRBP HR Business Partner people partner contact support escalation",
    "project tagging": "project tagging assignment resource allocation scheduling bench untagged",
    "not tagged": "project tagging assignment resource allocation scheduling bench untagged schedulingx@hitachidigital.com",
    "probation": "probation probationary period confirmation bonus eligibility employee status",
    "visa": "visa support letter company sponsor work permit immigration HR",
    "loan": "employee loan emergency loan salary advance financial assistance policy",
    "emergency loan": "employee loan emergency loan salary advance maximum amount eligibility",
    "tax": "tax management transfer pricing tax policy tax compliance",
    "tp audit": "transfer pricing audit tax compliance notification process",
    "transfer pricing": "transfer pricing audit tax management TP policy",
    "social media": "social media conduct policy personal professional content guidelines",
    "overtime": "overtime policy working hours extra hours compensation",
    "insurance": "health insurance medical insurance auto-renewal policy healthcare provider",
    "health insurance": "health insurance medical insurance auto-renewal policy premium coverage",
    "healthcare": "healthcare provider hospital insurance network medical coverage",
    "bullying": "bullying harassment workplace violence misconduct behavior policy",
    "conflict of interest": "conflict of interest perceived actual disclosure policy compliance",
    "risk management": "risk management hazard identification assessment control workplace safety",
    "cloud security": "cloud security management product owner security standard policy",
    "concession": "customer concession warranty service support policy",
    "retirement": "retirement benefits pension plan contribution employer employee",
    "disconnect": "right to disconnect working hours after hours policy",
}


def expand_query(query: str) -> str:
    """Expand query with domain-specific synonyms for better retrieval.

    Collects ALL matching expansions (up to 3) so that a query like
    "How many casual leaves ... carry forward?" gets both the casual-leave
    AND carry-forward expansions instead of only the first match.
    """
    q = query.lower()
    matched: list[str] = []
    seen_tokens: set[str] = set()
    for abbr, expansion in EXPANSIONS.items():
        if abbr in q:
            # Deduplicate expansion tokens to avoid bloating the query
            new_tokens = [t for t in expansion.split() if t.lower() not in seen_tokens]
            if new_tokens:
                matched.append(" ".join(new_tokens))
                seen_tokens.update(t.lower() for t in new_tokens)
            if len(matched) >= 3:
                break
    if matched:
        return f"{query} {' '.join(matched)}"
    return query


def _combined_analysis(
    query_en: str,
    history_text_en: str,
    home_location: str,
    original_text: str = None,
    original_lang: str = None,
) -> dict:
    """
    Single LLM call that replaces 5+ separate calls:
      - follow-up detection
      - intent classification
      - query rewriting (if follow-up)
      - geographic context extraction
      - employee name detection
      - translation correction (when query was machine-translated)

    Returns a JSON dict with all results.
    """
    has_history = bool(history_text_en and history_text_en.strip())
    history_block = (
        f"Conversation history:\n{history_text_en[-1500:]}"
        if has_history
        else "No conversation history."
    )

    # When the query was translated from another language, include the original
    # text so the LLM can verify/correct machine-translation errors.
    is_translated = bool(original_text and original_lang and original_lang != "en")
    translation_block = ""
    translation_field = ""
    translation_rule = ""
    if is_translated:
        translation_block = (
            f'\nOriginal query ({original_lang}): "{original_text}"'
            f"\nNote: The English query above was machine-translated. It may contain errors."
        )
        translation_field = ',\n  "corrected_translation": "corrected English translation of the original query"'
        translation_rule = """
- corrected_translation: You can read the original non-English text. If the machine translation is inaccurate or misleading
  (e.g. HR terms mistranslated — "leaves" translated as "tickets", etc.), provide a corrected, natural English translation.
  If the machine translation is acceptable, return it unchanged. This is an HR assistant context, so prefer HR terminology."""

    prompt = f"""You are an HR query analyzer. Analyze the user query and return a JSON object.

{history_block}

User query: "{query_en}"{translation_block}
User home location: {home_location}

Return ONLY a JSON object with these exact keys:
{{
  "is_followup": true/false (does the query depend on conversation history?),
  "intent": "concise" or "detailed" (does the user want a brief or detailed answer?),
  "rewritten_query": "standalone version of the query" (rewrite follow-ups to be self-contained; if not a follow-up, return the original query; do NOT add country names unless explicitly in the query),
  "target_region": "country or region name" (which country/region is the query ABOUT? See rules below),
  "mentioned_employee": "employee name" or null (is a specific person named in the query?){translation_field}
}}

Rules:
- is_followup: True only if the query references prior context (pronouns like "it", "that", "these", or builds on prior Q&A).
- intent: "concise" for summary/brief requests; "detailed" for everything else.
- rewritten_query: Make it independently understandable. Keep original wording when possible.
- target_region:
  * Extract the country/region the query is EXPLICITLY about.
  * If the query mentions a specific country (e.g. "in Poland", "for India", "Poland policy", "holidays in Japan"), return that country name.
  * If the query asks about "my" policies without mentioning any country (e.g. "my leave", "my benefits", "how to apply leave"), return "Global".
  * Do NOT assume the user's home location as the target. Only use explicit country/region mentions from the query text.
  * Examples: "What is leave policy in Poland?" → "Poland", "What are my benefits?" → "Global", "How many holidays in Japan?" → "Japan", "maternity leave for Canada" → "Canada"
- mentioned_employee: Extract the person's name ONLY if the user is asking about a specific OTHER employee (e.g. "What is John's leave balance?", "Show me reports for Sarah"). Do NOT extract:
  * The user's own name (e.g. a signature at the end of a message like "Regards, Kei Higuchi")
  * Names that are part of a document/certificate request (e.g. "issue an employment certificate" — the user is asking FOR THEMSELVES)
  * Generic role titles (e.g. "my manager", "HR representative")
  If unsure, return null.
{translation_rule}
Return ONLY valid JSON, no markdown."""

    for attempt in range(2):
        try:
            model = _get_qu_model()
            text = model.generate_content(prompt).text.strip()
            text = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()
            result = json.loads(text)
            out = {
                "is_followup": bool(result.get("is_followup", False)),
                "intent": result.get("intent", "detailed"),
                "rewritten_query": result.get("rewritten_query", query_en),
                "target_region": result.get("target_region", "Global"),
                "mentioned_employee": result.get("mentioned_employee"),
            }
            # Include corrected translation when present
            if is_translated and result.get("corrected_translation"):
                out["corrected_translation"] = result["corrected_translation"]
            return out
        except Exception as e:
            logger.error(f"Combined analysis error (attempt {attempt + 1}): {e}")
            if attempt == 0:
                import time as _time

                _time.sleep(0.5)  # Brief pause before retry

    # All retries exhausted — return safe defaults
    return {
        "is_followup": False,
        "intent": "detailed",
        "rewritten_query": query_en,
        "target_region": "Global",
        "mentioned_employee": None,
    }


# ─── ADK Tool: full query understanding pipeline ────────────────────────────


def understand_query(
    question: str,
    history_text: str = "",
    history_text_en: str = "",
    home_location: str = "Global",
    teams_metadata: dict = None,
) -> dict:
    """
    Full query understanding pipeline — optimised with a single combined LLM
    call for analysis + parallel language detection.
    """
    # Phase A: translation (API call) + explicit language detection (regex, instant)
    tr = detect_and_translate(question)
    explicit_lang = identify_explicit_language(question)

    query_en = tr["translated_text"]
    orig_lang = tr["original_language_code"]
    lang_code = explicit_lang if explicit_lang else orig_lang

    # Phase B: Small talk check (instant, regex)
    _is_greeting = is_small_talk(query_en)

    if _is_greeting:
        return {
            "query_en": query_en,
            "original_language_code": orig_lang,
            "response_language_code": lang_code,
            "is_greeting": True,
            "is_followup": False,
            "is_translation_request": False,
            "intent": "detailed",
            "expanded_query": query_en,
            "search_query": query_en,
            "target_region": home_location,
            "mentioned_employee": None,
        }

    # Phase C: Single combined LLM call for all analysis
    #          Also passes original text for translation correction when needed.
    analysis = _combined_analysis(
        query_en,
        history_text_en,
        home_location,
        original_text=question if tr["is_translated"] else None,
        original_lang=orig_lang if tr["is_translated"] else None,
    )

    # If the LLM corrected the machine translation, use the corrected version
    if analysis.get("corrected_translation"):
        corrected = analysis["corrected_translation"]
        if corrected.lower() != query_en.lower():
            logger.info(f"Translation corrected by LLM: '{query_en}' → '{corrected}'")
            query_en = corrected

    _is_followup = analysis["is_followup"]
    intent = analysis["intent"]
    mentioned_employee = analysis["mentioned_employee"]
    target_region = analysis["target_region"]

    # Query expansion
    expanded = expand_query(query_en)

    # Use rewritten query for search
    if _is_followup:
        search_query = expand_query(analysis["rewritten_query"])
    else:
        search_query = expanded

    # NOTE: home_loc-based fallback for target_region is handled in orchestrator.py
    # AFTER the real home_loc resolves from BQ (it's not available at this point).

    return {
        "query_en": query_en,
        "original_language_code": orig_lang,
        "response_language_code": lang_code,
        "is_greeting": _is_greeting,
        "is_followup": _is_followup,
        "is_translation_request": False,
        "intent": intent,
        "expanded_query": expanded,
        "search_query": search_query,
        "target_region": target_region,
        "mentioned_employee": mentioned_employee,
    }


# ─── ADK Agent ───────────────────────────────────────────────────────────────

query_understanding_agent = Agent(
    name="query_understanding_agent",
    model="gemini-2.0-flash",
    description="Analyzes raw user queries: translates, detects intent, extracts geography, identifies employees.",
    instruction="""You are the Query Understanding Agent for HD SKYE.
Your job is to analyze user queries and produce structured understanding.
Use the understand_query tool with the question and conversation history.
Return the full result dict.""",
    tools=[understand_query],
)
