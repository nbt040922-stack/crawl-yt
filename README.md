# crawl-yt

Nen tang Phase 1A cho YouTube Intelligence Engine. Phien ban hien tai tim cac
video theo tu khoa bang yt-dlp, rut ra cac kenh duy nhat va luu chung vao SQLite.
Chua co video crawling, transcript hay phan tich.

## Cai dat tren Windows

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Project hien da co `.venv` cuc bo voi dependency duy nhat la `yt-dlp`.

## Lenh Phase 1A

```powershell
python main.py doctor
python main.py discover "retirement"
python main.py discover "retirement" --limit 100
python main.py discover "retirement" --dry-run
python main.py stats
```

Mac dinh `discover` xem toi da 50 ket qua video. `--dry-run` thuc hien discovery
nhung khong ghi channel vao database.

Database mac dinh la `data/crawl_yt.db`. Co the doi bang bien moi truong:

```powershell
$env:DATABASE_URL = "sqlite:///data/another.db"
```

## Test

Unit test khong truy cap mang:

```powershell
python -m unittest discover -s tests -v
```

## Metadata hien tai

Discovery luu `channel_id`, ten kenh, URL kenh, tu khoa va nguon discovery khi
yt-dlp cung cap. Subscriber count duoc luu neu co san trong search result.
Description, video count va view count co the de `NULL`; cac truong nay se duoc
bo sung sau qua YouTube Data API thay vi goi them mot request cho moi kenh.

## Cac lenh van la placeholder

```powershell
python main.py add-channel <youtube_channel_url>
python main.py crawl <channel>
python main.py crawl-all
```
