"""MQTT collector with a durable local queue and timing measurements."""
import json
import math
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", encoding="utf-8-sig")
ORG = os.environ["INFLUXDB_ORG"]
BUCKET = os.environ["INFLUXDB_BUCKET_RAW"]
TOPIC = "ptit/B23DCCN578/iot2/esp32-01/telemetry"
PROBE_TOPIC = "ptit/B23DCCN578/iot2/esp32-01/probe"
RUN_ID = uuid.uuid4().hex
ALLOW_NTP_ESTIMATES = False  # Wokwi virtual clock is not a reliable wall clock.
FLOAT_FIELDS = ("temperature", "humidity", "distance_cm")
INT_FIELDS = ("rssi", "sequence", "uptime_s", "ts_ms", "ntp_sync_age_ms")
stop_event = threading.Event()
ready_event = threading.Event()
probe_lock = threading.Lock()
pending_probes = {}
queue_lock = threading.Lock()
queue_db = sqlite3.connect(BASE_DIR / "collector_queue.sqlite3", check_same_thread=False, timeout=10)
queue_db.row_factory = sqlite3.Row
queue_db.execute("PRAGMA journal_mode=WAL")
queue_db.execute("""CREATE TABLE IF NOT EXISTS events (
    device_id TEXT NOT NULL, boot_id TEXT NOT NULL, seq INTEGER NOT NULL,
    payload TEXT NOT NULL, received_ns INTEGER NOT NULL,
    raw_done INTEGER NOT NULL DEFAULT 0, metrics_json TEXT,
    done INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(device_id, boot_id, seq)
)""")
# Upgrade the existing queue without dropping pending or completed events.
existing_columns = {row[1] for row in queue_db.execute("PRAGMA table_info(events)")}
for name, sql_type in (("probe_started_ns", "INTEGER"), ("received_perf_ns", "INTEGER"), ("collector_run_id", "TEXT")):
    if name not in existing_columns:
        queue_db.execute("ALTER TABLE events ADD COLUMN " + name + " " + sql_type)
queue_db.commit()


def validate(data):
    if not isinstance(data, dict):
        raise ValueError("JSON phai la object")
    if data.get("device_id") != "esp32-01":
        raise ValueError("Sai device_id")
    boot = data.get("boot_id")
    if not isinstance(boot, str) or not 1 <= len(boot) <= 64:
        raise ValueError("Thieu/sai boot_id")
    for field in FLOAT_FIELDS + INT_FIELDS:
        value = data.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("Thieu/sai truong so: " + field)
    for field in INT_FIELDS:
        if not isinstance(data[field], int) or not -(2**63) <= data[field] < 2**63:
            raise ValueError("Truong phai la int64: " + field)
    if data["sequence"] < 1 or data["uptime_s"] < 0 or data["ntp_sync_age_ms"] < 0:
        raise ValueError("Sequence/uptime/NTP age khong hop le")
    if not 1700000000000 <= data["ts_ms"] < 4102444800000:
        raise ValueError("Timestamp Unix millisecond khong hop le")
    trace = data.get("trace_id", "")
    if not isinstance(trace, str) or (trace and (len(trace) != 32 or any(c not in "0123456789abcdef" for c in trace))):
        raise ValueError("Sai trace_id")


def timing(data, received_ns):
    # This is an estimate between two clocks, NOT pure network latency.
    estimated_ms = received_ns / 1_000_000 - data["ts_ms"]
    # Screening only; this does not certify clock accuracy.
    acceptable = ALLOW_NTP_ESTIMATES and 0 <= estimated_ms <= 60000 and data["ntp_sync_age_ms"] <= 10000
    return estimated_ms, acceptable


