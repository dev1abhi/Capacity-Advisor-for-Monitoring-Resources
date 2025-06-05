#!/bin/bash

set -e

# ---------- CONFIG ----------
JMX_PORT=7071
NODE_EXPORTER_VERSION="1.9.0"
JMX_EXPORTER_VERSION="1.0.1"
PROMETHEUS_VERSION="3.2.1"
KAFKA_VERSION="3.9.0"
SCALA_VERSION="2.13"
INSTALL_DIR="$(eval echo ~$SUDO_USER)/monitoring-setup"
JMX_DIR="$INSTALL_DIR/jmx-exporter"
PROM_DIR="$INSTALL_DIR/prometheus"
KAFKA_DIR="$INSTALL_DIR/kafka"
NODE_EXPORTER_DIR="$INSTALL_DIR/node_exporter"
# ----------------------------

mkdir -p "$INSTALL_DIR"
echo "[✓] Setup directory created at: $(realpath "$INSTALL_DIR")"

echo "[*] Updating system..."
echo "[*] Checking prerequisites..."

if java -version 2>&1 | grep -q "17"; then
  echo "[i] OpenJDK 17 already installed."
else
  echo "[*] Installing OpenJDK 17..."
  apt update -y
  apt install -y openjdk-17-jre-headless
fi

for pkg in wget curl tar unzip; do
  if ! command -v $pkg &>/dev/null; then
    echo "[*] Installing $pkg..."
    apt install -y $pkg
  else
    echo "[i] $pkg already installed."
  fi
done

# ========== NODE EXPORTER ==========
echo "[*] Downloading Node Exporter..."
cd $INSTALL_DIR
if [ -f "$INSTALL_DIR/node_exporter/node_exporter" ]; then
  echo "[i] Node Exporter is already installed at $INSTALL_DIR/node_exporter. Skipping setup."
else
  # Clean up any old downloads or incomplete folders
  rm -f node_exporter-*.tar.gz
  rm -rf node_exporter node_exporter-*

  # Download
  wget -O node_exporter.tar.gz "https://github.com/prometheus/node_exporter/releases/download/v$NODE_EXPORTER_VERSION/node_exporter-$NODE_EXPORTER_VERSION.linux-amd64.tar.gz"

  if [ $? -ne 0 ]; then
    echo "[!] Failed to download Node Exporter. Exiting."
    exit 1
  fi

  # Extract
  tar -xzf node_exporter.tar.gz

  # Rename and move
  mv "node_exporter-$NODE_EXPORTER_VERSION.linux-amd64" node_exporter

  # Cleanup
  rm node_exporter.tar.gz

  echo "[✓] Node Exporter setup complete."
fi

# ========== JMX EXPORTER ==========
echo "[*] Setting up JMX Exporter..."
mkdir -p $JMX_DIR
cd $JMX_DIR
wget https://repo1.maven.org/maven2/io/prometheus/jmx/jmx_prometheus_javaagent/$JMX_EXPORTER_VERSION/jmx_prometheus_javaagent-$JMX_EXPORTER_VERSION.jar

cat <<EOF > kafka-jmx-config.yaml
startDelaySeconds: 0
ssl: false
lowercaseOutputName: true
lowercaseOutputLabelNames: true
rules:
  - pattern: 'kafka.server<type=(.+), name=(.+)><>Count'
    name: kafka_server_\$1_\$2_total
    type: COUNTER
  - pattern: 'kafka.server<type=(.+), name=(.+)><>OneMinuteRate'
    name: kafka_server_\$1_\$2_1m_rate
    type: GAUGE
  - pattern: 'kafka.server<type=(.+), name=(.+)><>MeanRate'
    name: kafka_server_\$1_\$2_mean_rate
    type: GAUGE
  - pattern: 'kafka.server<type=(.+), name=(.+)><>Value'
    name: kafka_server_\$1_\$2
    type: GAUGE
    labels:
      kafka_server: "\$1"
  - pattern: 'kafka.log<type=Log, name=(.+), topic=(.+), partition=(.+)><>Value'
    name: kafka_log_\$1
    type: GAUGE
    labels:
      topic: "\$2"
      partition: "\$3"
EOF

# ========== PROMETHEUS ==========
echo "[*] Downloading and configuring Prometheus..."

cd "$INSTALL_DIR"

# Clean up any old/corrupt downloads
rm -f prometheus-$PROMETHEUS_VERSION.linux-amd64.tar.gz*

# Download Prometheus
wget -q --show-progress https://github.com/prometheus/prometheus/releases/download/v$PROMETHEUS_VERSION/prometheus-$PROMETHEUS_VERSION.linux-amd64.tar.gz

# Verify the archive
if ! gzip -t prometheus-$PROMETHEUS_VERSION.linux-amd64.tar.gz; then
    echo "[!] Downloaded archive is corrupted. Exiting."
    exit 1
