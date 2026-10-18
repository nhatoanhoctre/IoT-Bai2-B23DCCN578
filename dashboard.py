import json
import os
import traceback
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", encoding="utf-8-sig")

SENSORS = {
    "Nhi\u1ec7t \u0111\u1ed9": ("temperature", "\xb0C"),
    "\u0110\u1ed9 \u1ea9m": ("humidity", "%"),
    "Kho\u1ea3ng c\xe1ch": ("distance_cm", "cm"),
}

st.set_page_config(page_title="Gi\xe1m s\xe1t IoT - B23DCCN578", page_icon="\U0001f4ca", layout="wide")
st.title("Gi\xe1m s\xe1t d\u1eef li\u1ec7u IoT")
st.caption("B\xe0i th\u1ef1c h\xe0nh 2 \u2022 B23DCCN578 \u2022 L\xea C\xf4ng Nam")

required = ["INFLUXDB_URL", "INFLUXDB_TOKEN", "INFLUXDB_ORG", "INFLUXDB_BUCKET_RAW", "INFLUXDB_BUCKET_PROCESSED"]
missing = [key for key in required if not os.environ.get(key)]
if missing:
    st.error("Thi\u1ebfu c\u1ea5u h\xecnh trong file .env: " + ", ".join(missing))
    st.stop()

ORG = os.environ["INFLUXDB_ORG"]
RAW_BUCKET = os.environ["INFLUXDB_BUCKET_RAW"]
PROCESSED_BUCKET = os.environ["INFLUXDB_BUCKET_PROCESSED"]

with st.sidebar:
    st.header("\u0110i\u1ec1u khi\u1ec3n")
    minutes = st.selectbox("Kho\u1ea3ng th\u1eddi gian", [15, 30, 60], index=2, format_func=lambda value: f"{value} ph\xfat")
    sensor_label = st.selectbox("\u0110\u1ea1i l\u01b0\u1ee3ng tr\xean bi\u1ec3u \u0111\u1ed3", list(SENSORS))
    auto_refresh = st.checkbox("T\u1ef1 c\u1eadp nh\u1eadt m\u1ed7i 5 gi\xe2y", value=True)
    st.button("L\xe0m m\u1edbi ngay")
    st.caption("Thi\u1ebft b\u1ecb: esp32-01")
    st.caption("Gi\u1eef collector v\xe0 InfluxDB \u0111ang ch\u1ea1y.")


def apply_validity_flags(frame):
    # InfluxDB merges fields when rewriting a point. A false flag also
    # invalidates an older numeric field that is absent from the new write.
    frame = frame.copy()
    validity_columns = [column for column in frame if column.endswith("_valid")]
    for flag in validity_columns:
        column = flag[:-6]
        if column not in frame:
            frame[column] = float("nan")
        # Rows from the older schema have no flag; keep their original value.
        invalid = frame[flag].notna() & ~frame[flag].eq(True)
        frame.loc[invalid, column] = float("nan")
    return frame.drop(columns=validity_columns)


