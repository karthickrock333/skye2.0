"""
translation_agent.py
=====================
Handles final response translation: converts the internal English answer
to the user's requested language.  Also handles translation-only requests.
"""

from google.adk.agents import Agent
from agents.query_understanding_agent import translate_text, detect_and_translate
import logging

logger = logging.getLogger("HD_SKYE_AGENT")


def translate_response(answer_en: str, target_language_code: str) -> str:
    """Translate the English answer to the target language using Gemini to preserve Markdown."""
    if target_language_code == "en":
        return answer_en

    logger.info(f"Translating response to {target_language_code} using LLM")
    try:
        from vertexai.generative_models import GenerativeModel
        from config import LLM_MODEL, get_llm_generation_config
        import re

        # Use module-level cached model
        global _trans_model
        try:
            model = _trans_model
        except NameError:
            _trans_model = GenerativeModel(
                LLM_MODEL, generation_config=get_llm_generation_config()
            )
            model = _trans_model
        prompt = f"""You are a translator. Translate ONLY the text between the <translate> tags below from English into the language with ISO code '{target_language_code}'.

RULES:
- Preserve all markdown formatting (bold, bullets, headers) exactly as-is.
- Maintain the same line-break structure.
- Output ONLY the translated text. No preamble, no explanation, no tags.
- Do NOT translate these rules. Do NOT include any rules or instructions in your output.

<translate>
{answer_en}
</translate>"""
        response = model.generate_content(prompt)
        translated = response.text.strip()
        translated = re.sub(r"```(?:\w+)?", "", translated).replace("```", "").strip()
        return translated
    except Exception as e:
        logger.error(f"[translation_agent] LLM translation failed: {e}")
        return translate_text(answer_en, target_language_code)


def handle_translation_request(
    question: str,
    last_answer: str,
    last_sources: list = None,
    last_suggestions: list = None,
) -> dict | None:
    """
    If the user is requesting a translation of the previous response, perform it.
    Returns the translated response dict, or None if not a translation request.
    """
    from agents.query_understanding_agent import (
        is_translation_request,
        identify_explicit_language,
        detect_and_translate as _dat,
    )

    if not is_translation_request(question):
        return None

    target_lang = identify_explicit_language(question)
    if not target_lang:
        tr = _dat(question)
        qlang = tr["original_language_code"]
        if qlang != "en":
            target_lang = qlang
        else:
            lang_names = {
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
            for name, code in lang_names.items():
                if name in question.lower():
                    target_lang = code
                    break

    if not target_lang or not last_answer:
        return None

    translated = translate_response(last_answer, target_lang)
    translated_suggestions = [
        translate_text(s, target_lang) for s in (last_suggestions or [])
    ]

    return {
        "answer": translated,
        "sources": last_sources or [],
        "source_links": {},
        "suggested_questions": translated_suggestions,
        "show_feedback_prompt": False,
    }


translation_agent = Agent(
    name="translation_agent",
    model="gemini-2.0-flash",
    description="Translates final responses to the user's language and handles translation-only requests.",
    instruction="""You are the Translation Agent.
Use translate_response to convert English answers to the user's language.
Use handle_translation_request for pure translation requests of previous answers.""",
    tools=[translate_response, handle_translation_request],
)
