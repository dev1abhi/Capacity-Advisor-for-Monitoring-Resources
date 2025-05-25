import csv
import time
import requests
from datetime import datetime

# Your Prometheus base URL
PROMETHEUS_URL = ""

CSV_FILE = "kafka_node_metrics2.csv"

# Prometheus metric names 
PROMQL_QUERIES = {
    "messages_in": 'kafka_server_brokertopicmetrics_messagesinpersec_1m_rate',
    "bytes_in": 'kafka_server_brokertopicmetrics_bytesinpersec_1m_rate',
    "bytes_out": 'kafka_server_brokertopicmetrics_bytesoutpersec_1m_rate',
    "cpu_usage": '100 - (avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[1m])) * 100)',
    "memory_usage": '(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100',
    "disk_usage": '(1 - (node_filesystem_free_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"})) * 100'
}

def query_prometheus(promql):
    url = f"{PROMETHEUS_URL}/api/v1/query"
    try:
        res = requests.get(url, params={"query": promql})
        res.raise_for_status()
        data = res.json()
        if data["status"] == "success":
            result = data["data"]["result"]
            if result:
                return float(result[0]["value"][1])
    except Exception as e:
        print(f"[ERROR] Failed querying Prometheus for '{promql}': {e}")
    return 0.0

def get_metrics():
    metrics = {}
    for key, promql in PROMQL_QUERIES.items():
        value = query_prometheus(promql)
        metrics[key] = round(value, 2)
        print(f"[DEBUG] {key}: {value}")
    return metrics

def write_to_csv(timestamp, metrics):
    fieldnames = ["timestamp", "messages_in", "bytes_in", "bytes_out", "cpu_usage", "memory_usage", "disk_usage"]
    try:
        with open(CSV_FILE, "r"):
            file_exists = True
    except FileNotFoundError:
        file_exists = False

    with open(CSV_FILE, "a", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        row = {
            "timestamp": timestamp,
            "messages_in": metrics["messages_in"],
            "bytes_in": metrics["bytes_in"],
            "bytes_out": metrics["bytes_out"],
            "cpu_usage": metrics["cpu_usage"],
            "memory_usage": metrics["memory_usage"],
            "disk_usage": metrics["disk_usage"],
        }
        writer.writerow(row)

def main():
    while True:
        try:
            timestamp = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
            metrics = get_metrics()
            write_to_csv(timestamp, metrics)
            print(f"[{timestamp}] Metrics recorded.")
        except Exception as e:
            print(f"Error: {e}")
        time.sleep(4)

if __name__ == "__main__":
    main()