def read_data(database, bucket, measurement, start, stop):
    query = f'''
    from(bucket: {json.dumps(bucket)})
      |> range(start: time(v: "{start.isoformat()}"), stop: time(v: "{stop.isoformat()}"))
      |> filter(fn: (r) => r._measurement == {json.dumps(measurement)} and r.device_id == "esp32-01")
      |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''
    frame = database.query_api().query_data_frame(query)
    if isinstance(frame, list):
        frames = [item for item in frame if not item.empty]
        frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if frame.empty:
        return pd.DataFrame()
    frame["_time"] = pd.to_datetime(frame["_time"], utc=True)
    frame = frame.drop(columns=["result", "table", "_start", "_stop", "_measurement", "device_id"], errors="ignore")
    frame = frame.groupby("_time").first().sort_index()
    for column in frame.columns:
        if column not in {"boot_id", "trace_id", "measurement_kind"}:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if measurement == "sensor_processed":
        frame = apply_validity_flags(frame)
    frame.index = frame.index.tz_convert("Asia/Ho_Chi_Minh")
    frame.index.name = "Th\u1eddi gian"
    return frame


def total(frame, column):
    return int(frame[column].fillna(0).sum()) if not frame.empty and column in frame else 0


@st.fragment(run_every=5 if auto_refresh else None)
def render_dashboard():
    now = pd.Timestamp.now(tz="UTC")
    start = now - pd.Timedelta(minutes=minutes)
    try:
        with InfluxDBClient(url=os.environ["INFLUXDB_URL"], token=os.environ["INFLUXDB_TOKEN"], org=ORG, timeout=15000) as database:
            raw = read_data(database, RAW_BUCKET, "sensor_raw", start, now)
            processed = read_data(database, PROCESSED_BUCKET, "sensor_processed", start, now)
            metrics = read_data(database, RAW_BUCKET, "pipeline_metrics", start, now)
    except Exception:
        st.error("Kh\xf4ng \u0111\u1ecdc \u0111\u01b0\u1ee3c database. Ki\u1ec3m tra c\u1eeda s\u1ed5 InfluxDB v\xe0 c\u1ea5u h\xecnh trong file .env.")
        st.caption("Chi ti\u1ebft l\u1ed7i \u0111\u01b0\u1ee3c in trong PowerShell ch\u1ea1y dashboard.")
        traceback.print_exc()
        return

    if raw.empty:
        st.warning("Ch\u01b0a c\xf3 d\u1eef li\u1ec7u th\xf4 trong kho\u1ea3ng \u0111\xe3 ch\u1ecdn. Ki\u1ec3m tra Wokwi v\xe0 collector.")
        return

    last_received = raw.index.max()
    age = max(0, int((now - last_received.tz_convert("UTC")).total_seconds()))
    st.caption("D\u1eef li\u1ec7u th\xf4 m\u1edbi nh\u1ea5t: " + last_received.strftime("%d/%m/%Y %H:%M:%S") + f" \u2022 C\xe1ch \u0111\xe2y {age} gi\xe2y \u2022 Gi\u1edd Vi\u1ec7t Nam")
    if age > 30:
        st.warning(f"\u0110\xe3 {age} gi\xe2y ch\u01b0a c\xf3 d\u1eef li\u1ec7u m\u1edbi. Ki\u1ec3m tra m\xf4 ph\u1ecfng v\xe0 collector.")

    containers = st.columns(3)
    for container, (label, (field, unit)) in zip(containers, SENSORS.items()):
        values = raw[field].dropna() if field in raw else pd.Series(dtype=float)
        text = f"{values.iloc[-1]:.2f} {unit}" if not values.empty else "Ch\u01b0a c\xf3"
        container.metric(label, text)

    field, unit = SENSORS[sensor_label]
    figure = go.Figure()
    if field in raw:
        figure.add_trace(go.Scatter(x=raw.index, y=raw[field], mode="markers", name="M\u1eabu th\xf4", marker=dict(size=5, color="#3b82f6")))

    if not processed.empty:
        if field in processed:
            figure.add_trace(go.Scatter(x=processed.index, y=processed[field], mode="lines+markers", name="\u0110\xe3 x\u1eed l\xfd \xb7 c\u1eeda s\u1ed5 30 gi\xe2y", connectgaps=False, line=dict(color="#f59e0b", width=3)))
        rolling_field = field + "_rolling_mean"
        if rolling_field in processed:
            figure.add_trace(go.Scatter(x=processed.index, y=processed[rolling_field], mode="lines", name="Trung b\xecnh tr\u01b0\u1ee3t 3 c\u1eeda s\u1ed5", connectgaps=False, line=dict(color="#22c55e", width=2, dash="dash")))
        st.caption("M\u1ed1c \u0111\u1ea7u c\u1eeda s\u1ed5 \u0111\xe3 x\u1eed l\xfd m\u1edbi nh\u1ea5t: " + processed.index.max().strftime("%d/%m/%Y %H:%M:%S"))
        processed_age = (now - processed.index.max().tz_convert("UTC")).total_seconds()
        if processed_age > 120:
            st.info("K\u1ebft qu\u1ea3 ti\u1ec1n x\u1eed l\xfd ch\u01b0a c\u1eadp nh\u1eadt g\u1ea7n \u0111\xe2y. Ch\u1ea1y l\u1ea1i preprocess.py \u0111\u1ec3 x\u1eed l\xfd d\u1eef li\u1ec7u m\u1edbi.")
    else:
        st.info("Ch\u01b0a c\xf3 d\u1eef li\u1ec7u \u0111\xe3 x\u1eed l\xfd trong kho\u1ea3ng \u0111ang xem. Ch\u1ea1y preprocess.py ho\u1eb7c ch\u1ecdn kho\u1ea3ng th\u1eddi gian r\u1ed9ng h\u01a1n.")

    values = raw[field].dropna() if field in raw else pd.Series(dtype=float)
    if not processed.empty and field in processed:
        values = pd.concat([values, processed[field].dropna()])
    if not values.empty:
        minimum_padding = {"temperature": 1.0, "humidity": 2.0, "distance_cm": 2.0}[field]
        padding = max(float(values.max() - values.min()) * 0.1, minimum_padding)
        figure.update_yaxes(range=[float(values.min()) - padding, float(values.max()) + padding])

    figure.update_layout(title=f"So s\xe1nh d\u1eef li\u1ec7u \xb7 {sensor_label}", xaxis_title="Th\u1eddi gian Vi\u1ec7t Nam", yaxis_title=unit, height=440, margin=dict(l=20, r=20, t=80, b=30), legend=dict(orientation="h", y=1.12), hovermode="x unified")
    st.plotly_chart(figure, use_container_width=True)
    st.caption("M\u1eabu th\xf4 \u0111\u01b0\u1ee3c v\u1ebd b\u1eb1ng c\xe1c \u0111i\u1ec3m. D\u1eef li\u1ec7u x\u1eed l\xfd d\xf9ng m\u1ed1c \u0111\u1ea7u c\u1eeda s\u1ed5 30 gi\xe2y; kho\u1ea3ng thi\u1ebfu \u0111\u01b0\u1ee3c gi\u1eef tr\u1ed1ng.")

    st.subheader("Th\u1ed1ng k\xea trong kho\u1ea3ng \u0111ang xem")
    columns = st.columns(4)
    columns[0].metric("B\u1ea3n ghi th\xf4", len(raw))
    columns[1].metric("C\u1eeda s\u1ed5 \u0111\xe3 x\u1eed l\xfd", len(processed))
    columns[2].metric("Gi\xe1 tr\u1ecb b\u1ecb IQR \u0111\xe1nh d\u1ea5u", total(processed, "outlier_cells"))
    columns[3].metric("\xd4 \u0111\xe3 n\u1ed9i suy", total(processed, "imputed_cells"))
    st.caption("IQR \u0111\xe1nh d\u1ea5u kh\xe1c bi\u1ec7t th\u1ed1ng k\xea, ch\u01b0a kh\u1eb3ng \u0111\u1ecbnh c\u1ea3m bi\u1ebfn l\u1ed7i. S\u1ed1 \xf4 tr\u1ed1ng tr\xean l\u01b0\u1edbi th\u1eddi gian kh\xf4ng ph\u1ea3i s\u1ed1 b\u1ea3n tin MQTT b\u1ecb m\u1ea5t.")

    st.subheader("Th\u1eddi gian x\u1eed l\xfd v\xe0 chu tr\xecnh l\u1ea5y m\u1eabu")
    st.caption("Chu tr\xecnh: Python g\u1eedi y\xeau c\u1ea7u \u2192 ESP32 l\u1ea5y m\u1eabu \u2192 Python nh\u1eadn d\u1eef li\u1ec7u \u2192 InfluxDB x\xe1c nh\u1eadn ghi. D\xf9ng \u0111\u1ed3ng h\u1ed3 \u0111\u01a1n \u0111i\u1ec7u tr\xean c\xf9ng m\xe1y t\xednh, g\u1ed3m c\u1ea3 chi\u1ec1u y\xeau c\u1ea7u v\xe0 chi\u1ec1u tr\u1ea3 d\u1eef li\u1ec7u.")
    if "request_to_db_ack_ms" in metrics:
        cycle_values = pd.to_numeric(metrics["request_to_db_ack_ms"], errors="coerce")
        timing_rows = metrics.loc[cycle_values.notna() & cycle_values.ge(0)].copy()
    else:
        timing_rows = pd.DataFrame()
    timing_columns = st.columns(4)
    sample_count = len(timing_rows)
    timing_columns[0].metric("S\u1ed1 chu tr\xecnh \u0111o th\xe0nh c\xf4ng", sample_count)
    if sample_count:
        cycle_values = timing_rows["request_to_db_ack_ms"]
        timing_columns[1].metric("Chu tr\xecnh trung v\u1ecb", f"{cycle_values.median():.1f} ms")
        timing_columns[2].metric("Chu tr\xecnh P95", f"{cycle_values.quantile(0.95):.1f} ms" if sample_count >= 10 else "Ch\u1edd th\xeam m\u1eabu")
    else:
        timing_columns[1].metric("Chu tr\xecnh trung v\u1ecb", "Ch\u01b0a c\xf3")
        timing_columns[2].metric("Chu tr\xecnh P95", "Ch\u01b0a c\xf3")
    db_values = pd.to_numeric(metrics["db_write_ms"], errors="coerce").dropna() if "db_write_ms" in metrics else pd.Series(dtype=float)
    timing_columns[3].metric("Ghi DB trung v\u1ecb", f"{db_values.median():.1f} ms" if not db_values.empty else "Ch\u01b0a c\xf3")
    st.caption("Th\u1eddi gian ghi DB \u0111o ri\xeang l\u1eddi g\u1ecdi ghi d\u1eef li\u1ec7u th\xf4. C\xe1c th\u1ed1ng k\xea ch\u1ec9 d\xf9ng b\u1ea3n ghi trong kho\u1ea3ng th\u1eddi gian \u0111ang ch\u1ecdn. M\u1ed7i y\xeau c\u1ea7u l\u1ea5y m\u1eabu c\xe1ch nhau kho\u1ea3ng 30 gi\xe2y.")
    if sample_count < 10:
        st.info(f"Hi\u1ec7n c\xf3 {sample_count} m\u1eabu chu tr\xecnh. Ti\u1ebfp t\u1ee5c ch\u1ea1y \u0111\u1ebfn \xedt nh\u1ea5t 10 m\u1eabu \u0111\u1ec3 b\u1eaft \u0111\u1ea7u xem ph\xe2n b\u1ed1; c\u1ea7n th\xeam m\u1eabu \u0111\u1ec3 \u0111\xe1nh gi\xe1 \u1ed5n \u0111\u1ecbnh.")
    if sample_count:
        with st.expander("B\u1ea3ng s\u1ed1 li\u1ec7u th\u1eddi gian v\xe0 t\u1ea3i CSV"):
            export_columns = [column for column in ["trace_id", "boot_id", "sequence", "request_to_receive_ms", "receive_to_db_ack_ms", "request_to_db_ack_ms", "db_write_ms"] if column in timing_rows]
            export_frame = timing_rows[export_columns]
            st.dataframe(export_frame.tail(30), use_container_width=True)
            st.download_button("T\u1ea3i s\u1ed1 li\u1ec7u chu tr\xecnh CSV", data=export_frame.to_csv(index=True).encode("utf-8-sig"), file_name="iot_timing.csv", mime="text/csv", key="timing_csv")

    with st.expander("Xem d\u1eef li\u1ec7u th\xf4"):
        st.dataframe(raw.tail(50), use_container_width=True)
    with st.expander("Xem d\u1eef li\u1ec7u \u0111\xe3 x\u1eed l\xfd v\xe0 c\xe1c \u0111\u1eb7c tr\u01b0ng"):
        st.dataframe(processed.tail(50), use_container_width=True)
    with st.expander("Xem d\u1eef li\u1ec7u chu\u1ea9n h\xf3a"):
        norm_columns = [column for column in processed.columns if column.endswith("_norm")]
        if norm_columns:
            st.dataframe(processed[norm_columns].tail(50), use_container_width=True)
        else:
            st.write("Ch\u01b0a c\xf3 d\u1eef li\u1ec7u chu\u1ea9n h\xf3a.")


render_dashboard()
