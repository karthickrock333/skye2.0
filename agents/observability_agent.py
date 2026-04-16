"""
observability_agent.py
=======================
Structured logging, timing, and context box printing for debugging/monitoring.
"""
import time
import logging
import functools
from google.adk.agents import Agent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("HD_SKYE_AGENT")


def time_logger(func):
    """Decorator that logs function execution time."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        logger.info(f"⏱ {func.__name__} took {time.time() - start:.4f}s")
        return result
    return wrapper


def log_user_context(
    user_name: str,
    user_email: str,
    roles: dict,
    home_loc: str,
    allowed_locations: list,
    target_region: str,
    data_scope: str = "regional",
) -> str:
    """Log a structured user context box. Returns the formatted string."""
    role_parts = []
    if roles.get("is_manager"):
        role_parts.append("Manager")
    if roles.get("is_vp"):
        role_parts.append("VP")
    if roles.get("is_executive"):
        role_parts.append("Executive")
    role_str = ", ".join(role_parts) if role_parts else "Employee"

    lines = [
        f"+{'─' * 58}+",
        f"| {'USER CONTEXT':^56} |",
        f"+{'─' * 58}+",
        f"|  Name: {user_name:<49} |",
        f"|  Email: {(user_email or 'N/A'):<48} |",
        f"|  Role: {role_str:<49} |",
        f"|  Data Scope: {data_scope.upper():<43} |",
        f"|  Home Location: {home_loc:<40} |",
        f"|  Allowed Regions: {', '.join(allowed_locations):<39} |",
        f"|  Target Region: {target_region.upper():<42} |",
        f"+{'─' * 58}+",
    ]
    box = "\n".join(lines)
    logger.info(f"\n{box}")
    return box


def log_agent_step(agent_name: str, step: str, details: str = "") -> str:
    """Log an agent pipeline step."""
    msg = f"[{agent_name}] {step}"
    if details:
        msg += f" | {details}"
    logger.info(msg)
    return msg


observability_agent = Agent(
    name="observability_agent",
    model="gemini-2.0-flash",
    description="Structured logging and observability for the agentic pipeline.",
    instruction="Log pipeline steps and user context using the provided tools.",
    tools=[log_user_context, log_agent_step],
)
