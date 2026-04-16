"""Quick check: what VP/exec-like titles and job levels exist in the lifecycle table."""
import os
from google.cloud import bigquery
from google.oauth2 import service_account

CREDS_PATH = os.path.join(os.path.dirname(__file__), "..", "auth2.json")
PROJECT = "hd-onedata-prod"
DATASET = "hd1d_consumption_hds_hr"
LIFECYCLE = f"{PROJECT}.{DATASET}.hr_worker_lifecycle_oracle_cp5_vw"

creds = service_account.Credentials.from_service_account_file(CREDS_PATH)
client = bigquery.Client(project=PROJECT, credentials=creds)

# Check distinct titles containing VP/vice/director/SVP/president
print("=== TITLES containing VP/Vice/Director/SVP/President ===")
q1 = f"""
    SELECT DISTINCT `Title`
    FROM `{LIFECYCLE}`
    WHERE `Title` IS NOT NULL
      AND (
        LOWER(`Title`) LIKE '%vice%'
        OR LOWER(`Title`) LIKE '%vp%'
        OR LOWER(`Title`) LIKE '%director%'
        OR LOWER(`Title`) LIKE '%svp%'
        OR LOWER(`Title`) LIKE '%president%'
        OR LOWER(`Title`) LIKE '%chief%'
      )
    LIMIT 30
"""
for row in client.query(q1).result():
    print(f"  {row['Title']}")

# Check HR Job Level codes
print("\n=== DISTINCT HR Job Level Codes ===")
q2 = f"""
    SELECT DISTINCT `HR Job Level Code`, COUNT(1) as cnt
    FROM `{LIFECYCLE}`
    WHERE `HR Job Level Code` IS NOT NULL AND `HR Job Level Code` != ''
    GROUP BY `HR Job Level Code`
    ORDER BY `HR Job Level Code`
"""
for row in client.query(q2).result():
    print(f"  {row['HR Job Level Code']}: {row['cnt']} employees")

# Check Oracle Job field for VP-like titles
print("\n=== Oracle Job containing VP/Vice/Director ===")
q3 = f"""
    SELECT DISTINCT `Oracle Job`
    FROM `{LIFECYCLE}`
    WHERE `Oracle Job` IS NOT NULL
      AND (
        LOWER(`Oracle Job`) LIKE '%vice%'
        OR LOWER(`Oracle Job`) LIKE '%vp%'
        OR LOWER(`Oracle Job`) LIKE '%director%'
      )
    LIMIT 20
"""
for row in client.query(q3).result():
    print(f"  {row['Oracle Job']}")
