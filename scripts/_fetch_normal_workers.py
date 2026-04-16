import os
from google.cloud import bigquery
from google.oauth2 import service_account

CREDS_PATH = "auth2.json"
PROJECT = "hd-onedata-prod"
DATASET = "hd1d_consumption_hds_hr"
SNAPSHOT = f"{PROJECT}.{DATASET}.hr_worker_snapshot_ds_hyperion_feed_hds_erp_vw"
LIFECYCLE = f"{PROJECT}.{DATASET}.hr_worker_lifecycle_oracle_cp5_vw"

creds = service_account.Credentials.from_service_account_file(CREDS_PATH)
client = bigquery.Client(project=PROJECT, credentials=creds)

q = f"""
    WITH regular AS (
        SELECT DISTINCT
            l.`First Name`, l.`Last Name`, l.`Email Address`,
            l.`Oracle Job`, l.`Work Country Desc`
        FROM `{LIFECYCLE}` l
        WHERE l.`Email Address` IS NOT NULL
          AND l.`Email Address` != ''
          AND (LOWER(l.`Email Address`) LIKE '%@hitachidigital.com'
               OR LOWER(l.`Email Address`) LIKE '%@hitachids.com')
          AND LOWER(COALESCE(l.`Oracle Job`, '')) NOT LIKE '%vice%'
          AND LOWER(COALESCE(l.`Oracle Job`, '')) NOT LIKE '%vp%'
          AND LOWER(COALESCE(l.`Oracle Job`, '')) NOT LIKE '%director%'
          AND LOWER(COALESCE(l.`Oracle Job`, '')) NOT LIKE '%chief%'
          AND LOWER(COALESCE(l.`Oracle Job`, '')) NOT LIKE '%president%'
          AND LOWER(COALESCE(l.`Oracle Job`, '')) NOT LIKE '%manager%'
          AND LOWER(l.`Email Address`) NOT IN (
              SELECT DISTINCT LOWER(MANAGER_EMAIL)
              FROM `{SNAPSHOT}`
              WHERE MANAGER_EMAIL IS NOT NULL
          )
          AND l.`Work Country Desc` IS NOT NULL
          AND l.`Work Country Desc` != ''
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY l.`Work Country Desc`
            ORDER BY l.`HD1D Updated At` DESC
        ) = 1
    )
    SELECT * FROM regular
    ORDER BY `Work Country Desc`
"""
print("REGULAR EMPLOYEES (not managers, not VP/exec) - diverse countries")
print("=" * 90)
for row in client.query(q).result():
    print(f"  {row['First Name']} {row['Last Name']} | {row['Email Address']} | {row['Oracle Job'] or 'N/A'} | {row['Work Country Desc']}")
