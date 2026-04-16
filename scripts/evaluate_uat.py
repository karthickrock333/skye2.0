#!/usr/bin/env python3
"""
Skye 2.0 — UAT Feedback Evaluation Script

Analyzes test results against expected behaviors from UAT feedback.
Checks for:
- Wrong KB/source links (cross-country contamination)
- Access control issues (blocked when should be allowed)
- Incorrect response content
- Missing information responses

Usage:
    python scripts/evaluate_uat.py <results_dir>
    python scripts/evaluate_uat.py test-results/20260409_120000
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# ── Country-to-KB Mapping ───────────────────────────────────────────────────
# Maps country names to valid KB article patterns for that country

COUNTRY_KB_PATTERNS = {
    "india": [
        "KB0018073",  # India Leave Policy
        "KB0018536",  # India holidays/time-off
        "KB0018538",  # India insurance/newborn
        "KB0018981",  # India payroll -- NOTE: testers said this is Argentina
        "KB0019077",  # India Loan Policy
        "KB0018707",  # India onboarding/employee ID
        "KB0018607",  # Ticket submission (global)
        "KB0057381",  # India ID card/facilities
        "KB0019033",  # SKYE FAQ (global)
        "KB0018627",  # Journey/onboarding (global)
        "KB0018836",  # Journey/onboarding (global)
        "KB0018982",  # Performance management
        "KB0018609",  # Professional development
        "KB0018389",  # Benefits -- THIS IS MALAYSIA/UK (wrong for India)
        "KB0018733",  # Benefits -- THIS IS UK (wrong for India)
    ],
    "poland": [
        "KB0015160",  # Poland onboarding/Day 1 docs
        "KB0012513",  # Business cards (global)
        "KB0018607",  # Ticket submission (global)
        "KB0019033",  # SKYE FAQ (global)
        "KB0018627",  # Journey/onboarding (global)
        "KB0018836",  # Journey/onboarding (global)
    ],
}

# KB articles that are WRONG for specific countries (cross-country contamination)
WRONG_KB_FOR_COUNTRY = {
    "india": {
        "KB0018733": "UK benefits KB",
        "KB0018757": "UK holiday FAQ",
        "KB0018734": "UK holiday FAQ",
        "KB0019772": "UK voluntary benefits",
        "KB0018389": "Malaysia benefits KB",
        "KB0018330": "Spain leave policy",
        "KB0018378": "Vietnam leave policy",
        "KB0018410": "Israel KB",
        "KB0018405": "Austria/CEE KB",
        "KB0018342": "Thailand insurance",
        "KB0018925": "Indonesia New Joiner FAQs",
        "KB0018922": "Indonesia working hours",
        "KB0018923": "Indonesia/other country KB",
        "KB0018926": "Other country KB",
        "KB0018339": "Other country leave KB",
        "KB0017452": "UK Absence Policy",
        "KB0017921": "Spain remote working policy",
        "KB0018574": "French policy",
        "KB0018614": "Irrelevant KB",
    },
    "poland": {
        "KB0018707": "India onboarding KB",
        "KB0018733": "UK benefits KB",
        "KB0018757": "UK holiday FAQ",
        "KB0018734": "UK holiday FAQ",
        "KB0019772": "UK voluntary benefits",
        "KB0018410": "Israel KB",
        "KB0018405": "Austria/CEE KB",
        "KB0018574": "French policy",
    },
}

# ── Expected Behaviors per Question ─────────────────────────────────────────

# For UAT_Feedback_Apr3.txt (33 questions, India user context)
UAT_FEEDBACK_CHECKS = {
    1: {  # What is my leave policy in India?
        "should_answer": True,
        "must_contain": ["leave", "india"],
        "must_not_contain_sources": WRONG_KB_FOR_COUNTRY["india"],
    },
    2: {  # What is the payroll cycle in India?
        "should_answer": True,
        "must_contain": ["payroll", "last working day"],
        "must_not_contain_sources": WRONG_KB_FOR_COUNTRY["india"],
    },
    3: {  # Where can I apply leave?
        "should_answer": True,
        "must_contain_any": ["hinext", "workday", "absence", "timeoff", "time off"],
        "must_not_contain": ["peoplepay"],
    },
    4: {  # I joined in 2018 and can't find my offer letter
        "should_answer": True,
        "must_contain_any": ["ticket", "gps", "2019", "before"],
    },
    6: {  # What documents should I bring on Day 1 in Poland?
        # India user correctly blocked from Poland policies
        "should_block": True,
    },
    8: {  # Where can I find my US benefits?
        # India user correctly blocked from US policies
        "should_block": True,
    },
    9: {  # Where can I see my 401(k) info?
        # India user: 401(k) is US-specific. Acceptable response is either access
        # block OR no-info redirect — both prevent leaking US-specific content.
        "must_not_contain_sources": WRONG_KB_FOR_COUNTRY["india"],
    },
    10: {  # What are the standard working hours in Poland?
        # India user correctly blocked from Poland policies
        "should_block": True,
    },
    18: {  # Where are my shoes?
        "should_fallback": True,
        "should_have_no_sources": True,
    },
    23: {  # What is the maternity leave policy for Canada?
        # India user correctly blocked from Canada policies
        "should_block": True,
    },
    25: {  # When will I receive my VIP payout?
        "should_answer": True,
        "must_not_contain_sources": WRONG_KB_FOR_COUNTRY["india"],
    },
    33: {  # What is the leave cycle?
        "should_answer": True,
        "must_contain_any": ["april", "march", "financial year"],
        "must_not_contain": ["calendar year", "january", "december"],
    },
}

# For UAT_Wrong_KB_Questions.txt (28 questions, India user context)
WRONG_KB_CHECKS = {
    1: {  # What are my flexi benefits?
        "should_answer": True,
        "must_not_contain_sources": {"KB0018389": "Malaysia", "KB0018733": "UK"},
    },
    2: {  # Where is the holiday calendar?
        "should_answer": True,
        "must_not_contain_sources": {"KB0018733": "UK", "KB0018757": "UK"},
    },
    3: {  # Employee discounts scheme
        "should_answer": True,
        "must_not_contain_sources": {"KB0019772": "UK benefits"},
    },
    4: {  # What is the appraisal cycle?
        "should_answer": True,
        "must_not_contain_sources": WRONG_KB_FOR_COUNTRY["india"],
    },
    8: {  # Is there any sabbatical leave type?
        "should_answer": True,
        "must_not_contain_sources": {"KB0018378": "Vietnam"},
    },
    9: {  # How to submit my resignation?
        "should_answer": True,
        "must_not_contain_sources": {"KB0018410": "Israel"},
    },
    10: {  # How will my final paycheck be calculated?
        "should_answer": True,
        "must_not_contain_sources": {"KB0018405": "Austria/CEE"},
    },
    11: {  # I want health insurance for my parents
        "should_answer": True,
        "must_not_contain_sources": {"KB0018342": "Thailand"},
    },
    12: {  # Which expense system should I use?
        "should_answer": True,
        "must_not_contain_sources": {"KB0018925": "Indonesia"},
    },
    14: {  # How many leave days remaining?
        "should_answer": True,
        "must_not_contain_sources": {"KB0018330": "Spain"},
    },
    15: {  # What are the working hours for my region?
        "should_answer": True,
        "must_not_contain_sources": {
            "KB0018922": "Indonesia",
            "KB0018925": "Indonesia",
        },
    },
    17: {  # I have exhausted my leave, need emergency time off
        "should_answer": True,
        "must_not_contain_sources": {"KB0017452": "UK Absence Policy"},
    },
    18: {  # Who is my HRBP?
        "should_answer": True,
        "should_have_no_conflicts_of_interest_sources": True,
    },
    19: {  # What happens if a holiday falls on a weekend?
        "should_answer": True,
        "must_not_contain_sources": {"KB0018574": "French policy"},
    },
    20: {  # Could you send me the payroll contact details?
        "should_answer": True,
        "must_contain_any": ["india", "payroll"],
    },
}

# For UAT_Access_Control_Questions.txt (10 questions, HR/Global user - Boddhayan)
ACCESS_CONTROL_CHECKS = {i: {"should_not_block": True} for i in range(1, 11)}

# ── Evaluation Functions ────────────────────────────────────────────────────


def extract_kb_numbers(sources: list | None, source_links: dict | None) -> set[str]:
    """Extract KB article numbers from sources and source_links."""
    kb_numbers = set()
    kb_pat = re.compile(r"KB\d{7}")

    if sources:
        for s in sources:
            for m in kb_pat.finditer(str(s)):
                kb_numbers.add(m.group())

    if source_links:
        for key, url in source_links.items():
            for m in kb_pat.finditer(str(key)):
                kb_numbers.add(m.group())
            for m in kb_pat.finditer(str(url)):
                kb_numbers.add(m.group())

    return kb_numbers


def is_access_blocked(answer: str) -> bool:
    """Check if the response indicates access was blocked."""
    block_phrases = [
        "you do not have permission",
        "do not have access",
        "not authorized",
        "access denied",
        "permission denied",
        "you don't have permission",
    ]
    answer_lower = answer.lower()
    return any(phrase in answer_lower for phrase in block_phrases)


def is_no_info_response(answer: str) -> bool:
    """Check if the response says it doesn't have information."""
    no_info_phrases = [
        "i don't have information",
        "i don't have the information",
        "i do not have information",
        "i cannot provide",
        "please check asknow",
        "i don't have enough information",
    ]
    answer_lower = answer.lower()
    return any(phrase in answer_lower for phrase in no_info_phrases)


