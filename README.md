# Capacity-Advisor-for-Monitoring-Resources

## Architecture Diagram
![image](https://github.com/user-attachments/assets/9cb9bec2-1895-4686-939a-43d7cf334cdb)

# Week 1: Setting Up Apache Kafka and Prometheus on AWS EC2 (Linux)

## Installed and Configured:
- **Apache Kafka**: A distributed event streaming platform designed for high-throughput, fault-tolerant real-time data processing.
- **Prometheus**: An open-source monitoring and alerting toolkit optimized for time-series data and system performance metrics.

---

## Steps Followed:

### **Apache Kafka Setup**

#### **Pre-requisites**  
1. **Launch an EC2 instance** and allow **port 22** for SSH.
2. **Launch PuTTY and connect to AWS EC2**:
   - Install **PuTTY** (if not installed).
   - Create the EC2 instance with a key pair (`.pem` file).
   - Convert `.pem` to `.ppk` (Using **PuTTYgen**).
   - Connect to AWS EC2 using PuTTY:
     - Open **PuTTY**.
     - In **Host Name**, enter:  
       ```
       ec2-user@your-aws-public-ip
       ```
     - Navigate to **Connection > SSH > Auth**, load the `.ppk` file under  
       *"Private key file for authentication"*.
     - Click **Open**.
     - Click **"Yes"** if you get a security alert.
3. **Update Your System**
   - sudo yum update -y.
4. **Install Java(Required for Kafka)**:
   - sudo amazon-linux-extras enable corretto8.
   - sudo yum install java-11-amazon-corretto -y.

#### **Downloading and Installing Kafka**  
1. **Navigate to the /opt directory**:
   - cd /opt
2. **Download Kafka**:
   - wget https://downloads.apache.org/kafka/3.6.1/kafka_2.13-3.6.1.tgz
3. **Extract Kafka**:
   - sudo tar -xvzf kafka_2.13-3.6.1.tgz
   - sudo mv kafka_2.13-3.6.1 kafka
4. **Change ownership and permissions**:
   - sudo chown -R ec2-user:ec2-user /opt/kafka

#### **Start Zookeeper (Required for Kafka)**  
1. **Start Zookeeper**:
   - cd /opt/kafka
   - bin/zookeeper-server-start.sh config/zookeeper.properties
2. **Run Zookeeper in the background**:
   - nohup bin/zookeeper-server-start.sh config/zookeeper.properties > zookeeper.log

#### **Start Kafka Broker**
1. **Start Kafka**:
   - bin/kafka-server-start.sh config/server.properties

#### **Testing Kafka**
1. **Create a topic**:
   - bin/kafka-topics.sh --create --topic test-topic --bootstrap-server localhost:9092
   - --partitions 1 --replication-factor 1
2. **Start a Kafka Producer to send messages**:
   - bin/kafka-console-producer.sh --topic test-topic --bootstrap-server localhost:9092
3. **Start a kafka Consumer to read messages**:
   - bin/kafka-console-consumer.sh --topic test-topic --from-beginning --bootstrap-server
   - localhost:9092

![WhatsApp Image 2025-03-18 at 23 48 43_79933298](https://github.com/user-attachments/assets/6413e0d1-26cc-4f89-85de-a9cb4ea90077)
---

### **Prometheus**

1. Launch an **EC2 Instance** and allow **port 22** for **SSH**, **port 9090** for **Prometheus**, and **port 9100** for **Node Exporter**.
2. **Update system packages**:
   - sudo apt update && sudo apt upgrade -y.
3. **Download Prometheus**:
   - wget https://github.com/prometheus/prometheus/releases/download/v3.2.1/prometheus-3.2.1.linux-amd64.tar.gz
4. **Extract and move files**:
   - tar -xvzf prometheus-3.2.1.linux-amd64.tar.gz.
   - sudo mv prometheus-3.2.1.linux-amd64 /etc/prometheus.
5. **Create Prometheus user and set permissions**:
   - sudo useradd --no-create-home --shell /bin/false prometheus
   - sudo chown -R prometheus:prometheus /etc/prometheus
6. **Configure prometheus.yml**:
   - sudo nano /etc/prometheus/prometheus.yml
7. **Create a systemd service for Prometheus**:
   - sudo nano /etc/systemd/system/prometheus.service
8. **Reload systemd and start Prometheus**:
   - sudo systemctl daemon-reload
   - sudo systemctl start prometheus
   - sudo systemctl enable prometheus
9. **Check if Prometheus is running**:
   - sudo systemctl status prometheus

![Screenshot 2025-03-13 012612](https://github.com/user-attachments/assets/89d2055c-3e80-49e0-8f4a-274e01014679)

![Screenshot 2025-03-13 012538](https://github.com/user-attachments/assets/fdb0e846-2c4b-445e-9de3-39e5ce9a284d)

---

## Problem Faced

### **Problem in creating kafka topic**:

![Screenshot 2025-03-17 231042](https://github.com/user-attachments/assets/40cf636c-d399-4cb5-b05d-ae0919581d59)

#### **Trouble shoot**:
- Kafka topic creation issue was encountered because of insufficient memory. Therefore, we fixed it by adding swap space. Since Kafka needs more memory, we created a 2GB swap file:
  - **Create a 2GB swap file**:
       - sudo fallocate -l 2G /swapfile
  - **Set the correct permissions**:
       - sudo chmod 600 /swapfile
  - **Set up the swap space**:
       - sudo mkswap /swapfile
  - **Enable the swap**:
       - sudo swapon /swapfile
#### **Output achieved after Troubleshoot**:

![Screenshot 2025-03-17 224012](https://github.com/user-attachments/assets/87402548-6fec-4f4e-a4c1-54afcc185ddd)

#### **Note**:
   - **Change the timeout settings of the Zoopkeeper**:
       - change Zoopkeeper timeout settings from 0 to any arbitrary higher number(seconds) to avoid server timeout settings.
   - **Avoid using 9092 port**:
       - it might be blocked sometimes.
