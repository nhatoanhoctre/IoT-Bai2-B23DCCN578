# Bài thực hành 2 — Thu thập, lưu trữ và tiền xử lý dữ liệu IoT

**Sinh viên:** Lê Công Nam — **Mã sinh viên:** B23DCCN578.

ESP32 mô phỏng đọc DHT22 và HC-SR04, gửi JSON qua MQTT. Python kiểm tra bản tin, giữ hàng đợi SQLite, ghi InfluxDB và xử lý dữ liệu để hiển thị trên Streamlit.

**Dự án Wokwi:** https://wokwi.com/projects/475644736791391233

## Kiến trúc

- ESP32 gửi dữ liệu qua broker `broker.hivemq.com:1883`.
- `collector.py` nhận MQTT, kiểm tra JSON và chống trùng bằng khóa `(device_id, boot_id, sequence)` trong SQLite.
- Luồng ghi lấy từng bản tin trong hàng đợi, ghi `sensor_raw`, sau đó ghi `pipeline_metrics` vào bucket `iot_raw`.
- `preprocess.py` đọc dữ liệu thô, xử lý và ghi `sensor_processed` vào bucket `iot_processed`.
- `dashboard.py` truy vấn hai bucket để hiển thị dữ liệu và thống kê thời gian.
- Mỗi khoảng 30 giây, collector gửi một mã yêu cầu đo. ESP32 lấy mẫu rồi gửi trả cùng mã để đo chu trình bằng đồng hồ đơn điệu trên máy tính.

## Thành phần

| Đường dẫn | Nội dung |
|---|---|
| `firmware/esp32-blink.ino` | Firmware đã lưu trong dự án Wokwi |
| `firmware/diagram.json` | Sơ đồ mạch lấy từ cùng dự án |
| `firmware/libraries.txt` | Thư viện Arduino của Wokwi |
| `collector.py` | Nhận MQTT, hàng đợi, chống trùng và ghi database |
| `preprocess.py` | Tiền xử lý, schema phiên bản 2 |
| `dashboard.py` | Ứng dụng Streamlit, đọc cờ hợp lệ của phiên bản 2 |
| `.env.example` | Mẫu cấu hình, không chứa token thật |
| `requirements.txt` | Các thư viện trực tiếp, ghim phiên bản theo môi trường đã chạy |
| `docs/timing.csv` | 20 chu trình thành công đã xuất từ dashboard |
| `docs/dashboard.png` | Ảnh dashboard do sinh viên cung cấp ngày 18/10/2026 |
| `docs/ket_qua.md` | Kết quả đo và giới hạn diễn giải |

## Môi trường

- Windows, Python **3.11 64-bit**. Môi trường đã chạy dùng Python 3.11.9 và `.venv64`.
- InfluxDB **2.x** chạy cục bộ; môi trường thực hành dùng bản native Windows 2.10.0.
- Wokwi trên trình duyệt. Không cần cắm ESP32 cho bản mô phỏng này.
- Có Internet để Wokwi, MQTT broker và NTP hoạt động.
- Docker không phải thành phần bắt buộc của cách triển khai này.

## Chuẩn bị Python

Giải nén dự án, mở PowerShell ở thư mục chứa README. Nếu đã có môi trường làm bài đang hoạt động thì tiếp tục dùng môi trường đó.

