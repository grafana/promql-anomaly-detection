#!/usr/bin/env bash
# Delete only ml_predictions points tagged model=peer_rf
set -euo pipefail
INFLUX_HOST="${INFLUX_HOST:-https://52.35.251.91:8086}"
TOKEN="${INFLUX_TOKEN:?Set INFLUX_TOKEN}"
ORG="${INFLUX_ORG:-powertech}"
BUCKET="${INFLUX_BUCKET:-powertechdata}"

echo "Delete ml_predictions where model=peer_rf in ${BUCKET}? (yes/no)"
read -r CONFIRM
if [ "$CONFIRM" != "yes" ]; then
  echo "Aborted."
  exit 0
fi

RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
  -k -X POST "${INFLUX_HOST}/api/v2/delete?org=${ORG}&bucket=${BUCKET}" \
  -H "Authorization: Token ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "start": "1970-01-01T00:00:00Z",
    "stop": "2099-01-01T00:00:00Z",
    "predicate": "_measurement=\"ml_predictions\" AND model=\"peer_rf\""
  }')

if [ "$RESPONSE" = "204" ]; then
  echo "✓ peer_rf ml_predictions deleted"
else
  echo "✗ HTTP ${RESPONSE}"
  exit 1
fi
