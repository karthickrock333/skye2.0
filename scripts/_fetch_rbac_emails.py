"""Fetch VPs and executives based on Oracle Job field, and managers with multi-country reports."""
import os
from google.cloud import bigquery
from google.oauth2 import service_account

CREDS_PATH = os.path.join(os.path.dirname(__file__), "..", "auth2.json")
PROJECT = "hd-onedata-prod"
DATASET = "hd1d_consumption_hds_hr"
SNAPSHOT = f"{PROJECT}.{DATASET}.hr_worker_snapshot_ds_hyperion_feed_hds_erp_vw"
LIFECYCLE = f"{PROJECT}.{DATASET}.hr_worker_lifecycle_oracle_cp5_vw"

creds = service_account.Credentials.from_service_account_file(CREDS_PATH)
client = bigquery.Client(project=PROJECT, credentials=creds)

# ── 1. VP / Executives (Oracle Job contains VP/Vice/Director/President/Chief) ──
print("=" * 80)
print("VPs & EXECUTIVES (from Oracle Job field)")
print("=" * 80)
q_vp = f"""
    SELECT DISTINCT
        `First Name`, `Last Name`, `Email Address`, `Oracle Job`,
        `HR Job Level Name`, `Work Country Desc`
    FROM `{LIFECYCLE}`
    WHERE `Email Address` IS NOT NULL AND `Email Address` != ''
      AND (
        LOWER(`Oracle Job`) LIKE 'vp%'
        OR LOWER(`Oracle Job`) LIKE 'vice president%'
        OR LOWER(`Oracle Job`) LIKE '%svp%'
        OR LOWER(`Oracle Job`) LIKE 'chief%'
      )
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY `Email Address`
        ORDER BY `HD1D Updated At` DESC
    ) = 1
    LIMIT 15
"""
vp_emails = []
for row in client.query(q_vp).result():
    email = row["Email Address"]
    vp_emails.append(email)
    print(f"  {row['First Name']} {row['Last Name']} | {email} | {row['Oracle Job']} | {row['Work Country Desc']}")

# ── 2. Directors ────────────────────────────────────────────────────────────
print("\n" + "=" * 80)
print("DIRECTORS (Oracle Job contains 'Director')")
print("=" * 80)
q_dir = f"""
    SELECT DISTINCT
        `First Name`, `Last Name`, `Email Address`, `Oracle Job`, `Work Country Desc`
    FROM `{LIFECYCLE}`
    WHERE `Email Address` IS NOT NULL AND `Email Address` != ''
      AND LOWER(`Oracle Job`) LIKE '%director%'
      AND LOWER(`Oracle Job`) NOT LIKE 'vp%'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY `Email Address`
        ORDER BY `HD1D Updated At` DESC
    ) = 1
    LIMIT 10
"""
dir_emails = []
for row in client.query(q_dir).result():
    email = row["Email Address"]
    dir_emails.append(email)
    print(f"  {row['First Name']} {row['Last Name']} | {email} | {row['Oracle Job']} | {row['Work Country Desc']}")

# ── 3. Managers with multi-country reports (interesting for access control) ──
print("\n" + "=" * 80)
print("MANAGERS WITH MULTI-COUNTRY REPORTS (best for testing cross-country access)")
print("=" * 80)
q_multi = f"""
    SELECT
        MANAGER_EMAIL,
        COUNT(1) as report_count,
        ARRAY_AGG(DISTINCT EMPLOYING_COUNTRY IGNORE NULLS) as countries
    FROM `{SNAPSHOT}`
    WHERE MANAGER_EMAIL IS NOT NULL AND MANAGER_EMAIL != ''
    GROUP BY MANAGER_EMAIL
    HAVING ARRAY_LENGTH(ARRAY_AGG(DISTINCT EMPLOYING_COUNTRY IGNORE NULLS)) > 1
    ORDER BY report_count DESC
    LIMIT 10
"""
multi_mgr_emails = []
for row in client.query(q_multi).result():
    email = row["MANAGER_EMAIL"]
    multi_mgr_emails.append(email)
    countries = ", ".join(row["countries"][:8]) if row["countries"] else "N/A"
    print(f"  {email} | {row['report_count']} reports | Countries: {countries}")

# ── 4. VP Reportees ─────────────────────────────────────────────────────────
if vp_emails:
    print("\n" + "=" * 80)
    print("VP REPORTEES")
    print("=" * 80)
    for vp_email in vp_emails[:3]:
        print(f"\n  >> Under VP: {vp_email}")
        rep_query = f"""
            SELECT EMPLOYEE_NAME, EMAIL_ADDRESS, EMPLOYING_COUNTRY
            FROM `{SNAPSHOT}`
            WHERE LOWER(MANAGER_EMAIL) = LOWER(@mgr)
            LIMIT 8
        """
        jc = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("mgr", "STRING", vp_email)]
        )
        for row in client.query(rep_query, job_config=jc).result():
            print(f"     {row['EMPLOYEE_NAME']} | {row['EMAIL_ADDRESS']} | {row['EMPLOYING_COUNTRY']}")

# ── 5. Multi-country manager reportees ──────────────────────────────────────
if multi_mgr_emails:
    print("\n" + "=" * 80)
    print("MULTI-COUNTRY MANAGER REPORTEES")
    print("=" * 80)
    for mgr_email in multi_mgr_emails[:3]:
        print(f"\n  >> Under Manager: {mgr_email}")
        rep_query = f"""
            SELECT EMPLOYEE_NAME, EMAIL_ADDRESS, EMPLOYING_COUNTRY
            FROM `{SNAPSHOT}`
            WHERE LOWER(MANAGER_EMAIL) = LOWER(@mgr)
            LIMIT 8
        """
        jc = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("mgr", "STRING", mgr_email)]
        )
        for row in client.query(rep_query, job_config=jc).result():
            print(f"     {row['EMPLOYEE_NAME']} | {row['EMAIL_ADDRESS']} | {row['EMPLOYING_COUNTRY']}")

print("\n" + "=" * 80)
print("DONE")
print("=" * 80)
