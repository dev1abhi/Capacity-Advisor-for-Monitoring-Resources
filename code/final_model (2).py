

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
import requests
import joblib
from sklearn.metrics import classification_report, roc_auc_score, average_precision_score
from lightgbm import LGBMClassifier
from prophet import Prophet
from apscheduler.schedulers.background import BackgroundScheduler
import time
import os
import matplotlib.pyplot as plt
import smtplib
from email.mime.text import MIMEText
warnings.filterwarnings("ignore")

import yaml

# Load configuration
def load_config(file_path='config.yaml'):
    with open(file_path, 'r') as f:
        return yaml.safe_load(f)

CONFIG = load_config()
PROMETHEUS_URL = CONFIG['prometheus_url']
CRASH_THRESHOLD = CONFIG['thresholds']['crash_threshold']
FORECAST_HORIZON = CONFIG['thresholds']['forecast_horizon']
MIN_TRAIN_SAMPLES = CONFIG['thresholds']['min_train_samples']
MODEL_RETRAIN_INTERVAL = CONFIG['thresholds']['model_retrain_interval']
ALERT_INTERVAL = CONFIG['thresholds']['alert_interval']

NORMAL_RANGES = CONFIG['normal_ranges']
SEVERITY_LEVELS = CONFIG['severity_levels']

HISTORICAL_DATA = CONFIG['file_paths']['historical_data']
LABELED_DATA = CONFIG['file_paths']['labeled_data']
MODEL_FILE = CONFIG['file_paths']['model_file']
FORECASTER_FILE = CONFIG['file_paths']['forecaster_file']

KAFKA_METRICS = CONFIG['metrics']['kafka']
NODE_METRICS = CONFIG['metrics']['node']


LLM_API_KEY = env.API_KEY  # Replace with your Perplexity API key
LLM_API_URL = "https://api.perplexity.ai/chat/completions"  # Perplexity API endpoint
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
EMAIL_ADDRESS = CONFIG['email']  # Replace with your Gmail
EMAIL_PASSWORD = env.PASSWORD

def label_data(df):
    """Label crash events based on message rate AND resource exhaustion"""
    df['timestamp'] = pd.to_datetime(df['timestamp'], dayfirst=True)
    df = df.sort_values('timestamp').reset_index(drop=True)
    df = df.ffill().dropna(subset=['messages_in', 'cpu_usage', 'memory_usage'])

    # Calculate normalized message rate
    msg_min = df['messages_in'].quantile(0.05)
    msg_max = df['messages_in'].quantile(0.95)
    df['normalized_msg_rate'] = (df['messages_in'] - msg_min) / (msg_max - msg_min)

    # Initialize crash column
    df['crash'] = 0

    # Label crashes where normalized message rate drops below threshold
    message_crash = (df['normalized_msg_rate'] < CRASH_THRESHOLD)
    df.loc[message_crash, 'crash'] = 1

    # Label resource exhaustion as crashes
    resource_crash = (
        (df['cpu_usage'] > NORMAL_RANGES['cpu_usage'][1]) |
        (df['memory_usage'] > NORMAL_RANGES['memory_usage'][1]) |
        (df['disk_usage'] > NORMAL_RANGES['disk_usage'][1])
    )
    df.loc[resource_crash, 'crash'] = 1

    # Combine crash indices
    crash_indices = df[df['crash'] == 1].index

    # Label pre-crash periods (5 minutes before actual crash)
    for idx in crash_indices:
        pre_crash_start = max(0, idx - 60)  # 5 minutes before at 5s intervals
        df.loc[pre_crash_start:idx, 'crash'] = 1

    return df

try:
    df = pd.read_csv(HISTORICAL_DATA)
    labeled_df = label_data(df)
    labeled_df.to_csv(LABELED_DATA, index=False)
    print(f"Data labeled and saved to {LABELED_DATA}")
    print(f"Crash events detected: {labeled_df['crash'].sum()}")
except Exception as e:
    print(f"Error labeling data: {e}")