def evaluate_result(result: dict, checks: dict, question_num: int) -> dict:
    """Evaluate a single result against its expected checks."""
    check = checks.get(question_num)
    if not check:
        return {"status": "no_check", "severity": "NONE", "issues": []}

    resp = result.get("response", {})
    answer = resp.get("answer", resp.get("response", ""))
    sources = resp.get("sources", [])
    source_links = resp.get("source_links", {})
    kb_numbers = extract_kb_numbers(sources, source_links)
    issues = []

    answer_lower = answer.lower() if answer else ""

    # Check: should not be blocked by access control
    if check.get("should_not_block"):
        if is_access_blocked(answer):
            issues.append(
                {
                    "type": "ACCESS_BLOCKED",
                    "severity": "HIGH",
                    "detail": f"Response blocked by access control: {answer[:200]}",
                }
            )

    # Check: should be blocked (expected access control enforcement)
    if check.get("should_block"):
        if not is_access_blocked(answer):
            issues.append(
                {
                    "type": "ACCESS_NOT_BLOCKED",
                    "severity": "HIGH",
                    "detail": f"Response should be blocked by access control but wasn't: {answer[:200]}",
                }
            )

    # Check: should answer (not redirect or no-info)
    if check.get("should_answer"):
        if is_no_info_response(answer):
            issues.append(
                {
                    "type": "NO_INFO_RESPONSE",
                    "severity": "MEDIUM",
                    "detail": f"Agent said it doesn't have info: {answer[:200]}",
                }
            )
        if is_access_blocked(answer):
            issues.append(
                {
                    "type": "ACCESS_BLOCKED",
                    "severity": "HIGH",
                    "detail": f"Response blocked by access control: {answer[:200]}",
                }
            )

    # Check: should fall back (non-HR question)
    if check.get("should_fallback"):
        if (
            not is_no_info_response(answer)
            and "not" not in answer_lower
            and "can't" not in answer_lower
        ):
            # If it gave a substantive answer to a non-HR question, that's wrong
            if len(answer) > 300 and "hr" not in answer_lower[:100]:
                issues.append(
                    {
                        "type": "SHOULD_FALLBACK",
                        "severity": "LOW",
                        "detail": f"Expected fallback but got substantive response ({len(answer)} chars)",
                    }
                )

    # Check: should have no sources (fallback question)
    if check.get("should_have_no_sources"):
        if sources and len(sources) > 0:
            issues.append(
                {
                    "type": "UNEXPECTED_SOURCES",
                    "severity": "LOW",
                    "detail": f"Fallback response should have no sources but got: {sources}",
                }
            )

    # Check: must contain specific words
    if check.get("must_contain"):
        for word in check["must_contain"]:
            if word.lower() not in answer_lower:
                issues.append(
                    {
                        "type": "MISSING_CONTENT",
                        "severity": "MEDIUM",
                        "detail": f"Response should contain '{word}' but doesn't",
                    }
                )

    # Check: must contain any of these words
    if check.get("must_contain_any"):
        words = check["must_contain_any"]
        if not any(w.lower() in answer_lower for w in words):
            issues.append(
                {
                    "type": "MISSING_CONTENT",
                    "severity": "MEDIUM",
                    "detail": f"Response should contain one of {words} but doesn't",
                }
            )

    # Check: must NOT contain specific words
    if check.get("must_not_contain"):
        for word in check["must_not_contain"]:
            if word.lower() in answer_lower:
                issues.append(
                    {
                        "type": "WRONG_CONTENT",
                        "severity": "HIGH",
                        "detail": f"Response contains incorrect content: '{word}'",
                    }
                )

    # Check: wrong KB sources
    if check.get("must_not_contain_sources"):
        wrong_kbs = check["must_not_contain_sources"]
        for kb_num in kb_numbers:
            if kb_num in wrong_kbs:
                issues.append(
                    {
                        "type": "WRONG_KB_SOURCE",
                        "severity": "HIGH",
                        "detail": f"Response cites wrong KB: {kb_num} ({wrong_kbs[kb_num]})",
                        "kb_number": kb_num,
                        "wrong_reason": wrong_kbs[kb_num],
                    }
                )

    # Check: expected KB sources
    if check.get("expected_kb"):
        for expected in check["expected_kb"]:
            if expected not in kb_numbers:
                issues.append(
                    {
                        "type": "MISSING_KB_SOURCE",
                        "severity": "MEDIUM",
                        "detail": f"Expected KB {expected} not found in sources",
                        "kb_number": expected,
                    }
                )

    # Check: Conflicts of Interest sources (should never appear for HRBP/general questions)
    if check.get("should_have_no_conflicts_of_interest_sources"):
        coi_pattern = re.compile(r"conflict", re.IGNORECASE)
        for s in sources:
            if coi_pattern.search(str(s)):
                issues.append(
                    {
                        "type": "WRONG_KB_SOURCE",
                        "severity": "HIGH",
                        "detail": f"HRBP question cites Conflicts of Interest doc: {s}",
                    }
                )

    status = "PASS" if not issues else "FAIL"
    severity = "NONE"
    if issues:
        severities = [i["severity"] for i in issues]
        if "HIGH" in severities:
            severity = "HIGH"
        elif "MEDIUM" in severities:
            severity = "MEDIUM"
        else:
            severity = "LOW"

    return {"status": status, "severity": severity, "issues": issues}


