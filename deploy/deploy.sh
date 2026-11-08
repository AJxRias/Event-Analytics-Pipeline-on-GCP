#!/usr/bin/env bash
# Build the image, upload source data, deploy the mock API (Cloud Run service) and the
# pipeline (Cloud Run job), and schedule it nightly at 02:30 IST with Cloud Scheduler.
# Usage: GCP_PROJECT=my-project bash deploy/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."

: "${GCP_PROJECT:?set GCP_PROJECT}"
REGION="${GCP_REGION:-asia-south1}"
BUCKET="${GCS_BUCKET:-${GCP_PROJECT}-event-data}"
DATASET="${BQ_DATASET:-event_analytics}"
SA="event-pipeline@${GCP_PROJECT}.iam.gserviceaccount.com"
TAG="$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M)"
IMAGE="${REGION}-docker.pkg.dev/${GCP_PROJECT}/event-analytics/pipeline:${TAG}"

echo "==> build $IMAGE with Cloud Build"
gcloud builds submit --project "$GCP_PROJECT" --tag "$IMAGE" .

echo "==> upload raw exports to the landing zone"
[ -f data/raw/registrations.csv ] || python data_gen/generate.py
gcloud storage cp data/raw/*.csv "gs://$BUCKET/raw/"
gcloud storage cp data/api_source/scans.jsonl "gs://$BUCKET/api_source/scans.jsonl"

echo "==> deploy mock badge-scan API (Cloud Run service)"
gcloud run deploy scan-api --project "$GCP_PROJECT" --region "$REGION" --image "$IMAGE" \
  --service-account "$SA" --allow-unauthenticated \
  --command uvicorn --args "api.mock_scan_api:app,--host,0.0.0.0,--port,8080" \
  --set-env-vars "SCAN_DATA_PATH=gs://$BUCKET/api_source/scans.jsonl,MOCK_API_FAILURE_RATE=0.05" \
  --set-secrets "SCAN_API_KEY=scan-api-key:latest" \
  --memory 2Gi --cpu 1 --min-instances 0 --max-instances 2
API_URL="$(gcloud run services describe scan-api --project "$GCP_PROJECT" --region "$REGION" --format 'value(status.url)')"

echo "==> deploy pipeline (Cloud Run job)"
gcloud run jobs deploy event-pipeline --project "$GCP_PROJECT" --region "$REGION" --image "$IMAGE" \
  --service-account "$SA" \
  --set-env-vars "PIPELINE_TARGET=bigquery,GCP_PROJECT=$GCP_PROJECT,BQ_DATASET=$DATASET,BQ_LOCATION=$REGION,GCS_BUCKET=$BUCKET,RAW_SOURCE=gs://$BUCKET/raw,SCAN_API_URL=$API_URL" \
  --set-secrets "SCAN_API_KEY=scan-api-key:latest,PII_SALT=pii-salt:latest" \
  --memory 4Gi --cpu 2 --task-timeout 30m --max-retries 1

echo "==> schedule nightly run (02:30 IST)"
RUN_URI="https://run.googleapis.com/v2/projects/${GCP_PROJECT}/locations/${REGION}/jobs/event-pipeline:run"
if gcloud scheduler jobs describe event-pipeline-nightly --location "$REGION" >/dev/null 2>&1; then
  ACTION=update
else
  ACTION=create
fi
gcloud scheduler jobs "$ACTION" http event-pipeline-nightly --project "$GCP_PROJECT" --location "$REGION" \
  --schedule "30 2 * * *" --time-zone "Asia/Kolkata" --uri "$RUN_URI" --http-method POST \
  --oauth-service-account-email "$SA"

echo "==> first run"
gcloud run jobs execute event-pipeline --project "$GCP_PROJECT" --region "$REGION" --wait

echo "done. query it:  PIPELINE_TARGET=bigquery GCP_PROJECT=$GCP_PROJECT python scripts/run_queries.py"