class KafkaCrashClassifier:
    def __init__(self):
        self.model = None
        self.expected_features = [
            'messages_in', 'cpu_usage', 'memory_usage', 'disk_usage',
            'cpu_exceeded', 'mem_exceeded', 'disk_exceeded', 'multiple_exceeded'
        ]
        self.load_model()

    def create_features(self, df):
        """Create engineered features for training/prediction"""
        # Create exceedance features
        df['cpu_exceeded'] = (df['cpu_usage'] > NORMAL_RANGES['cpu_usage'][1]).astype(int)
        df['mem_exceeded'] = (df['memory_usage'] > NORMAL_RANGES['memory_usage'][1]).astype(int)
        df['disk_exceeded'] = (df['disk_usage'] > NORMAL_RANGES['disk_usage'][1]).astype(int)
        df['multiple_exceeded'] = df[['cpu_exceeded', 'mem_exceeded', 'disk_exceeded']].sum(axis=1)
        return df

    def train_model(self):
        """Train the classifier with enhanced features"""
        try:
            df = pd.read_csv(LABELED_DATA)

            # Only require base features + crash for training
            base_features = ['messages_in', 'cpu_usage', 'memory_usage', 'disk_usage', 'crash']
            df = df.dropna(subset=base_features)

            if len(df) < MIN_TRAIN_SAMPLES:
                raise ValueError(f"Not enough samples ({len(df)}) for training")

            # Create new features
            df = self.create_features(df)

            # Ensure we have all required features
            missing_features = [f for f in self.expected_features if f not in df.columns]
            if missing_features:
                raise ValueError(f"Missing features in training data: {missing_features}")

            features = df[self.expected_features]
            y = df['crash'].astype(int)

            from sklearn.preprocessing import StandardScaler
            from sklearn.pipeline import make_pipeline

            scaler = StandardScaler()
            self.model = make_pipeline(
                scaler,
                LGBMClassifier(
                    objective='binary',
                    n_estimators=300,
                    learning_rate=0.05,
                    scale_pos_weight=(1 - y.mean()) / y.mean(),
                    random_state=42,
                    verbose=-1,
                    class_weight='balanced'
                )
            ).fit(features, y)

            # Evaluate model
            y_pred = self.model.predict(features)
            y_prob = self.model.predict_proba(features)[:, 1]

            print("\nModel Evaluation Report:")
            print(classification_report(y, y_pred))
            print(f"AUC: {roc_auc_score(y, y_prob):.4f}")
            print(f"AP: {average_precision_score(y, y_prob):.4f}")

            joblib.dump(self.model, MODEL_FILE)
            print("Model trained and saved successfully")
            return True

        except Exception as e:
            print(f"Error training model: {e}")
            raise

    def load_model(self):
        """Load saved model or train new one"""
        try:
            if os.path.exists(MODEL_FILE):
                self.model = joblib.load(MODEL_FILE)
                print("Loaded existing crash prediction model")
            else:
                print("No saved model found - training new one")
                self.train_model()
        except Exception as e:
            print(f"Error loading model: {e}")
            print("Attempting to train new model...")
            self.train_model()