# ── Generic Source Checks (run on ALL questions) ────────────────────────────


def check_cross_country_contamination(result: dict, user_country: str) -> list:
    """Check if any sources are from a different country than the user."""
    resp = result.get("response", {})
    sources = resp.get("sources", [])
    source_links = resp.get("source_links", {})
    kb_numbers = extract_kb_numbers(sources, source_links)
    issues = []

    wrong_kbs = WRONG_KB_FOR_COUNTRY.get(user_country.lower(), {})
    for kb_num in kb_numbers:
        if kb_num in wrong_kbs:
            issues.append(
                {
                    "type": "CROSS_COUNTRY_KB",
                    "severity": "HIGH",
                    "detail": f"Source {kb_num} is for {wrong_kbs[kb_num]}, but user is in {user_country}",
                    "kb_number": kb_num,
                }
            )

    return issues


# ── Report Generation ───────────────────────────────────────────────────────


def generate_report(
    results_dir: Path,
    test_name: str,
    checks: dict,
    user_country: str = "India",
) -> dict:
    """Generate evaluation report for a test file."""
    json_path = results_dir / f"{test_name}.json"
    if not json_path.exists():
        return {"name": test_name, "status": "not_found"}

    with open(json_path) as f:
        data = json.load(f)

    all_issues = []
    question_results = []

    for r in data.get("results", []):
        qnum = r["question_number"]
        question = r["question"]
        resp = r.get("response", {})
        answer = resp.get("answer", resp.get("response", ""))
        sources = resp.get("sources", [])

        # Run specific checks
        eval_result = evaluate_result(r, checks, qnum)

        # Run generic cross-country check
        cc_issues = check_cross_country_contamination(r, user_country)
        eval_result["issues"].extend(cc_issues)
        if cc_issues:
            eval_result["status"] = "FAIL"
            if eval_result["severity"] == "NONE":
                eval_result["severity"] = "HIGH"

        qr = {
            "question_number": qnum,
            "question": question,
            "answer_preview": answer[:300] if answer else "",
            "sources": sources,
            "kb_numbers": list(
                extract_kb_numbers(sources, resp.get("source_links", {}))
            ),
            "evaluation": eval_result,
        }
        question_results.append(qr)
        all_issues.extend(eval_result["issues"])

    # Aggregate stats
    total = len(question_results)
    passed = sum(1 for qr in question_results if qr["evaluation"]["status"] == "PASS")
    failed = sum(1 for qr in question_results if qr["evaluation"]["status"] == "FAIL")
    no_check = sum(
        1 for qr in question_results if qr["evaluation"]["status"] == "no_check"
    )

    # Issue breakdown
    issue_types = {}
    for issue in all_issues:
        t = issue["type"]
        issue_types[t] = issue_types.get(t, 0) + 1

    return {
        "name": test_name,
        "total_questions": total,
        "passed": passed,
        "failed": failed,
        "no_check": no_check,
        "issue_types": issue_types,
        "total_issues": len(all_issues),
        "question_results": question_results,
    }


