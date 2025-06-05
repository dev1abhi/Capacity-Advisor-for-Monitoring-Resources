#!/bin/bash

echo "[*] Stopping monitoring services..."

services=(zookeeper kafka prometheus node_exporter)

for service in "${services[@]}"; do
    echo "→ Stopping $service..."
    sudo systemctl stop "$service"
    echo "✓ $service stopped."
done

echo "[✓] All services have been stopped."