class MetricForecaster:
    def __init__(self):
        self.models = {}
        self.load_models()

    def train_forecasters(self, df):
        """Train Prophet models for each metric"""
        df = df.copy()
        df['timestamp'] = pd.to_datetime(df['timestamp'], dayfirst=True)

        for metric in ['messages_in', 'cpu_usage', 'memory_usage', 'disk_usage']:
            try:
                print(f"Training Prophet model for {metric}...")
                data = df[['timestamp', metric]].dropna()
                data.columns = ['ds', 'y']

                model = Prophet(
                    daily_seasonality=True,
                    changepoint_prior_scale=0.05
                )
                model.fit(data)
                self.models[metric] = model

            except Exception as e:
                print(f"Error training Prophet for {metric}: {e}")
                raise

        joblib.dump(self.models, FORECASTER_FILE)

    def forecast(self):
        """Generate forecasts for all metrics"""
        if not self.models:
            print("No forecast models available")
            return None

        forecasts = {}
        future = self.models['messages_in'].make_future_dataframe(
            periods=FORECAST_HORIZON,
            freq='5S',
            include_history=False
        )

        for metric, model in self.models.items():
            try:
                forecast = model.predict(future)
                forecasts[f'{metric}_forecast'] = forecast['yhat'].values
                forecasts[f'{metric}_trend'] = np.polyfit(
                    np.arange(len(forecast['yhat'])),
                    forecast['yhat'],
                    1
                )[0]
            except Exception as e:
                print(f"Error forecasting {metric}: {e}")
                forecasts[f'{metric}_forecast'] = None
                forecasts[f'{metric}_trend'] = None

        return pd.DataFrame(forecasts)

    def load_models(self):
        """Load saved forecasters or train new ones"""
        try:
            if os.path.exists(FORECASTER_FILE):
                self.models = joblib.load(FORECASTER_FILE)
                print("Loaded existing forecast models")
            else:
                print("No saved forecast models found - training new ones")
                df = pd.read_csv(LABELED_DATA)
                self.train_forecasters(df)
        except Exception as e:
            print(f"Error loading forecast models: {e}")
            df = pd.read_csv(LABELED_DATA)
            self.train_forecasters(df)