def print_report(report: dict) -> None:
    """Print a formatted evaluation report."""
    name = report["name"]
    print(f"\n{'=' * 70}")
    print(f"  Evaluation Report: {name}")
    print(f"{'=' * 70}")

    if report.get("status") == "not_found":
        print(f"  Results file not found for '{name}'")
        return

    total = report["total_questions"]
    passed = report["passed"]
    failed = report["failed"]
    print(f"  Total: {total} | Passed: {passed} | Failed: {failed}")
    print(f"  Issue types: {report['issue_types']}")
    print()

    # Print failed questions
    for qr in report["question_results"]:
        ev = qr["evaluation"]
        if ev["status"] == "FAIL":
            print(f"  FAIL Q{qr['question_number']}: {qr['question']}")
            print(f"    Sources: {qr['kb_numbers']}")
            for issue in ev["issues"]:
                sev = issue["severity"]
                print(f"    [{sev}] {issue['type']}: {issue['detail']}")
            print()


def main():
    if len(sys.argv) < 2:
        # Find the latest results directory
        base = Path(__file__).resolve().parent.parent / "test-results"
        if not base.exists():
            print("No test-results directory found. Run tests first.")
            sys.exit(1)
        dirs = sorted(base.iterdir())
        if not dirs:
            print("No test results found. Run tests first.")
            sys.exit(1)
        results_dir = dirs[-1]
    else:
        results_dir = Path(sys.argv[1])

    if not results_dir.exists():
        print(f"Results directory not found: {results_dir}")
        sys.exit(1)

    print(f"Evaluating results in: {results_dir}")

    # Run evaluations
    reports = []

    # UAT Feedback (India user, default metadata)
    report = generate_report(results_dir, "uat_feedback", UAT_FEEDBACK_CHECKS, "India")
    if report.get("status") != "not_found":
        reports.append(report)
        print_report(report)

    # Wrong KB Questions (India user)
    report = generate_report(results_dir, "uat_wrong_kb", WRONG_KB_CHECKS, "India")
    if report.get("status") != "not_found":
        reports.append(report)
        print_report(report)

    # Access Control (HR/Global user - Boddhayan)
    report = generate_report(
        results_dir, "uat_access_control", ACCESS_CONTROL_CHECKS, "India"
    )
    if report.get("status") != "not_found":
        reports.append(report)
        print_report(report)

    # ── Overall Summary ──
    print(f"\n{'=' * 70}")
    print(f"  OVERALL SUMMARY")
    print(f"{'=' * 70}")

    total_q = sum(r["total_questions"] for r in reports)
    total_pass = sum(r["passed"] for r in reports)
    total_fail = sum(r["failed"] for r in reports)
    total_issues = sum(r["total_issues"] for r in reports)

    # Aggregate issue types
    all_issue_types = {}
    for r in reports:
        for t, c in r["issue_types"].items():
            all_issue_types[t] = all_issue_types.get(t, 0) + c

    print(f"  Tests evaluated: {len(reports)}")
    print(f"  Total questions: {total_q}")
    print(f"  Passed: {total_pass} ({100 * total_pass // max(total_q, 1)}%)")
    print(f"  Failed: {total_fail} ({100 * total_fail // max(total_q, 1)}%)")
    print(f"  Total issues: {total_issues}")
    print(f"\n  Issue breakdown:")
    for t, c in sorted(all_issue_types.items(), key=lambda x: -x[1]):
        print(f"    {t}: {c}")

    # Save evaluation report
    eval_path = results_dir / "EVALUATION.json"
    with open(eval_path, "w") as f:
        json.dump(
            {
                "results_dir": str(results_dir),
                "total_questions": total_q,
                "passed": total_pass,
                "failed": total_fail,
                "total_issues": total_issues,
                "issue_types": all_issue_types,
                "reports": reports,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    print(f"\n  Full report saved to: {eval_path}")


if __name__ == "__main__":
    main()
