# Environment Audit

Thời điểm kiểm tra: 2026-08-13 (Asia/Saigon)
Thư mục làm việc: `D:\crawl-yt`

## Kết quả

| Hạng mục | Trạng thái | Phiên bản / ghi chú |
|---|---|---|
| OS | PASS | Microsoft Windows 11 Home, 10.0.26200, 64-bit |
| Python | PASS | 3.11.9 |
| pip | PASS | 24.0 (Python 3.11) |
| Git | PASS | 2.55.0.windows.4 |
| FFmpeg | PASS | 8.1.2 full build (gyan.dev) |
| FFprobe | PASS | 8.1.2 full build (gyan.dev) |
| yt-dlp | WARN | Không tìm thấy command hoặc Python module |
| Node.js | PASS | v24.18.0 |
| SQLite qua Python | PASS | 3.45.1 (`sqlite3` trong standard library) |
| SQLite CLI | WARN | 3.50.6, bản 32-bit |
| PostgreSQL client | WARN | Không tìm thấy `psql` |
| NVIDIA GPU | PASS | NVIDIA GeForce RTX 5060 Ti, 16,311 MiB; driver 610.88 |
| CUDA runtime/toolkit | PASS | CUDA UMD 13.3; `nvcc` 13.3.73 |
| Python virtual environment | WARN | Chưa có `.venv`, `venv`, `env`; `VIRTUAL_ENV` và `CONDA_PREFIX` trống |
| Git repository/status | WARN | Thư mục chưa được khởi tạo Git; không có git status |

## Phụ thuộc còn thiếu

- `yt-dlp`: chưa cần cho skeleton, sẽ cần khi triển khai transcript/video metadata thực tế.
- PostgreSQL client (`psql`): chưa cần khi đang dùng SQLite; chỉ cần khi bắt đầu hỗ trợ PostgreSQL.
- Virtual environment: không phải dependency, nhưng nên tạo trước khi cài package trong các phase sau.

Không có dependency bắt buộc nào bị thiếu để chạy Phase 1.

## Lệnh cài đặt khuyến nghị (không được thực thi)

```powershell
# Tạo môi trường ảo cho dự án
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1

# Cài yt-dlp khi bắt đầu phase thu thập dữ liệu
python -m pip install yt-dlp

# PostgreSQL client, chỉ khi chuyển sang PostgreSQL (chọn một cách)
winget install PostgreSQL.PostgreSQL
# hoặc: choco install postgresql
```

## Cấu trúc thư mục trước khi tạo skeleton

Thư mục `D:\crawl-yt` trống hoàn toàn.

## Lưu ý riêng cho Windows

- SQLite CLI được phát hiện là 32-bit trong môi trường Windows/Python 64-bit.
  CLI này vẫn dùng được độc lập, còn ứng dụng dùng module `sqlite3` của Python.
- Khi tạo virtual environment, PowerShell có thể chặn script kích hoạt. Có thể dùng
  `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` nếu chính sách của máy cho phép,
  hoặc gọi trực tiếp `.\.venv\Scripts\python.exe` mà không cần kích hoạt.
- FFmpeg/FFprobe đã có trên `PATH`. Với các worker chạy nền sau này, cần bảo đảm
  tài khoản chạy worker nhận cùng cấu hình `PATH`.
- NVIDIA đang chạy chế độ WDDM, bình thường trên Windows desktop. Phase 1 không dùng GPU.
