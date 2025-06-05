#!/bin/bash

set -e

SERVICES=(
  zookeeper
  kafka
  prometheus
  node_exporter
)

echo "[*] Starting all services..."

for service in "${SERVICES[@]}"; do
  echo "→ Starting $service..."
  sudo systemctl start "$service"
done

echo "[✓] All services started successfully!"
echo
echo "Status summary:"
systemctl status "${SERVICES[@]}" --no-pager --lines=2
