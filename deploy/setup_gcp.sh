#!/usr/bin/env bash
# One-time GCP setup: APIs, Artifact Registry, GCS bucket, BigQuery dataset, service account, secrets.
# Usage: GCP_PROJECT=my-project bash deploy/setup_gcp.sh
set -euo pipefail

: "${GCP_PROJECT:?set GCP_PROJECT}"
REGION="${GCP_REGION:-asia-south1}"                 # Mumbai
BUCKET="${GCS_BUCKET:-${GCP_PROJECT}-event-data}"
DATASET="${BQ_DATASET:-event_analytics}"
SA_NAME="event-pipeline"
SA="${SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com"

gcloud config set project "$GCP_PROJECT"

echo "==> enabling APIs"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  bigquery.googleapis.com storage.googleapis.com cloudscheduler.googleapis.com secretmanager.googleapis.com

echo "==> Artifact Registry repo"
gcloud artifacts repositories create event-analytics --repository-format=docker --location="$REGION" \
  --description="Event analytics pipeline images" 2>/dev/null || echo "   (exists)"

echo "==> GCS bucket gs://$BUCKET (raw landing zone + parquet staging)"
gcloud storage buckets create "gs://$BUCKET" --location="$REGION" --uniform-bucket-level-access 2>/dev/null || echo "   (exists)"
cat > /tmp/lifecycle.json <<'JSON'
{"rule": [{"action": {"type": "Delete"}, "condition": {"age": 30, "matchesPrefix": ["curated/"]}}]}
JSON
gcloud storage buckets update "gs://$BUCKET" --lifecycle-file=/tmp/lifecycle.json   # staged parquet expires after 30 days

echo "==> BigQuery dataset $DATASET"
bq --location="$REGION" mk --dataset --description "Exhibition analytics warehouse" "$GCP_PROJECT:$DATASET" 2>/dev/null || echo "   (exists)"

echo "==> service account $SA (least privilege)"
gcloud iam service-accounts create "$SA_NAME" --display-name="Event analytics pipeline" 2>/dev/null || echo "   (exists)"
for role in roles/bigquery.jobUser roles/run.invoker roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding "$GCP_PROJECT" --member="serviceAccount:$SA" --role="$role" --condition=None >/dev/null
done
bq add-iam-policy-binding --member="serviceAccount:$SA" --role=roles/bigquery.dataEditor "$GCP_PROJECT:$DATASET" >/dev/null
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="serviceAccount:$SA" --role=roles/storage.objectAdmin >/dev/null

echo "==> secrets"
for secret in scan-api-key pii-salt; do
  if ! gcloud secrets describe "$secret" >/dev/null 2>&1; then
    openssl rand -hex 24 | gcloud secrets create "$secret" --data-file=- --replication-policy=automatic
  fi
done

echo "done. next: GCP_PROJECT=$GCP_PROJECT bash deploy/deploy.sh"
