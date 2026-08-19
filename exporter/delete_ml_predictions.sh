#!/bin/bash
# =============================================================================
# PowerTech - Delete ml_predictions from InfluxDB
# Usage: ./delete_ml_predictions.sh
# =============================================================================
INFLUX_HOST="${INFLUX_HOST:-https://52.35.251.91:8086}"
TOKEN="${INFLUX_TOKEN:?Set INFLUX_TOKEN}"
ORG="${INFLUX_ORG:-powertech}"
BUCKET="${INFLUX_BUCKET:-powertechdata}"
echo "============================================"
echo "  PowerTech - Delete ml_predictions"
echo "============================================"
echo ""
echo "Host:   $INFLUX_HOST"
echo "Bucket: $BUCKET"
echo "Org:    $ORG"
echo ""
echo "⚠️  This will delete ALL ml_predictions data"
echo "    from InfluxDB. Backfill will re-run on"
echo "    next container restart."
echo ""
read -p "Are you sure? (yes/no): " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "Aborted."
    exit 0
fi
echo ""
echo "Deleting ml_predictions..."
RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
    -k \
    -X POST "$INFLUX_HOST/api/v2/delete?org=$ORG&bucket=$BUCKET" \
    -H "Authorization: Token $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
        "start": "1970-01-01T00:00:00Z",
        "stop":  "2099-01-01T00:00:00Z",
        "predicate": "_measurement=\"ml_predictions\""
    }')
if [ "$RESPONSE" == "204" ]; then
    echo "✓ ml_predictions deleted successfully (HTTP 204)"
    echo ""
    echo "Next steps:"
    echo "  1. Restart the container to trigger re-backfill"
    echo "  2. docker restart <your_container_name>"
else
    echo "✗ Delete failed (HTTP $RESPONSE)"
    echo "  Check your InfluxDB connection and token"
    exit 1
fi