def enqueue(data, received_ns, received_perf_ns, probe_started_ns):
    with queue_lock:
        cursor = queue_db.execute(
            "INSERT OR IGNORE INTO events(device_id,boot_id,seq,payload,received_ns,received_perf_ns,probe_started_ns,collector_run_id) VALUES(?,?,?,?,?,?,?,?)",
            (data["device_id"], data["boot_id"], data["sequence"], json.dumps(data), received_ns, received_perf_ns, probe_started_ns, RUN_ID),
        )
        queue_db.commit()
        return cursor.rowcount == 1


def on_connect(client, userdata, flags, reason_code, properties):
    ready_event.clear()
    if reason_code.is_failure:
        print("MQTT FAILED:", reason_code, flush=True)
        return
    print("Da ket noi MQTT!", flush=True)
    client.subscribe(TOPIC, qos=0)


def on_subscribe(client, userdata, mid, reason_codes, properties):
    if any(code.is_failure for code in reason_codes):
        print("Dang ky topic that bai:", reason_codes, flush=True)
    else:
        print("Dang nhan vao hang dem, sau do luu InfluxDB:", BUCKET, flush=True)
        ready_event.set()


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
    ready_event.clear()


def on_message(client, userdata, message):
    received_perf_ns = time.perf_counter_ns()
    received_ns = time.time_ns()
    try:
        data = json.loads(message.payload.decode("utf-8"))
        validate(data)
        with probe_lock:
            probe_started_ns = pending_probes.get(data.get("trace_id", ""))
        if not enqueue(data, received_ns, received_perf_ns, probe_started_ns):
            print("BO QUA BAN TRUNG | boot={} | seq={}".format(data["boot_id"], data["sequence"]), flush=True)
        elif probe_started_ns is not None:
            with probe_lock:
                pending_probes.pop(data["trace_id"], None)
    except (UnicodeDecodeError, ValueError, OverflowError) as error:
        print("BO QUA BAN TIN:", error, flush=True)
    except sqlite3.Error as error:
        print("LOI HANG DEM CUC BO:", error, flush=True)


def probe_worker():
    while not stop_event.is_set():
        if not ready_event.wait(1):
            continue
        if stop_event.is_set():
            break
        trace_id = uuid.uuid4().hex
        started_ns = time.perf_counter_ns()
        with probe_lock:
            expired = [key for key, value in pending_probes.items() if started_ns - value > 120_000_000_000]
            for key in expired:
                pending_probes.pop(key)
                print("YEU CAU DO QUA HAN 120 GIAY:", key[:8], flush=True)
            pending_probes[trace_id] = started_ns
        try:
            info = client.publish(PROBE_TOPIC, trace_id, qos=0, retain=False)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                raise RuntimeError("MQTT publish rc=" + str(info.rc))
            print("DA GUI YEU CAU DO | trace=" + trace_id[:8], flush=True)
        except Exception as error:
            with probe_lock:
                pending_probes.pop(trace_id, None)
            print("CHUA GUI DUOC YEU CAU DO:", error, flush=True)
        stop_event.wait(30)


