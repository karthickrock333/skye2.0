"""
guardrails_agent.py
====================
Pre-processing guardrails that decide whether the query should be blocked,
redirected, or allowed to proceed:
  - Hitachi Vantara (HV) query detection → out-of-scope response
  - P-Card permission gating (VP/Executive only)
  - Greeting / small-talk early return
"""

from google.adk.agents import Agent
from tools.opco_tools import is_hv_query, is_p_card_query, get_user_opco
from tools.bq_tools import get_user_roles
import logging

logger = logging.getLogger("HD_SKYE_AGENT")

HV_DISCLAIMER = (
    "Note: This system currently provides information for Hitachi Digital, HDS, and GlobalLogic. "
    "Policies for Hitachi Vantara (Antara) are not covered here."
)

GREETING_RESPONSE = (
    "Hello, how can I assist you today? I'm HD SKYE, your Limitless HR companion. "
    "I can help you navigate through HR policies and associated FAQs."
)

THANK_YOU_RESPONSE = (
    "You're welcome! If you have any more questions, feel free to ask. "
    "I'm here to help!"
)

_THANK_WORDS = {"thank", "thanks", "thx", "appreciate", "gracias", "merci", "danke"}


def _is_thank_you(query: str) -> bool:
    """Check if the query is a thank-you message."""
    return any(w in query.lower() for w in _THANK_WORDS)


def apply_guardrails(
    query_en: str,
    user_email: str = None,
    user_opco: str = "Unknown",
    is_greeting: bool = False,
    data_scope: str = "regional",
) -> dict:
    """
    Pre-flight checks on the query.
    Returns:
      {
        "blocked": bool,
        "block_reason": str | None,
        "response": str | None,       # If blocked, the canned response
        "hv_disclaimer": str | None,   # Disclaimer to prepend if HV user
        "proceed": bool                # True if query should continue to retrieval
      }
    """
    is_hv_user = user_opco == "Hitachi Vantara (HV)"
    hv_note = HV_DISCLAIMER if (is_hv_user or is_hv_query(query_en)) else None

    # 1. Greeting / thank-you guard
    if is_greeting:
        response = THANK_YOU_RESPONSE if _is_thank_you(query_en) else GREETING_RESPONSE
        return {
            "blocked": False,
            "block_reason": None,
            "response": response,
            "hv_disclaimer": hv_note,
            "proceed": False,  # No retrieval needed
        }

    # 2. HV query guard
    if is_hv_query(query_en):
        resp = (
            f"{HV_DISCLAIMER}\n\n"
            "I'm sorry, I don't have information about Hitachi Vantara policies. "
            "Please reach out to your local HR team or Hitachi Vantara's internal HR portal."
        )
        return {
            "blocked": True,
            "block_reason": "hv_query",
            "response": resp,
            "hv_disclaimer": hv_note,
            "proceed": False,
        }

    # 3. P-Card permission guard & routing
    if is_p_card_query(query_en):
        # For non-global scope, enforce VP/Executive/Super Admin restriction
        if data_scope != "global":
            roles = get_user_roles(user_email) if user_email else {}
            if not (
                roles.get("is_vp")
                or roles.get("is_executive")
                or roles.get("is_super_admin")
                or roles.get("is_hr_finance")
            ):
                return {
                    "blocked": True,
                    "block_reason": "pcard_permission",
                    "response": "I'm sorry, you do not have permission to access P-Card related information.",
                    "hv_disclaimer": hv_note,
                    "proceed": False,
                    "pcard_authorized": False,
                }
        # P-Card query detected and user is allowed — route to P-Card pipeline
        return {
            "blocked": False,
            "block_reason": None,
            "response": None,
            "hv_disclaimer": hv_note,
            "proceed": True,
            "pcard_authorized": True,
        }

    return {
        "blocked": False,
        "block_reason": None,
        "response": None,
        "hv_disclaimer": hv_note,
        "proceed": True,
        "pcard_authorized": False,
    }


guardrails_agent = Agent(
    name="guardrails_agent",
    model="gemini-2.0-flash",
    description="Pre-processing guardrails: HV blocking, P-Card gating, greeting detection.",
    instruction="""You are the Guardrails Agent.
Evaluate the query against safety/permission rules using apply_guardrails.
If blocked, return the canned response. Otherwise signal proceed=True.""",
    tools=[apply_guardrails],
)