fi

# Extract and set up
tar -xzf prometheus-$PROMETHEUS_VERSION.linux-amd64.tar.gz
mv prometheus-$PROMETHEUS_VERSION.linux-amd64 prometheus
rm prometheus-$PROMETHEUS_VERSION.linux-amd64.tar.gz

# Create configuration
cat <<EOF > $PROM_DIR/prometheus.yml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'kafka-jmx'
    static_configs:
      - targets: ['localhost:$JMX_PORT']

  - job_name: 'node'
    static_configs:
      - targets: ['localhost:9100']
EOF

# ========== KAFKA ==========
echo "[*] Downloading Apache Kafka..."
cd $INSTALL_DIR
wget https://downloads.apache.org/kafka/$KAFKA_VERSION/kafka_$SCALA_VERSION-$KAFKA_VERSION.tgz
tar -xzf kafka_$SCALA_VERSION-$KAFKA_VERSION.tgz
mv kafka_$SCALA_VERSION-$KAFKA_VERSION kafka
rm kafka_$SCALA_VERSION-$KAFKA_VERSION.tgz

echo -n "[?] Are you running this on a cloud VM (with public IP)? [y/N]: "
read IS_CLOUD

if [[ "$IS_CLOUD" =~ ^[Yy]$ ]]; then
    echo -n "[?] Enter public IP to set for advertised.listeners (e.g. 123.45.67.89): "
    read ADVERTISE_IP
else
    # Get private IP automatically
    ADVERTISE_IP=$(hostname -I | awk '{print $1}')
    echo "[i] Detected private IP: $ADVERTISE_IP"
    echo -n "[?] Do you want to use this private IP for advertised.listeners? [Y/n]: "
    read CONFIRM_PRIVATE
    if [[ "$CONFIRM_PRIVATE" =~ ^[Nn]$ ]]; then
        echo -n "[?] Enter custom IP to use: "
        read ADVERTISE_IP
    fi
fi

KAFKA_CONFIG="$KAFKA_DIR/config/server.properties"
sed -i "s|^#*advertised.listeners=.*|advertised.listeners=PLAINTEXT://${ADVERTISE_IP}:9092|" $KAFKA_CONFIG

echo "[*] Patching Kafka start script with JMX agent..."
KAFKA_START="$KAFKA_DIR/bin/kafka-server-start.sh"
sed -i '1iexport KAFKA_HEAP_OPTS="-Xmx256M -Xms128M"' $KAFKA_START
sed -i "2iexport KAFKA_OPTS=\"\$KAFKA_OPTS -javaagent:$JMX_DIR/jmx_prometheus_javaagent-$JMX_EXPORTER_VERSION.jar=$JMX_PORT:$JMX_DIR/kafka-jmx-config.yaml\"" $KAFKA_START

# ========== SERVICE FILES ==========

echo "[*] Creating systemd service files..."

# Node Exporter
cat <<EOF > /etc/systemd/system/node_exporter.service
[Unit]
Description=Node Exporter
After=network.target

[Service]
ExecStart=$NODE_EXPORTER_DIR/node_exporter
Restart=always

[Install]
WantedBy=multi-user.target
EOF

# Prometheus
cat <<EOF > /etc/systemd/system/prometheus.service
[Unit]
Description=Prometheus
After=network.target

[Service]
ExecStart=$PROM_DIR/prometheus --config.file=$PROM_DIR/prometheus.yml
Restart=always

[Install]
WantedBy=multi-user.target
EOF

# Zookeeper
cat <<EOF > /etc/systemd/system/zookeeper.service
[Unit]
Description=Apache Zookeeper
After=network.target

[Service]
Type=simple
ExecStart=$KAFKA_DIR/bin/zookeeper-server-start.sh $KAFKA_DIR/config/zookeeper.properties
Restart=on-abnormal

[Install]
WantedBy=multi-user.target
EOF

# Kafka Broker
cat <<EOF > /etc/systemd/system/kafka.service
[Unit]
Description=Apache Kafka Broker
After=zookeeper.service

[Service]
Type=simple
ExecStart=$KAFKA_DIR/bin/kafka-server-start.sh $KAFKA_DIR/config/server.properties
Restart=on-abnormal

[Install]
WantedBy=multi-user.target
EOF

# ========== FINAL ==========
echo "[*] Reloading systemd and enabling services..."
systemctl daemon-reexec
systemctl daemon-reload
systemctl enable node_exporter
systemctl enable prometheus
systemctl enable zookeeper
systemctl enable kafka

echo "[✓] Setup completed. You can now start services with:"
echo "    sudo systemctl start zookeeper"
echo "    sudo systemctl start kafka"
echo "    sudo systemctl start prometheus"
echo "    sudo systemctl start node_exporter"
