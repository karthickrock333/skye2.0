"""
feedback_agent.py
==================
Handles user feedback submission to Firestore.
"""
from google.adk.agents import Agent
from config import PROJECT_ID
from datetime import datetime
import logging

logger = logging.getLogger("HD_SKYE_AGENT")


def submit_feedback(session_id: str, helpful: bool, comment: str = "") -> dict:
    """Submit user feedback to Firestore."""
    try:
        from google.cloud import firestore
        db = firestore.Client(project=PROJECT_ID)
        db.collection("user_feedback").add({
            "session_id": session_id,
            "helpful": helpful,
            "comment": comment,
            "timestamp": datetime.utcnow(),
        })
        return {"status": "success", "message": "Feedback submitted successfully"}
    except Exception as e:
        logger.error(f"Error submitting feedback: {e}")
        return {"status": "error", "message": str(e)}


feedback_agent = Agent(
    name="feedback_agent",
    model="gemini-2.0-flash",
    description="Handles user feedback submission to Firestore.",
    instruction="Submit user feedback using the submit_feedback tool.",
    tools=[submit_feedback],
)
