"""
fetch_test_emails.py
Queries BigQuery to find VP/executive emails, manager emails,
and their direct reportees for role-based access testing.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from google.cloud import bigquery
from google.oauth2 import service_account

CREDS_PATH = os.path.join(os.path.dirname(__file__), "..", "auth2.json")
PROJECT = "hd-onedata-prod"
DATASET = "hd1d_consumption_hds_hr"
SNAPSHOT = f"{PROJECT}.{DATASET}.hr_worker_snapshot_ds_hyperion_feed_hds_erp_vw"
LIFECYCLE = f"{PROJECT}.{DATASET}.hr_worker_lifecycle_oracle_cp5_vw"


def get_client():
    creds = service_account.Credentials.from_service_account_file(CREDS_PATH)
    return bigquery.Client(project=PROJECT, credentials=creds)


def main():
    client = get_client()

    # ── 1. Find VPs and Executives ──────────────────────────────────────
    print("=" * 70)
    print("VPs / EXECUTIVES (title starts with 'Vice' or 'VP')")
    print("=" * 70)
    vp_query = f"""
        SELECT DISTINCT
            `First Name`, `Last Name`, `Email Address`, `Title`,
            `HR Job Level Code`, `Work Country Desc`
        FROM `{LIFECYCLE}`
        WHERE (LOWER(`Title`) LIKE 'vice%' OR LOWER(`Title`) LIKE 'vp%')
          AND `Email Address` IS NOT NULL
          AND `Email Address` != ''
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY `Email Address`
            ORDER BY `HD1D Updated At` DESC
        ) = 1
        ORDER BY `Title`
        LIMIT 15
    """
    vp_emails = []
    for row in client.query(vp_query).result():
        email = row["Email Address"]
        vp_emails.append(email)
        print(f"  {row['First Name']} {row['Last Name']} | {email} | {row['Title']} | {row['Work Country Desc']}")

    # ── 2. Find Managers (people who have direct reports) ────────────────
    print("\n" + "=" * 70)
    print("MANAGERS (have direct reports, NOT VP/exec)")
    print("=" * 70)
    mgr_query = f"""
        SELECT
            s.MANAGER_EMAIL,
            COUNT(1) as report_count,
            ARRAY_AGG(DISTINCT s.EMPLOYING_COUNTRY IGNORE NULLS) as countries
        FROM `{SNAPSHOT}` s
        WHERE s.MANAGER_EMAIL IS NOT NULL
          AND s.MANAGER_EMAIL != ''
          AND LOWER(s.MANAGER_EMAIL) NOT IN (
              SELECT LOWER(`Email Address`)
              FROM `{LIFECYCLE}`
              WHERE (LOWER(`Title`) LIKE 'vice%' OR LOWER(`Title`) LIKE 'vp%')
                AND `Email Address` IS NOT NULL
              QUALIFY ROW_NUMBER() OVER (
                  PARTITION BY `Email Address`
                  ORDER BY `HD1D Updated At` DESC
              ) = 1
          )
        GROUP BY s.MANAGER_EMAIL
        HAVING COUNT(1) BETWEEN 2 AND 15
        ORDER BY report_count DESC
        LIMIT 10
    """
    mgr_emails = []
    for row in client.query(mgr_query).result():
        email = row["MANAGER_EMAIL"]
        mgr_emails.append(email)
        countries = ", ".join(row["countries"][:5]) if row["countries"] else "N/A"
        print(f"  {email} | {row['report_count']} reports | Countries: {countries}")

    # ── 3. For top 3 VPs - show their reportees ─────────────────────────
    print("\n" + "=" * 70)
    print("VP REPORTEES (direct reports of first 3 VPs)")
    print("=" * 70)
    for vp_email in vp_emails[:3]:
        print(f"\n  >> Reports under VP: {vp_email}")
        rep_query = f"""
            SELECT EMPLOYEE_NAME, EMAIL_ADDRESS, EMPLOYING_COUNTRY, MANAGER_EMAIL
            FROM `{SNAPSHOT}`
            WHERE LOWER(MANAGER_EMAIL) = LOWER(@mgr)
            LIMIT 10
        """
        jc = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("mgr", "STRING", vp_email)]
        )
        for row in client.query(rep_query, job_config=jc).result():
            print(f"     {row['EMPLOYEE_NAME']} | {row['EMAIL_ADDRESS']} | {row['EMPLOYING_COUNTRY']}")

    # ── 4. For top 3 Managers - show their reportees ─────────────────────
    print("\n" + "=" * 70)
    print("MANAGER REPORTEES (direct reports of first 3 managers)")
    print("=" * 70)
    for mgr_email in mgr_emails[:3]:
        print(f"\n  >> Reports under Manager: {mgr_email}")
        rep_query = f"""
            SELECT EMPLOYEE_NAME, EMAIL_ADDRESS, EMPLOYING_COUNTRY
            FROM `{SNAPSHOT}`
            WHERE LOWER(MANAGER_EMAIL) = LOWER(@mgr)
            LIMIT 10
        """
        jc = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("mgr", "STRING", mgr_email)]
        )
        for row in client.query(rep_query, job_config=jc).result():
            print(f"     {row['EMPLOYEE_NAME']} | {row['EMAIL_ADDRESS']} | {row['EMPLOYING_COUNTRY']}")

    # ── 5. Find a few "above" / Executive-level ──────────────────────────
    print("\n" + "=" * 70)
    print("EXECUTIVES (HR Job Level E2-E5)")
    print("=" * 70)
    exec_query = f"""
        SELECT DISTINCT
            `First Name`, `Last Name`, `Email Address`, `Title`,
            `HR Job Level Code`, `Work Country Desc`
        FROM `{LIFECYCLE}`
        WHERE `HR Job Level Code` IN ('E2', 'E3', 'E4', 'E5')
          AND `Email Address` IS NOT NULL
          AND `Email Address` != ''
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY `Email Address`
            ORDER BY `HD1D Updated At` DESC
        ) = 1
        ORDER BY `HR Job Level Code`
        LIMIT 10
    """
    for row in client.query(exec_query).result():
        print(f"  {row['First Name']} {row['Last Name']} | {row['Email Address']} | {row['Title']} | Level: {row['HR Job Level Code']} | {row['Work Country Desc']}")

    # ── 6. Regular employees (NOT managers, NOT VP/exec) ─────────────────
    print("\n" + "=" * 70)
    print("REGULAR EMPLOYEES (not managers, not VP/exec - for comparison)")
    print("=" * 70)
    emp_query = f"""
        SELECT DISTINCT
            l.`First Name`, l.`Last Name`, l.`Email Address`, l.`Title`, l.`Work Country Desc`
        FROM `{LIFECYCLE}` l
        WHERE l.`Email Address` IS NOT NULL
          AND l.`Email Address` != ''
          AND LOWER(COALESCE(l.`Title`, '')) NOT LIKE 'vice%'
          AND LOWER(COALESCE(l.`Title`, '')) NOT LIKE 'vp%'
          AND COALESCE(l.`HR Job Level Code`, '') NOT IN ('E2', 'E3', 'E4', 'E5')
          AND LOWER(l.`Email Address`) NOT IN (
              SELECT DISTINCT LOWER(MANAGER_EMAIL)
              FROM `{SNAPSHOT}`
              WHERE MANAGER_EMAIL IS NOT NULL
          )
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY l.`Email Address`
            ORDER BY l.`HD1D Updated At` DESC
        ) = 1
        LIMIT 5
    """
    for row in client.query(emp_query).result():
        print(f"  {row['First Name']} {row['Last Name']} | {row['Email Address']} | {row['Title']} | {row['Work Country Desc']}")

    print("\n" + "=" * 70)
    print("DONE - Use these emails to test role-based access in SKYE")
    print("=" * 70)


if __name__ == "__main__":
    main()