```powershell
py -3.11-64 -m venv .venv64
.\.venv64\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Điền token thật vào `.env`. Token phải có quyền đọc hai bucket, ghi hai bucket và đọc dữ liệu cần thiết. Giữ `.env` tại cùng thư mục với ba chương trình Python. Không đẩy token hoặc `.env` lên GitHub.

## Chuẩn bị InfluxDB

Chạy `influxd.exe` bản 2.x trong một cửa sổ PowerShell riêng. Với vị trí tải xuống đã dùng khi thực hành:

```powershell
& "$env:USERPROFILE\Downloads\influxd.exe" --http-bind-address 127.0.0.1:8086
```

Nếu lưu chương trình ở vị trí khác, sửa đường dẫn tương ứng. Mở http://127.0.0.1:8086 và hoàn tất thiết lập ban đầu:

- Organization: `PTIT`.
- Bucket `iot_raw`: retention **7 ngày**.
- Bucket `iot_processed`: retention **30 ngày**.
- Tạo API token đủ quyền rồi điền vào `.env`.

Việc tạo bucket và retention thực hiện qua giao diện InfluxDB; các script Python không tự tạo chúng. Giữ cửa sổ `influxd` chạy trong lúc demo.

## Chạy hệ thống

Mở các cửa sổ PowerShell riêng, cùng đứng ở thư mục dự án. Chỉ chạy **một collector** để tránh gửi nhiều luồng yêu cầu đo.

**Collector:**

```powershell
.\.venv64\Scripts\python.exe collector.py
```

**Wokwi:** mở link ở đầu README và bấm Run. Firmware, sơ đồ và danh sách thư viện tương ứng nằm trong `firmware/`. DHT22 ở GPIO15, HC-SR04 TRIG ở GPIO5 và ECHO ở GPIO18, LED ở GPIO2. Chờ kết nối Wi-Fi, NTP và MQTT; Serial Monitor báo `Probe listener ready!` khi đăng ký topic yêu cầu đo.

**Tiền xử lý, lặp mỗi 30 giây:**

```powershell
while ($true) {
    .\.venv64\Scripts\python.exe preprocess.py
    Start-Sleep -Seconds 30
}
```

**Dashboard:**

```powershell
.\.venv64\Scripts\python.exe -m streamlit run dashboard.py --server.address 127.0.0.1
```

Mở http://127.0.0.1:8501. Nếu Streamlit hỏi email ở lần chạy đầu, có thể để trống rồi nhấn Enter. Dashboard làm mới khoảng 5 giây. Trong mục **Thời gian xử lý và chu trình lấy mẫu**, mở **Bảng số liệu thời gian và tải CSV** để tải kết quả.

Nhấn Ctrl+C tại từng cửa sổ để dừng. Hàng đợi giữ trên đĩa để xử lý tiếp ở lần chạy sau.

## MQTT và cấu trúc bản tin

| Mục | Giá trị |
|---|---|
| Broker | `broker.hivemq.com:1883` |
| Telemetry | `ptit/B23DCCN578/iot2/esp32-01/telemetry` |
| Yêu cầu đo | `ptit/B23DCCN578/iot2/esp32-01/probe` |
| QoS | 0, không retained |
| Chu kỳ định kỳ | 5.000 ms theo đồng hồ ESP32 mô phỏng |
| Khoảng gửi yêu cầu đo | Khoảng 30 giây theo đồng hồ máy tính |

```json
{
  "device_id": "esp32-01",
  "boot_id": "2ccd2c551a7aad05",
  "trace_id": "1427447b882f4bbe8f198549b55f3557",
  "ts_ms": 1792292634864,
  "ntp_sync_age_ms": 5000,
  "temperature": 33.1,
  "humidity": 62.5,
  "distance_cm": 276.41,
  "rssi": -67,
  "sequence": 17,
  "uptime_s": 80
}
```

JSON trên chỉ minh họa cấu trúc, không phải bản ghi đo dùng để tính thống kê. `trace_id` rỗng ở bản tin định kỳ. `boot_id` đổi khi ESP32 khởi động lại, `sequence` bắt đầu lại từ 1. Collector kiểm tra kiểu dữ liệu, số hữu hạn, trường bắt buộc, thiết bị và định dạng mã đo. Nếu một cảm biến không hợp lệ, firmware hiện bỏ qua cả mẫu.

## Schema và thời gian

| Measurement | Bucket | Tag | Timestamp | Trường chính |
|---|---|---|---|---|
| `sensor_raw` | `iot_raw` | `device_id` | Máy tính nhận MQTT, UTC ns | Ba cảm biến, `boot_id`, `sequence`, `rssi`, `uptime_s`, `ts_ms`, tuổi NTP |
| `pipeline_metrics` | `iot_raw` | `device_id` | Cùng mốc với bản ghi thô | `db_write_ms`, thời gian chờ/chu trình khi đo được, `trace_id` |
| `sensor_processed` | `iot_processed` | `device_id` | Đầu cửa sổ 30 giây, UTC | Giá trị xử lý, rolling mean, delta, chuẩn hóa, số mẫu, các bộ đếm ô và cờ `*_valid` |

Database dùng thời điểm nhận của máy tính cho dữ liệu thô. `ts_ms` từ ESP32 được giữ làm field để chẩn đoán. Dashboard đổi cách hiển thị sang giờ Việt Nam. Thời gian từ cảm biến tới máy tính không được coi là chính xác chỉ vì cả hai từng đồng bộ NTP.

## Tiền xử lý

1. Đọc tối đa một giờ gần nhất, kết thúc tại mốc 30 giây đã đóng; bỏ cửa sổ đầu không đầy đủ.
2. Ép kiểu số, đổi vô cực và giá trị không đọc được thành NaN.
3. Tính IQR riêng cho ba đại lượng trên dữ liệu trong khoảng truy vấn. Cần ít nhất 8 giá trị. Nếu IQR xấp xỉ 0 thì không tự gắn outlier; trường hợp khác dùng ngưỡng Q1 − 1,5×IQR và Q3 + 1,5×IQR.
4. Tạo lưới 5 giây, lấy trung bình nếu có nhiều mẫu trong ô. Chỉ nội suy theo thời gian cho khoảng thiếu nội bộ dài tối đa **2 ô**. Giữ nguyên khoảng dài hơn và khoảng thiếu ở hai đầu.
5. Lấy trung bình cửa sổ 30 giây. Một cửa sổ có thể có giá trị từ số mẫu ít; đây không phải bảo đảm đủ 6 ô quan sát thật.
6. Tạo rolling mean 3 cửa sổ với đủ 3 giá trị, delta giữa các cửa sổ kề nhau.
7. Min-Max theo khoảng tham chiếu cố định: nhiệt độ −40..80°C, độ ẩm 0..100%, khoảng cách 0..450 cm. Đây là khoảng dùng cho bài mô phỏng; không clip nên giá trị ngoài khoảng có thể cho kết quả ngoài 0..1.
8. Ghi sang `sensor_processed`; không thay đổi `sensor_raw`.

`n_samples` là số bản ghi thô trong cửa sổ. `missing_cells` đếm ô thiếu sau lọc, `imputed_cells` đếm ô đã nội suy, `remaining_missing_cells` đếm ô còn thiếu; các bộ đếm ô cộng trên cả ba cảm biến. Chúng không phải số gói MQTT bị mất.

**Quy tắc bắt buộc của schema 2:** chỉ dùng một giá trị xử lý khi `<tên_field>_valid` là `true`. Mỗi lần chạy lại luôn ghi cờ, kể cả `false`. InfluxDB hợp nhất field khi ghi trùng measurement/tag/timestamp, nên việc chỉ bỏ qua NaN không xóa giá trị cũ. Dashboard đi kèm áp dụng các cờ trước khi vẽ và hiển thị bảng. Khi truy vấn trực tiếp bằng Data Explorer, phải đọc cả cờ hợp lệ. Hai file `preprocess.py` và `dashboard.py` phải được cập nhật cùng nhau.

## Hàng đợi và chống trùng

`collector_queue.sqlite3` lưu bản tin hợp lệ trước khi ghi InfluxDB. `raw_done=1` đánh dấu dữ liệu thô đã được ghi; `done=1` sau khi cả dữ liệu thô và metrics được ghi. Khi database lỗi, collector giữ bản tin và thử lại khoảng 3 giây. Retry dùng lại timestamp của lần nhận đầu để tránh tạo điểm mới.

SQLite giữ cả bản tin đã hoàn tất để chống phát lại. Bản hiện tại chưa tự dọn các bản ghi này nên dung lượng hàng đợi sẽ tăng khi chạy dài ngày. Không xóa file hàng đợi trong lúc chạy; xóa sẽ mất cả bản tin đang chờ và lịch sử chống trùng.

QoS 0 không bảo đảm nhận được bản tin khi broker, subscriber hoặc mạng mất kết nối. Firmware chưa có bộ đệm bền vững. Phục hồi khi InfluxDB dừng chỉ bảo vệ các bản tin collector đã nhận và đưa vào SQLite, không chứng minh không mất dữ liệu toàn hệ thống.

## Định nghĩa thời gian đo

- `db_write_ms`: thời gian lời gọi ghi đồng bộ dữ liệu thô, gồm cả giao tiếp HTTP tới InfluxDB cục bộ.
- `receive_to_db_ack_ms`: từ MQTT callback nhận bản tin đến xác nhận ghi dữ liệu thô; gồm thời gian đợi trong hàng đợi và thử lại.
- `request_to_receive_ms`: từ Python bắt đầu gửi yêu cầu tới lúc nhận mẫu khớp mã.
- `request_to_db_ack_ms`: từ bắt đầu gửi yêu cầu tới xác nhận ghi dữ liệu thô. Gồm chiều yêu cầu, chờ lấy mẫu, chiều trả dữ liệu, hàng đợi và ghi DB. Đây là chu trình yêu cầu–lấy mẫu–lưu trữ, không phải độ trễ mạng một chiều hoặc phép đo chính xác sensor→DB một chiều.

Ba khoảng sau dùng `perf_counter_ns()` trong cùng tiến trình collector. Không ghép đồng hồ đơn điệu giữa hai lần khởi động collector. Phép đo kết thúc khi dữ liệu thô được xác nhận, chưa bao gồm ghi measurement metrics, tiền xử lý hoặc tải trang dashboard.

Wokwi có thể chạy chậm hơn thời gian thực, đồng hồ mô phỏng có thể lệch dần so với máy tính. Vì vậy `ALLOW_NTP_ESTIMATES=False`; số chênh thời gian hai máy chỉ lưu để chẩn đoán. Giữ mô phỏng chạy khi demo. Yêu cầu không có phản hồi khớp trong hơn 120 giây được dọn và báo quá hạn ở chu kỳ kiểm tra sau; CSV dashboard chỉ chứa các chu trình hoàn tất.

## Kiểm tra đã thực hiện

- Đã quan sát bản tin cảm biến vào InfluxDB và hiển thị raw/processed trên dashboard.
- Dừng rồi bật lại InfluxDB: có thông báo kết nối bị từ chối, sau đó ghi lại thành công; truy vấn SQLite cho thấy `done=0` và `raw_done=0` đều có số lượng 0 tại thời điểm kiểm tra.
- Phát lại một bản tin đã nhận: collector báo `BO QUA BAN TRUNG` với cùng `boot_id` và `seq`.
- Kiểm tra logic bằng dữ liệu thử: chỉ nội suy khoảng thiếu 1–2 ô, giữ khoảng dài và biên; giữ cửa sổ trống; áp dụng cờ hợp lệ khi ghi lại một giá trị cũ nay trở thành NaN.
- Python đã kiểm tra cú pháp. Các kiểm tra bằng dữ liệu thử không thay thế kiểm thử tải hoặc chạy phần cứng thực.

Kết quả số và giới hạn nằm tại [docs/ket_qua.md](docs/ket_qua.md).

## Khắc phục lỗi thường gặp

| Hiện tượng | Kiểm tra |
|---|---|
| `WinError 10061` tại cổng 8086 | Bật lại InfluxDB; collector tự thử lại các bản tin đã vào hàng đợi |
| Lỗi xác thực hoặc không thấy bucket | Kiểm tra URL, token, org, tên bucket trong `.env` |
| Dashboard không mở | Kiểm tra cửa sổ Streamlit, cổng 8501 và môi trường `.venv64` |
| `NaN`, `n_samples=0` | Không có bản ghi thô trong cửa sổ đó; kiểm tra Wokwi và collector |
| Có mẫu nhưng một đại lượng xử lý bị NaN | Kiểm tra IQR và cờ hợp lệ; không tự kết luận cảm biến hỏng |
| Chu kỳ nhận dài hơn 5 giây | 5 giây là đồng hồ mô phỏng, không bảo đảm 5 giây trên máy tính |
| Một số yêu cầu đo quá hạn | Kiểm tra mô phỏng, MQTT và log; không tính tỷ lệ thành công chỉ từ CSV các lần thành công |

## Giới hạn và hướng cải tiến

- IQR toàn khoảng truy vấn có thể loại một mức cảm biến hợp lệ sau khi người dùng thay đổi giá trị. IQR=0 thì cơ chế hiện tại bỏ qua, do đó cũng có thể bỏ sót một xung bất thường. Có thể đánh giá thêm bộ lọc theo lịch sử cục bộ, ngưỡng vật lý và cơ chế nhận diện chuyển mức.
- Tham số IQR thay đổi khi khoảng một giờ trượt nên dữ liệu quá khứ trong khoảng có thể được tính lại; cờ hợp lệ giải quyết giá trị cũ còn hiện, không biến thuật toán thành xử lý gia tăng.
- Đề xuất ghi bền vững toàn bộ yêu cầu đo và trạng thái thành công/quá hạn để tính tỷ lệ hoàn tất, thay vì chỉ CSV các chu trình thành công và log.
- Có thể bổ sung MQTT QoS 1, bộ đệm phía thiết bị, broker riêng/TLS, dọn lịch sử hàng đợi theo chính sách chống trùng và đo tải dài hơn.
- Broker công cộng hiện dùng kết nối không TLS, không phù hợp dữ liệu nhạy cảm hoặc triển khai sản phẩm.

## Tài liệu tham khảo

- InfluxDB 2.x: https://docs.influxdata.com/influxdb/v2/
- Quy tắc ghi trùng điểm: https://docs.influxdata.com/influxdb/v2/write-data/best-practices/duplicate-points/
- Wokwi: https://docs.wokwi.com/
- Paho MQTT Python: https://eclipse.dev/paho/files/paho.mqtt.python/html/
- Streamlit: https://docs.streamlit.io/
