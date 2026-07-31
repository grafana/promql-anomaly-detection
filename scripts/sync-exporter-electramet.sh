#!/usr/bin/env bash
# Sync exporter/ to ElectraMet data_bridge mount and restart container.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
GRAFT_SSH_KEY="${GRAFT_SSH_KEY:-${HOME}/.ssh/tig-key-pair.pem}"
GRAFT_EC2_HOST="${GRAFT_EC2_HOST:-ec2-user@35.175.68.13}"
REMOTE_DIR="${GRAFT_REMOTE_PROMETHEUS:-~/ptw_data/Cloud/Docker/prometheus}"
# Runtime enroll writes live on EC2 — do not clobber unless SYNC_PEER_RF_CONFIG=1.
SYNC_PEER_RF_CONFIG="${SYNC_PEER_RF_CONFIG:-0}"

echo "==> Syncing exporter to ${GRAFT_EC2_HOST}:${REMOTE_DIR}"
RSYNC_FILES=(
  "${REPO_ROOT}/exporter/bridge.py"
  "${REPO_ROOT}/exporter/bridge_peer_rf.py"
  "${REPO_ROOT}/exporter/bridge_peer_rf_api.py"
  "${REPO_ROOT}/exporter/delete_ml_predictions.sh"
  "${REPO_ROOT}/exporter/delete_peer_rf_predictions.sh"
)
if [[ "${SYNC_PEER_RF_CONFIG}" == "1" ]]; then
  echo "  (including peer_rf_config.json — SYNC_PEER_RF_CONFIG=1)"
  RSYNC_FILES+=("${REPO_ROOT}/exporter/peer_rf_config.json")
else
  echo "  (skipping peer_rf_config.json — set SYNC_PEER_RF_CONFIG=1 to overwrite live enrollments)"
fi

rsync -avz -e "ssh -i ${GRAFT_SSH_KEY}" \
  "${RSYNC_FILES[@]}" \
  "${GRAFT_EC2_HOST}:${REMOTE_DIR}/"

echo "==> Restarting data_bridge (if present)"
ssh -i "${GRAFT_SSH_KEY}" "${GRAFT_EC2_HOST}" \
  'docker restart data_bridge 2>/dev/null || docker restart data-bridge 2>/dev/null || echo "Restart data_bridge manually"'

echo "==> Done. Peer-RF backfill runs on container start when ENABLE_PEER_RF_BACKFILL=1"
echo "    Control API: set PEER_RF_CONTROL_TOKEN + publish port 8001 on data_bridge"