def get_llm_advice(resource, value):
    """Get mitigation advice from Perplexity API"""
    if not isinstance(value, (int, float)) or value < 0:
        return "Invalid metric value - cannot generate advice"
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json"
    }
    if resource == 'Message Rate':
        prompt = f"Kafka message rate dropped to {value:.0f} msg/sec. Suggest immediate mitigation steps. Keep the advice simple, clear and concise without using air quotes or bold text. It is running on a linux system using zookeeper."
    else:
        prompt = f"High {resource} usage at {value:.1f}% detected. Suggest immediate mitigation steps.  Keep the advice simple, clear and concise without using air quotes or bold text. It is running on a linux system using zookeeper."

    payload = {
        "model": "sonar-pro",  # Using perplexity online model
        "messages": [
            {
                "role": "system",
                "content": "You are a helpful DevOps assistant providing concise technical advice."
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
    }

    try:
        response = requests.post(LLM_API_URL, headers=headers, json=payload)
        response.raise_for_status()
        advice = response.json()["choices"][0]["message"]["content"]
        return advice.strip()
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error: {e.response.status_code} - {e.response.text}")
    except Exception as e:
        print(f"Other error: {str(e)}")

def send_email(subject, body, to_email="abhilashsarangi222@gmail.com"):
    """Send email notification using SMTP"""
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = to_email

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.send_message(msg)
        print(f"Email notification sent to {to_email}")
    except Exception as e:
        print(f"Error sending email: {e}")

class KafkaMonitor:
    def __init__(self):
        self.classifier = KafkaCrashClassifier()
        self.forecaster = MetricForecaster()
        self.last_alert_time = None
        self.crash_prob_history = []
        self.metrics_history = []
        self.history_window = 360
        self.alert_cooldown = {}
    def _get_threshold_value(self, resource):
        """Get threshold value for alert message formatting"""
        thresholds = {
            'CPU': NORMAL_RANGES['cpu_usage'][1],
            'Memory': NORMAL_RANGES['memory_usage'][1],
            'Disk': NORMAL_RANGES['disk_usage'][1],
            'Message Rate': NORMAL_RANGES['messages_in'][0]
        }
        return thresholds.get(resource, '')
    def query_prometheus(self):
        """Query current metrics from Prometheus"""
        def fetch_metric(query):
            try:
                response = requests.get(PROMETHEUS_URL+'/api/v1/query', params={'query': query})
                response.raise_for_status()
                result = response.json()
                if result['status'] == 'success' and result['data']['result']:
                    return float(result['data']['result'][0]['value'][1])
                else:
                    return None
            except Exception as e:
                print(f"Error querying Prometheus for {query}: {e}")
                return None

        metrics = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'messages_in': fetch_metric(KAFKA_METRICS['messages_in']),
            'cpu_usage': fetch_metric(NODE_METRICS['cpu_usage']),
            'memory_usage': fetch_metric(NODE_METRICS['memory_usage']),
            'disk_usage': fetch_metric(NODE_METRICS['disk_usage'])
        }

        # Store metrics for historical analysis
        self.metrics_history.append(metrics)
        if len(self.metrics_history) > self.history_window:
            self.metrics_history.pop(0)

        return metrics

    def calculate_crash_probability(self, current_metrics):
        """Predict crash probability with rule-based boosting"""
        if not self.classifier.model:
            print("No classifier model available")
            return 0.0

        try:
            # Create feature dictionary
            features = {
                'messages_in': current_metrics.get('messages_in', 0),
                'cpu_usage': current_metrics.get('cpu_usage', 0),
                'memory_usage': current_metrics.get('memory_usage', 0),
                'disk_usage': current_metrics.get('disk_usage', 0)
            }

            # Add engineered features
            features['cpu_exceeded'] = int(features['cpu_usage'] > NORMAL_RANGES['cpu_usage'][1])
            features['mem_exceeded'] = int(features['memory_usage'] > NORMAL_RANGES['memory_usage'][1])
            features['disk_exceeded'] = int(features['disk_usage'] > NORMAL_RANGES['disk_usage'][1])
            features['multiple_exceeded'] = features['cpu_exceeded'] + features['mem_exceeded'] + features['disk_exceeded']

            # Predict base probability
            features_df = pd.DataFrame([features])[self.classifier.expected_features]
            base_prob = self.classifier.model.predict_proba(features_df)[0, 1]

            # Apply rule-based probability boost
            boost = 0.0
            metrics_exceeded = 0

            # CPU boost (scales with severity)
            if features['cpu_exceeded']:
                cpu_excess = max(0, current_metrics['cpu_usage'] - NORMAL_RANGES['cpu_usage'][1])
                boost += min(0.4, cpu_excess * 0.015)  # 1.5% per percentage point over 85
                metrics_exceeded += 1

            # Memory boost
            if features['mem_exceeded']:
                mem_excess = max(0, current_metrics['memory_usage'] - NORMAL_RANGES['memory_usage'][1])
                boost += min(0.4, mem_excess * 0.02)  # 2% per percentage point over 90
                metrics_exceeded += 1

            # Disk boost
            if features['disk_exceeded']:
                disk_excess = max(0, current_metrics['disk_usage'] - NORMAL_RANGES['disk_usage'][1])
                boost += min(0.3, disk_excess * 0.015)  # 1.5% per percentage point over 90
                metrics_exceeded += 1

            # Message rate boost
            msg_min, msg_max = NORMAL_RANGES['messages_in']
            if current_metrics['messages_in'] < msg_min:
                msg_deficit = (msg_min - current_metrics['messages_in']) / msg_min
                boost += min(0.5, msg_deficit * 0.8)  # Significant boost for low message rate

            # Multi-metric penalty
            if metrics_exceeded > 1:
                boost += 0.1 * metrics_exceeded  # +10% per additional exceeded metric

            # Apply boost (cap at 99%)
            boosted_prob = min(0.99, base_prob + boost)

            # Smooth probability with moving average
            self.crash_prob_history.append(boosted_prob)
            if len(self.crash_prob_history) > 12:
                smoothed_prob = np.mean(self.crash_prob_history[-12:])
            else:
                smoothed_prob = boosted_prob

            return smoothed_prob

        except Exception as e:
            print(f"Prediction error: {e}")
            return 0.0

    def send_alert(self, message, severity='medium'):
        """Send alert with severity level and cooldown"""
        # Cooldown check to prevent alert spam
        if severity in self.alert_cooldown:
            if (datetime.now() - self.alert_cooldown[severity]).seconds < 300:
                return

        # Choose appropriate alert header
        if severity == 'critical':
            alert_header = "🔴 CRITICAL ALERT: Kafka Crash Imminent"
        elif severity == 'high':
            alert_header = "🟠 HIGH ALERT: Kafka Crash Likely"
        else:
            alert_header = "🟡 WARNING: Potential Kafka Issues"

        # Compose message
        formatted_msg = (
            f"\n{alert_header}\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"{message}\n"
        )

        # ✅ 1. Print alert to console
        print(formatted_msg)

        # ✅ 2. Send alert via external API if enabled
        if CONFIG.get('alert_api', {}).get('enabled', False):
            api_payload = {
                'severity': severity,
                'timestamp': datetime.now().isoformat(),
                'message': f"{alert_header}\n{message}"
            }

            try:
                response = requests.post(
                    CONFIG['alert_api']['url'],
                    headers=CONFIG['alert_api'].get('headers', {}),
                    json=api_payload,
                    timeout=5
                )
                if response.status_code != 200:
                    print(f"[API Alert] ❗ Alert API responded with status {response.status_code}: {response.text}")
                else:
                    print(f"[API Alert] ✅ Alert sent successfully to {CONFIG['alert_api']['url']}")
            except Exception as e:
                print(f"[API Alert] ❌ Failed to send alert: {e}")

        # Track cooldown
        self.last_alert_time = datetime.now()
        self.alert_cooldown[severity] = datetime.now()

    def handle_alert(self, severity, resource, value, prob, current_metrics):
        """Enhanced alert handling with LLM advice and email notifications"""
        if severity == 'info':
            return

        alert_msg = (
            f"Threshold Exceeded:\n{resource}: {value}{'%' if resource != 'Message Rate' else ''} "
            f"{'>' if resource != 'Message Rate' else '<'} {self._get_threshold_value(resource)}\n\n"
            f"Crash probability: {prob:.1%}\n"
            f"Current metrics:\n"
            f"- Messages/s: {current_metrics['messages_in']:.0f}\n"
            f"- CPU: {current_metrics['cpu_usage']:.1f}%\n"
            f"- Memory: {current_metrics['memory_usage']:.1f}%\n"
            f"- Disk: {current_metrics['disk_usage']:.1f}%\n\n"
        )

        # Get LLM advice if this is a new alert
        if resource and value:
            try:
                advice = get_llm_advice(resource, value)
                alert_msg += f"Recommended Actions:\n{advice}\n\n"

                # Send email notification for high severity alerts
                if severity in ['high', 'critical']:
                    subject = f"🚨 Warning: High {resource} Usage Detected"
                    # In handle_alert():
                    email_body = (
                        f"Alert Details:\n"
                        f"- Resource: {resource}\n"
                        f"- Usage: {value}{'%' if resource != 'Message Rate' else ' msg/sec'}\n"
                        f"- Severity: {severity.upper()}\n\n"
                        f"Current Metrics:\n"
                        f"- Messages/s: {current_metrics.get('messages_in', 0):.0f}\n"
                        f"- CPU: {current_metrics.get('cpu_usage', 0):.1f}%\n"
                        f"- Memory: {current_metrics.get('memory_usage', 0):.1f}%\n"
                        f"- Disk: {current_metrics.get('disk_usage', 0):.1f}%\n\n"
                        f"Recommended Actions:\n{advice}"
                    )
                    send_email(subject, email_body)

            except Exception as e:
                print(f"Alert enhancement error: {e}")
                alert_msg += "Could not generate mitigation advice.\n"

        # Original alert sending logic
        self.send_alert(alert_msg, severity)
    def check_system(self):
        """Main monitoring function"""
        try:
            # 1. Get current metrics
            current_metrics = self.query_prometheus()
            print(f"\nCurrent Metrics: {current_metrics}")

            # 2. Calculate crash probability
            prob = self.calculate_crash_probability(current_metrics)
            print(f"Crash Probability: {prob:.1%}")

            # 3. Check individual metric thresholds
            threshold_warnings = []
            if current_metrics['cpu_usage'] > NORMAL_RANGES['cpu_usage'][1]:
                threshold_warnings.append(f"CPU Usage: {current_metrics['cpu_usage']:.1f}% > {NORMAL_RANGES['cpu_usage'][1]}%")
            if current_metrics['memory_usage'] > NORMAL_RANGES['memory_usage'][1]:
                threshold_warnings.append(f"Memory Usage: {current_metrics['memory_usage']:.1f}% > {NORMAL_RANGES['memory_usage'][1]}%")
            if current_metrics['disk_usage'] > NORMAL_RANGES['disk_usage'][1]:
                threshold_warnings.append(f"Disk Usage: {current_metrics['disk_usage']:.1f}% > {NORMAL_RANGES['disk_usage'][1]}%")
            if current_metrics['messages_in'] < NORMAL_RANGES['messages_in'][0]:
                threshold_warnings.append(
                    f"Messages In: {current_metrics['messages_in']:.0f} < {NORMAL_RANGES['messages_in'][0]}"
                )


            # 4. Determine severity
            severity = next(
                (level for level, thresh in SEVERITY_LEVELS.items() if prob >= thresh),
                'info'
            )

            # 5. Send alerts if needed
            alert_msg = ""
            if threshold_warnings:
                alert_msg += "Threshold Exceeded:\n" + "\n".join(threshold_warnings) + "\n\n"
                # Extract the most critical resource for LLM advice
                resource, value = self._get_primary_alert_resource(current_metrics)
            else:
                resource, value = None, None

            if severity != 'info' or threshold_warnings:
                alert_msg += (
                    f"Crash probability: {prob:.1%}\n"
                    f"Current metrics:\n"
                    f"- Messages/s: {current_metrics['messages_in']:.0f}\n"
                    f"- CPU: {current_metrics['cpu_usage']:.1f}%\n"
                    f"- Memory: {current_metrics['memory_usage']:.1f}%\n"
                    f"- Disk: {current_metrics['disk_usage']:.1f}%"
                )
                self.handle_alert(severity, resource, value, prob,  current_metrics)

            # Periodic status update
            if self.last_alert_time is None or (datetime.now() - self.last_alert_time).seconds >= ALERT_INTERVAL:
                status = "NORMAL" if prob < 0.3 and not threshold_warnings else "WARNING"
                print(f"\n📊 Status: {status} | Crash risk: {prob:.1%}")

            return prob, current_metrics

        except Exception as e:
            print(f"Monitoring error: {e}")
            return 0.0, None

    def _get_primary_alert_resource(self, metrics):
        """Determine which resource triggered the alert with fallbacks"""
        # Check CPU first
        if metrics.get('cpu_usage', 0) > NORMAL_RANGES['cpu_usage'][1]:
            return 'CPU', metrics['cpu_usage']
        # Then memory
        elif metrics.get('memory_usage', 0) > NORMAL_RANGES['memory_usage'][1]:
            return 'Memory', metrics['memory_usage']
        # Then disk
        elif metrics.get('disk_usage', 0) > NORMAL_RANGES['disk_usage'][1]:
            return 'Disk', metrics['disk_usage']
        # Finally message rate (use get with default)
        elif metrics.get('messages_in', float('inf')) < NORMAL_RANGES['messages_in'][0]:
            return 'Message Rate', metrics['messages_in']
        return 'System', 0  # Fallback value

if __name__ == "__main__":
    print("Initializing Fixed Kafka Crash Prediction System...")
    monitor = KafkaMonitor()

    # Set up scheduler for periodic retraining
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        monitor.classifier.train_model,
        'interval',
        seconds=MODEL_RETRAIN_INTERVAL,
        next_run_time=datetime.now() + timedelta(seconds=10)
    )
    scheduler.start()

    # Main monitoring loop
    try:
        print("Starting Kafka crash monitoring... Press Ctrl+C to stop")
        print(f"Alert thresholds: CPU > {NORMAL_RANGES['cpu_usage'][1]}% | "
              f"Memory > {NORMAL_RANGES['memory_usage'][1]}% | "
              f"Disk > {NORMAL_RANGES['disk_usage'][1]}% | "
              f"Messages < {NORMAL_RANGES['messages_in'][0]}")

        while True:
            prob, metrics = monitor.check_system()
            time.sleep(5)  # Check every 5 seconds

    except KeyboardInterrupt:
        scheduler.shutdown()
        print("Monitoring stopped")
    except Exception as e:
        scheduler.shutdown()
        print(f"Fatal error: {e}")