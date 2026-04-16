"""
gcs_tools.py - Google Cloud Storage helpers (upload, download, signed URLs).
"""

import datetime
import logging
import re

import google.auth
import google.auth.transport.requests
from google.auth import compute_engine
from google.cloud import storage
from config import (
    PROJECT_ID,
    GCS_BUCKET_NAME,
    GCS_DOCUMENTS_PREFIX,
    GCS_SERVICENOW_KB_PREFIX,
)

logger = logging.getLogger(__name__)

# Regex to detect ServiceNow KB filenames: ServiceNow_KB_KB0018536.html
_SERVICENOW_KB_RE = re.compile(r"^ServiceNow_KB_(KB\d+)\.html$")


def upload_blob(bucket_name: str, source_file: str, dest_blob: str):
    client = storage.Client(project=PROJECT_ID)
    bucket = client.bucket(bucket_name)
    bucket.blob(dest_blob).upload_from_filename(source_file)


def list_blobs(bucket_name: str, prefix: str = None):
    client = storage.Client(project=PROJECT_ID)
    return list(client.list_blobs(bucket_name, prefix=prefix))


def download_blob(bucket_name: str, source_blob: str, dest_file: str):
    client = storage.Client(project=PROJECT_ID)
    client.bucket(bucket_name).blob(source_blob).download_to_filename(dest_file)


def generate_signed_url(blob_name: str, expiration_minutes: int = 15) -> str:
    """Generate a v4 signed URL for a GCS document.

    Lookup order:
    1. For ServiceNow KB HTML files (ServiceNow_KB_*.html):
       - servicenow_kb_extraction/extracted/{kb_number}_html.txt
       - servicenow_kb_extraction/pdfs/{blob_name}
    2. For regular documents:
       - hd-skye-2.0/Documents/{blob_name}
    3. Legacy fallbacks:
       - pdf/{blob_name}
       - pdf/japanese/{blob_name}
       - {blob_name}  (bucket root)
    """
    client = storage.Client(project=PROJECT_ID)
    bucket = client.bucket(GCS_BUCKET_NAME)

    # Check if this is a ServiceNow KB article
    sn_match = _SERVICENOW_KB_RE.match(blob_name)
    if sn_match:
        kb_number = sn_match.group(1)
        # ServiceNow KB articles: try extracted text first, then PDFs
        candidates = [
            f"{GCS_SERVICENOW_KB_PREFIX}extracted/{kb_number}_html.txt",
            f"{GCS_SERVICENOW_KB_PREFIX}pdfs/{blob_name}",
            f"{GCS_DOCUMENTS_PREFIX}{blob_name}",
        ]
    else:
        # Regular documents: try the main Documents folder first
        candidates = [
            f"{GCS_DOCUMENTS_PREFIX}{blob_name}",
            f"pdf/{blob_name}",
            f"pdf/japanese/{blob_name}",
            blob_name,  # bucket root (legacy)
        ]

    # Find the first existing blob
    blob = None
    for path in candidates:
        candidate_blob = bucket.blob(path)
        if candidate_blob.exists():
            blob = candidate_blob
            logger.info("Found blob at gs://%s/%s", GCS_BUCKET_NAME, path)
            break

    if blob is None:
        logger.warning(
            "Blob not found for %s in any location (tried: %s)",
            blob_name,
            candidates,
        )
        # Fall back to the original name so the error is at least meaningful
        blob = bucket.blob(blob_name)

    expiration = datetime.timedelta(minutes=expiration_minutes)

    # Detect credential type for Cloud Run compatibility.
    # On Cloud Run, compute_engine credentials don't have a private key,
    # so we must use IAM-based signing via the signBlob API.
    credentials, _ = google.auth.default()

    if isinstance(credentials, compute_engine.Credentials):
        auth_request = google.auth.transport.requests.Request()
        credentials.refresh(auth_request)
        service_account_email = getattr(credentials, "service_account_email", None)

        if not service_account_email:
            logger.error("Could not determine service account email on Cloud Run")
            return ""

        return blob.generate_signed_url(
            version="v4",
            expiration=expiration,
            method="GET",
            service_account_email=service_account_email,
            access_token=credentials.token,
        )
    else:
        # Local dev / service account with private key — direct signing
        return blob.generate_signed_url(
            version="v4",
            expiration=expiration,
            method="GET",
        )
