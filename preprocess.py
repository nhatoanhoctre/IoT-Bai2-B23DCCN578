import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
from sklearn.preprocessing import MinMaxScaler

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", encoding="utf-8-sig")

SENSORS = ["temperature", "humidity", "distance_cm"]
COUNT_FIELDS = {
    "n_samples", "missing_cells", "imputed_cells", "outlier_cells",
    "remaining_missing_cells",
}


def fill_short_gaps(series):
    """Fill only internal runs of at most two missing 5-second cells."""
    missing = series.isna()
    groups = missing.ne(missing.shift()).cumsum()
    gap_lengths = missing.groupby(groups).transform("sum")
    interpolated = series.interpolate(method="time", limit_area="inside")
    return series.where(~(missing & gap_lengths.le(2)), interpolated)


def process_frame(frame, stop):
    frame = frame.copy()
    frame["_time"] = pd.to_datetime(frame["_time"], utc=True)
    frame = frame.sort_values("_time").drop_duplicates("_time")
    frame = frame.set_index("_time").reindex(columns=SENSORS)
    for column in SENSORS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.replace([np.inf, -np.inf], np.nan)

    # Exclude the first partial window and all open windows.
    first_window = frame.index.min().ceil("30s")
    frame = frame.loc[(frame.index >= first_window) & (frame.index < stop)]
    if frame.empty:
        return pd.DataFrame()
    print("So ban ghi doc duoc:", len(frame))

    # IQR identifies statistical outliers, not confirmed sensor failures.
    # Recomputed over the query interval, so earlier results may change.
    filtered = frame.copy()
    outlier_flags = pd.DataFrame(False, index=frame.index, columns=SENSORS)
    for column in SENSORS:
        values = frame[column].dropna()
        if len(values) < 8:
            print(column, ": chua du mau de danh gia IQR")
            continue
        q1, q3 = values.quantile(0.25), values.quantile(0.75)
        iqr = q3 - q1
        if np.isclose(iqr, 0):
            print(column, ": IQR = 0, khong tu dong gan outlier")
            continue
        flags = frame[column].lt(q1 - 1.5 * iqr) | frame[column].gt(q3 + 1.5 * iqr)
        outlier_flags[column] = flags
        filtered.loc[flags, column] = np.nan
        print(column, "- so gia tri outlier:", int(flags.sum()))

    grid = pd.date_range(first_window, stop, freq="5s", inclusive="left")
    data_5s = filtered.resample("5s").mean().reindex(grid)
    cleaned = data_5s.copy()
    for column in SENSORS:
        cleaned[column] = fill_short_gaps(data_5s[column])
    imputed = data_5s.isna() & cleaned.notna()
    result = cleaned.resample("30s").mean()

    result["n_samples"] = frame.resample("30s").size().reindex(result.index, fill_value=0)
    # These count cells across all three sensors, not lost MQTT messages.
    result["missing_cells"] = data_5s.isna().resample("30s").sum().sum(axis=1)
    result["imputed_cells"] = imputed.resample("30s").sum().sum(axis=1)
    result["remaining_missing_cells"] = cleaned.isna().resample("30s").sum().sum(axis=1)
    result["outlier_cells"] = (
        outlier_flags.resample("30s").sum().sum(axis=1)
        .reindex(result.index, fill_value=0)
    )
    for column in SENSORS:
        result[column + "_rolling_mean"] = result[column].rolling(3, min_periods=3).mean()
        result[column + "_delta"] = result[column].diff()

    # Fixed reference scaling. Values outside the reference can be <0 or >1.
    reference = pd.DataFrame(
        [[-40.0, 0.0, 0.0], [80.0, 100.0, 450.0]], columns=SENSORS
    )
    scaler = MinMaxScaler().fit(reference)
    normalized = scaler.transform(result[SENSORS])
    for index, column in enumerate(SENSORS):
        result[column + "_norm"] = normalized[:, index]

    print("So o trong tren luoi 5s sau loc:", int(result["missing_cells"].sum()))
    print("So o da noi suy:", int(result["imputed_cells"].sum()))
    print("So o van con trong:", int(result["remaining_missing_cells"].sum()))
    return result


def build_points(result):
    points = []
    for timestamp, row in result.iterrows():
        point = Point("sensor_processed").tag("device_id", "esp32-01").time(
            timestamp.to_pydatetime()
        )
        for column, value in row.items():
            valid = bool(pd.notna(value) and np.isfinite(value))
            if column in COUNT_FIELDS:
                if not valid:
                    raise ValueError("Invalid quality counter: " + column)
                point.field(column, int(value))
            else:
                # REQUIRED QUERY CONTRACT: use numeric values only when their
                # <field>_valid flag is true. InfluxDB merges duplicate points;
                # omitting a now-invalid value does not erase its old value.
                # Always overwrite this flag, including false, on every run.
                point.field(column + "_valid", valid)
                if valid:
                    point.field(column, float(value))
        point.field("processing_schema_version", 2)
        points.append(point)
    return points


def main():
    required = [
        "INFLUXDB_URL", "INFLUXDB_TOKEN", "INFLUXDB_ORG",
        "INFLUXDB_BUCKET_RAW", "INFLUXDB_BUCKET_PROCESSED",
    ]
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise RuntimeError("Thieu cau hinh .env: " + ", ".join(missing))
    org = os.environ["INFLUXDB_ORG"]
    raw_bucket = os.environ["INFLUXDB_BUCKET_RAW"]
    processed_bucket = os.environ["INFLUXDB_BUCKET_PROCESSED"]
    stop = pd.Timestamp.now(tz="UTC").floor("30s")
    start = stop - pd.Timedelta(hours=1)
    query = f'''
    from(bucket: {json.dumps(raw_bucket)})
      |> range(start: time(v: "{start.isoformat()}"), stop: time(v: "{stop.isoformat()}"))
      |> filter(fn: (r) => r._measurement == "sensor_raw" and r.device_id == "esp32-01")
      |> filter(fn: (r) => contains(value: r._field, set: ["temperature", "humidity", "distance_cm"]))
      |> group(columns: ["device_id"])
      |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''
    with InfluxDBClient(
        url=os.environ["INFLUXDB_URL"], token=os.environ["INFLUXDB_TOKEN"],
        org=org, timeout=30000,
    ) as database:
        frame = database.query_api().query_data_frame(query)
        if isinstance(frame, list):
            frames = [item for item in frame if not item.empty]
            frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if frame.empty:
            print("Chua co du lieu. Cho collector chay roi thu lai.")
            return
        result = process_frame(frame, stop)
        if result.empty:
            print("Chua du mot cua so. Cho them 30-60 giay.")
            return
        points = build_points(result)
        with database.write_api(write_options=SYNCHRONOUS) as writer:
            writer.write(bucket=processed_bucket, org=org, record=points)
        print("\n----- KET QUA -----")
        print("So cua so 30s da luu:", len(points))
        print("Bucket:", processed_bucket)
        print("Measurement: sensor_processed | schema: 2")
        print("Dung dashboard.py moi de ap dung co *_valid.")
        print("\n5 cua so cuoi:")
        print(result[SENSORS + ["n_samples"]].tail().to_string())


if __name__ == "__main__":
    main()