def writer_worker():
    with InfluxDBClient(url=os.environ["INFLUXDB_URL"], token=os.environ["INFLUXDB_TOKEN"], org=ORG, timeout=5000) as database:
        with database.write_api(write_options=SYNCHRONOUS) as writer:
            while not stop_event.is_set():
                with queue_lock:
                    row = queue_db.execute("SELECT * FROM events WHERE done=0 ORDER BY received_ns LIMIT 1").fetchone()
                if row is None:
                    stop_event.wait(0.3)
                    continue
                try:
                    data = json.loads(row["payload"])
                    key = (row["device_id"], row["boot_id"], row["seq"])
                    estimated_ms, acceptable = timing(data, row["received_ns"])

                    if not row["raw_done"]:
                        point = (Point("sensor_raw")
                                 .tag("device_id", data["device_id"])
                                 .time(row["received_ns"], WritePrecision.NS)
                                 .field("boot_id", data["boot_id"])
                                 .field("received_at_ms", row["received_ns"] // 1_000_000)
                                 .field("clock_estimate_usable", acceptable))
                        for field in FLOAT_FIELDS:
                            point.field(field, float(data[field]))
                        for field in INT_FIELDS:
                            point.field(field, data[field])
                        if acceptable:
                            point.field("latency_ms_est", float(estimated_ms))

                        started = time.perf_counter()
                        writer.write(bucket=BUCKET, org=ORG, record=point)
                        stored_perf_ns = time.perf_counter_ns()
                        write_ms = (time.perf_counter() - started) * 1000
                        metrics = {
                            "sequence": data["sequence"],
                            "boot_id": data["boot_id"],
                            "receive_latency_ms_est_unfiltered": float(estimated_ms),
                            "clock_estimate_usable": acceptable,
                            "ntp_sync_age_ms": data["ntp_sync_age_ms"],
                            "db_write_ms": float(write_ms),
                        }
                        same_run = row["collector_run_id"] == RUN_ID
                        if same_run and row["received_perf_ns"] is not None:
                            metrics["receive_to_db_ack_ms"] = (stored_perf_ns - row["received_perf_ns"]) / 1_000_000
                        if same_run and row["probe_started_ns"] is not None:
                            metrics["trace_id"] = data["trace_id"]
                            metrics["measurement_kind"] = "request_sample_store_round_trip"
                            metrics["request_to_receive_ms"] = (row["received_perf_ns"] - row["probe_started_ns"]) / 1_000_000
                            metrics["request_to_db_ack_ms"] = (stored_perf_ns - row["probe_started_ns"]) / 1_000_000
                        if acceptable:
                            metrics["latency_ms_est"] = float(estimated_ms)
                        with queue_lock:
                            queue_db.execute("UPDATE events SET raw_done=1,metrics_json=? WHERE device_id=? AND boot_id=? AND seq=?", (json.dumps(metrics),) + key)
                            queue_db.commit()
                        stamp = datetime.fromtimestamp(row["received_ns"] / 1e9, timezone.utc).astimezone().strftime("%H:%M:%S")
                        cycle = metrics.get("request_to_db_ack_ms")
                        cycle_label = " | yeu cau->DB={:.1f} ms".format(cycle) if cycle is not None else ""
                        print("[{}] DA LUU | seq={} | T={} | H={} | D={} | ghi DB={:.1f} ms{}".format(stamp, data["sequence"], data["temperature"], data["humidity"], data["distance_cm"], write_ms, cycle_label), flush=True)
                    else:
                        metrics = json.loads(row["metrics_json"])

                    metric_point = Point("pipeline_metrics").tag("device_id", data["device_id"]).time(row["received_ns"], WritePrecision.NS)
                    for field, value in metrics.items():
                        metric_point.field(field, value)
                    writer.write(bucket=BUCKET, org=ORG, record=metric_point)
                    with queue_lock:
                        queue_db.execute("UPDATE events SET done=1 WHERE device_id=? AND boot_id=? AND seq=?", key)
                        queue_db.commit()
                except Exception as error:
                    print("LOI GHI DATABASE; giu ban tin trong hang dem va thu lai:", error, flush=True)
                    stop_event.wait(3)


client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.on_subscribe = on_subscribe
client.on_message = on_message
client.on_disconnect = on_disconnect
client.reconnect_delay_set(min_delay=1, max_delay=30)
worker = threading.Thread(target=writer_worker, daemon=True)
worker.start()
probe_thread = threading.Thread(target=probe_worker, daemon=True)
probe_thread.start()

try:
    print("Collector: do chu trinh yeu cau->lay mau->luu DB, moi 30 giay | Ctrl+C de dung", flush=True)
    client.connect("broker.hivemq.com", 1883, keepalive=60)
    client.loop_forever()
except KeyboardInterrupt:
    print("\nDang dung; cac ban tin chua ghi van duoc giu tren dia.", flush=True)
finally:
    client.disconnect()
    stop_event.set()
    ready_event.set()
    probe_thread.join()
    worker.join()
    queue_db.close()
