#!/usr/bin/env python3
"""
Skye 2.0 — Automated QA Test Runner

Reads test prompt files, sends each question to the running Skye agent,
and saves structured JSON + human-readable responses to an output directory.

Usage:
    python scripts/run_tests.py                      # run all test files
    python scripts/run_tests.py --file Testing_Prompts.txt  # run one file
    python scripts/run_tests.py --dry-run             # parse only, don't send
    python scripts/run_tests.py --parallel 3          # 3 concurrent requests
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests

# ── Defaults ────────────────────────────────────────────────────────────────

AGENT_URL = os.getenv("SKYE_AGENT_URL", "http://localhost:8391")
CHAT_ENDPOINT = f"{AGENT_URL}/chat"
NEW_CHAT_ENDPOINT = f"{AGENT_URL}/new-chat"

DEFAULT_USER_METADATA = {
    "email": "anuj.karn@hitachidigital.com",
    "Country": "India",
    "usageLocation": "IN",
    "officeLocation": "IN Pune - Magarpatta",
}

# Role-based questions need specific user metadata
ROLE_METADATA = {
    "vinay": {
        "email": "vinay.kumar@hitachidigital.com",
        "Country": "India",
        "usageLocation": "IN",
        "officeLocation": "IN Pune - Magarpatta",
    },
    "maria": {
        "email": "maria.luna@hitachidigital.com",
        "Country": "US",
        "usageLocation": "US",
        "officeLocation": "US California",
    },
    "boddhayan": {
        "email": "boddhayan.bhowmick@hitachidigital.com",
        "Country": "India",
        "usageLocation": "IN",
        "officeLocation": "IN Pune - Magarpatta",
    },
    "dorota": {
        "email": "dorota.pajor@hitachidigital.com",
        "Country": "Poland",
        "usageLocation": "PL",
        "officeLocation": "PL Warsaw",
    },
    "priya": {
        "email": "priya.chakraborty@hitachidigital.com",
        "Country": "India",
        "usageLocation": "IN",
        "officeLocation": "IN Pune - Magarpatta",
    },
    "pavani": {
        "email": "pavani.gunnamanidi@hitachidigital.com",
        "Country": "India",
        "usageLocation": "IN",
        "officeLocation": "IN Pune - Magarpatta",
    },
    "weronika": {
        "email": "weronika.wolek@hitachidigital.com",
        "Country": "Poland",
        "usageLocation": "PL",
        "officeLocation": "PL Warsaw",
    },
}

# Test file registry — maps filename to metadata
TEST_FILES: list[dict] = [
    {
        "path": "Testing Prompts/Testing_Prompts.txt",
        "name": "english_policy",
        "description": "53 English policy questions (P-Card, T&E, anti-bribery, gifts)",
    },
    {
        "path": "Testing Prompts/Location_testing.txt",
        "name": "location",
        "description": "5 location-specific questions (Colombia, UK, HR)",
    },
    {
        "path": "Testing Prompts/Payroll_questions.txt",
        "name": "payroll_te",
        "description": "16 payroll + T&E questions",
    },
    {
        "path": "Testing Prompts/Prompts_all_scenarios.txt",
        "name": "all_scenarios",
        "description": "17 mixed-scenario questions (DPN, security, grievance, Spanish, Tamil)",
    },
    {
        "path": "Testing Prompts/Questions_for_UAT.txt",
        "name": "uat_multilang",
        "description": "Multi-language UAT (English, Japanese, Kannada, German)",
    },
    {
        "path": "Testing Prompts/Top Questions.txt",
        "name": "top_questions",
        "description": "6 top questions (T&E, P-card, Payroll)",
    },
    {
        "path": "Testing Prompts/Welcome_questions.txt",
        "name": "welcome",
        "description": "5 welcome/general questions",
    },
    {
        "path": "Testing Prompts/Testing_Prompts_German.txt",
        "name": "german",
        "description": "53 German policy questions",
    },
    {
        "path": "Testing Prompts/Testing_Prompts_Japanese.txt",
        "name": "japanese",
        "description": "58 Japanese policy questions",
    },
    {
        "path": "Testing Prompts/Testing_Prompts_Tamil.txt",
        "name": "tamil",
        "description": "53 Tamil policy questions",
    },
    {
        "path": "Role based questions.txt",
        "name": "role_based",
        "description": "4 role-based employee lookup questions",
    },
    {
        "path": "Testing Prompts/PCard_questions.txt",
        "name": "pcard_variant",
        "description": "6 P-Card policy questions (variant=pcard)",
        "variant": "pcard",
        "data_scope": "global",
    },
    {
        "path": "Testing Prompts/Payroll_APAC_questions.txt",
        "name": "payroll_variant",
        "description": "14 APAC payroll questions (variant=payroll)",
        "variant": "payroll",
        "data_scope": "regional",
    },
    {
        "path": "Testing Prompts/UAT_Feedback_Apr3.txt",
        "name": "uat_feedback",
        "description": "33 UAT feedback test script questions (India user)",
    },
    {
        "path": "Testing Prompts/UAT_Wrong_KB_Questions.txt",
        "name": "uat_wrong_kb",
        "description": "28 questions that returned wrong KB sources in UAT",
    },
    {
        "path": "Testing Prompts/UAT_Access_Control_Questions.txt",
        "name": "uat_access_control",
        "description": "10 cross-country questions from HR/Global user",
        "metadata_override": "boddhayan",
    },
]


# ── Question Parsing ────────────────────────────────────────────────────────


def parse_questions(filepath: Path) -> list[dict]:
    """
    Extract questions from a test prompt file.
    Handles numbered (1. / 2.) and unnumbered lines.
    Returns list of {"index": int, "question": str, "metadata": dict | None}
    """
    text = filepath.read_text(encoding="utf-8-sig").strip()
    lines = text.splitlines()
    questions: list[dict] = []
    idx = 0

    # Pattern: optional number prefix like "1." or "1:"
    num_pat = re.compile(r"^\d+[\.\):\s]+\s*(.+)")

    # Section headers to skip
    skip_patterns = [
        r"^(Prompts? for Testing|Testfragen|சோதனை|Top Questions)",
        r"^(P-\s?Card|T&E|Payroll|Location|Language|🔹)",
        r"^(Role based questions|Reporting to)",
        r"^(APAC Payroll|Bulk Expense)",
        r"^(UAT\s|Focused Test|Access Control Questions)",
    ]
    skip_re = re.compile("|".join(skip_patterns), re.IGNORECASE)

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if skip_re.match(stripped):
            continue

        # Try numbered format
        m = num_pat.match(stripped)
        if m:
            q = m.group(1).strip()
        else:
            # Unnumbered line — treat as question if it looks like one
            # (has a question mark, or is long enough to be a sentence)
            if len(stripped) > 15 or "?" in stripped:
                q = stripped
            else:
                continue

        if not q:
            continue

        idx += 1
        meta = None

        # For role-based file, assign metadata based on context
        if (
            "role_based" in filepath.stem.lower()
            or "role based" in filepath.stem.lower()
        ):
            # Questions about Vinay's reportees
            if any(name in q.lower() for name in ["praveen jain"]):
                meta = ROLE_METADATA["vinay"]
            # Questions about Maria Luna's reportees
            elif any(
                name in q.lower()
                for name in [
                    "izabela",
                    "sara terese",
                    "anny",
                    "anny czermińskiej",
                    "anna",
                ]
            ):
                meta = ROLE_METADATA["maria"]

        questions.append({"index": idx, "question": q, "metadata": meta})

    return questions


# ── API Calls ───────────────────────────────────────────────────────────────


def clear_session(session_id: str) -> None:
    """Clear conversation history for a session."""
    try:
        requests.post(
            NEW_CHAT_ENDPOINT,
            json={
                "question": "clear",
                "session_id": session_id,
            },
            timeout=10,
        )
    except Exception:
        pass


def send_question(
    question: str,
    session_id: str,
    metadata: dict | None = None,
    variant: str | None = None,
    data_scope: str = "regional",
) -> dict:
    """Send a question to the Skye agent and return the response."""
    payload = {
        "question": question,
        "session_id": session_id,
        "data_scope": data_scope,
    }
    if metadata:
        payload["teams_metadata"] = metadata
    else:
        payload["teams_metadata"] = DEFAULT_USER_METADATA
    if variant:
        payload["variant"] = variant

    start = time.time()
    try:
        resp = requests.post(CHAT_ENDPOINT, json=payload, timeout=120)
        elapsed = round(time.time() - start, 2)
        if resp.status_code == 200:
            data = resp.json()
            data["_client_elapsed_seconds"] = elapsed
            return data
        else:
            return {
                "error": True,
                "status_code": resp.status_code,
                "detail": resp.text[:500],
                "_client_elapsed_seconds": elapsed,
            }
    except requests.exceptions.Timeout:
        return {
            "error": True,
            "detail": "Request timed out after 120s",
            "_client_elapsed_seconds": round(time.time() - start, 2),
        }
    except Exception as e:
        return {
            "error": True,
            "detail": str(e),
            "_client_elapsed_seconds": round(time.time() - start, 2),
        }


# ── Test Execution ──────────────────────────────────────────────────────────


def run_test_file(
    test_meta: dict,
    base_dir: Path,
    output_dir: Path,
    dry_run: bool = False,
) -> dict:
    """Run all questions from a single test file."""
    filepath = base_dir / test_meta["path"]
    name = test_meta["name"]

    if not filepath.exists():
        print(f"  [SKIP] {test_meta['path']} — file not found")
        return {"name": name, "status": "skipped", "reason": "file not found"}

    questions = parse_questions(filepath)
    print(f"  [{name}] Parsed {len(questions)} questions from {test_meta['path']}")

    if dry_run:
        for q in questions:
            print(f"    Q{q['index']:>3}: {q['question'][:80]}...")
        return {"name": name, "status": "dry_run", "count": len(questions)}

    # Create unique session for this test file
    session_id = f"qa-test-{name}-{int(time.time())}"
    clear_session(session_id)

    variant = test_meta.get("variant")
    data_scope = test_meta.get("data_scope", "regional")
    metadata_override_key = test_meta.get("metadata_override")
    file_metadata = (
        ROLE_METADATA.get(metadata_override_key) if metadata_override_key else None
    )

    results = []
    for q in questions:
        qnum = q["index"]
        qtext = q["question"]
        variant_tag = f" [variant={variant}]" if variant else ""
        print(f"    Q{qnum:>3}: {qtext[:70]}...{variant_tag}", end="", flush=True)

        # Clear session between questions to avoid history contamination
        clear_session(session_id)

        # Use question-level metadata, then file-level override, then default
        q_metadata = q.get("metadata") or file_metadata

        resp = send_question(
            qtext, session_id, q_metadata, variant=variant, data_scope=data_scope
        )
        elapsed = resp.get("_client_elapsed_seconds", "?")
        is_err = resp.get("error", False)

        status_tag = "ERR" if is_err else "OK"
        print(f" [{status_tag} {elapsed}s]")

        results.append(
            {
                "question_number": qnum,
                "question": qtext,
                "user_metadata": q.get("metadata") or DEFAULT_USER_METADATA,
                "response": resp,
            }
        )

    # ── Save results ──

    # JSON (full structured data)
    json_path = output_dir / f"{name}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "test_file": test_meta["path"],
                "description": test_meta["description"],
                "timestamp": datetime.now().isoformat(),
                "agent_url": AGENT_URL,
                "total_questions": len(questions),
                "results": results,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    # Human-readable text
    txt_path = output_dir / f"{name}.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"{'=' * 80}\n")
        f.write(f"Test File: {test_meta['path']}\n")
        f.write(f"Description: {test_meta['description']}\n")
        f.write(f"Timestamp: {datetime.now().isoformat()}\n")
        f.write(f"Total Questions: {len(questions)}\n")
        f.write(f"{'=' * 80}\n\n")

        for r in results:
            resp = r["response"]
            answer = resp.get(
                "answer", resp.get("response", resp.get("detail", "NO ANSWER"))
            )
            sources = resp.get("sources", resp.get("source_urls", []))
            elapsed = resp.get("_client_elapsed_seconds", "?")
            is_err = resp.get("error", False)

            f.write(
                f"--- Q{r['question_number']} {'[ERROR]' if is_err else ''} ({elapsed}s) ---\n"
            )
            f.write(f"Q: {r['question']}\n\n")
            f.write(f"A: {answer}\n")
            if sources:
                f.write(f"\nSources: {json.dumps(sources, ensure_ascii=False)}\n")
            f.write(f"\n{'─' * 60}\n\n")

    print(f"  [{name}] Saved: {json_path.name}, {txt_path.name}")

    return {
        "name": name,
        "status": "completed",
        "count": len(questions),
        "errors": sum(1 for r in results if r["response"].get("error")),
        "avg_time": round(
            sum(r["response"].get("_client_elapsed_seconds", 0) for r in results)
            / max(len(results), 1),
            2,
        ),
    }


# ── Main ────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Skye 2.0 QA Test Runner")
    parser.add_argument(
        "--file", "-f", help="Run only this test file (by name or path fragment)"
    )
    parser.add_argument(
        "--dry-run", "-n", action="store_true", help="Parse questions only, don't send"
    )
    parser.add_argument(
        "--output",
        "-o",
        default="test-results",
        help="Output directory (default: test-results)",
    )
    parser.add_argument(
        "--parallel",
        "-p",
        type=int,
        default=1,
        help="Number of parallel files (default: 1)",
    )
    parser.add_argument(
        "--url", "-u", help="Agent URL (default: http://localhost:8391)"
    )
    args = parser.parse_args()

    if args.url:
        global AGENT_URL, CHAT_ENDPOINT, NEW_CHAT_ENDPOINT
        AGENT_URL = args.url
        CHAT_ENDPOINT = f"{AGENT_URL}/chat"
        NEW_CHAT_ENDPOINT = f"{AGENT_URL}/new-chat"

    base_dir = Path(__file__).resolve().parent.parent  # skye-agent/
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = base_dir / args.output / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Skye 2.0 QA Test Runner")
    print(f"Agent URL: {AGENT_URL}")
    print(f"Output dir: {output_dir}")
    print()

    # Filter test files if --file specified
    files_to_run = TEST_FILES
    if args.file:
        frag = args.file.lower()
        files_to_run = [
            tf
            for tf in TEST_FILES
            if frag in tf["name"].lower() or frag in tf["path"].lower()
        ]
        if not files_to_run:
            print(f"No test file matching '{args.file}'. Available:")
            for tf in TEST_FILES:
                print(f"  {tf['name']:20s} — {tf['path']}")
            sys.exit(1)

    print(f"Running {len(files_to_run)} test file(s):")
    for tf in files_to_run:
        print(f"  - {tf['name']:20s} ({tf['description']})")
    print()

    # Check agent health
    if not args.dry_run:
        try:
            r = requests.get(f"{AGENT_URL}/healthz", timeout=5)
            print(f"Agent health: {r.json()}")
        except Exception as e:
            print(f"WARNING: Agent may not be running — {e}")
            print("Continuing anyway...\n")

    # Run tests
    summaries = []
    total_start = time.time()

    if args.parallel > 1 and len(files_to_run) > 1:
        with ThreadPoolExecutor(max_workers=args.parallel) as pool:
            futures = {
                pool.submit(run_test_file, tf, base_dir, output_dir, args.dry_run): tf
                for tf in files_to_run
            }
            for fut in as_completed(futures):
                summaries.append(fut.result())
    else:
        for tf in files_to_run:
            summaries.append(run_test_file(tf, base_dir, output_dir, args.dry_run))

    total_elapsed = round(time.time() - total_start, 2)

    # ── Summary ──
    print(f"\n{'=' * 60}")
    print(f"QA Test Run Complete — {total_elapsed}s total")
    print(f"{'=' * 60}")

    total_q = 0
    total_err = 0
    for s in summaries:
        count = s.get("count", 0)
        errors = s.get("errors", 0)
        avg = s.get("avg_time", 0)
        total_q += count
        total_err += errors
        status = s["status"]
        print(
            f"  {s['name']:20s} — {status:10s} {count:>3} questions, {errors} errors, avg {avg}s"
        )

    print(f"\n  Total: {total_q} questions, {total_err} errors")
    print(f"  Results saved to: {output_dir}")

    # Save summary
    summary_path = output_dir / "SUMMARY.json"
    with open(summary_path, "w") as f:
        json.dump(
            {
                "timestamp": datetime.now().isoformat(),
                "agent_url": AGENT_URL,
                "total_elapsed_seconds": total_elapsed,
                "total_questions": total_q,
                "total_errors": total_err,
                "files": summaries,
            },
            f,
            indent=2,
        )

    print(f"  Summary: {summary_path}")


if __name__ == "__main__":
    main()
