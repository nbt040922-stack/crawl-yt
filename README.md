# crawl-yt

Nen tang Phase 1C cho YouTube Intelligence Engine. Du an tim channel, liet ke
video va bo sung metadata co chon loc bang yt-dlp. Khong tai video/audio,
transcript hay chay phan tich.

## Cai dat tren Windows

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Project hien da co `.venv` cuc bo voi dependency duy nhat la `yt-dlp`.

## Lenh Phase 1C

```powershell
python main.py doctor
python main.py discover "retirement"
python main.py discover "retirement" --limit 100
python main.py discover "retirement" --dry-run
python main.py crawl UCxxxxxxxxxxxx --limit 20
python main.py crawl UCxxxxxxxxxxxx
python main.py crawl-all --max-channels 3 --limit-per-channel 10
python main.py enrich VIDEO_ID
python main.py enrich-channel UCxxxxxxxxxxxx --limit 20
python main.py enrich-pending --limit 50
python main.py stats
```

Mac dinh `discover` xem toi da 50 ket qua video. `--dry-run` thuc hien discovery
nhung khong ghi channel vao database.

`crawl` chi chap nhan channel ID da co trong database (hoac URL
`/channel/UC...`). Khong co `--limit` se liet ke toan bo upload. `crawl-all`
chay tuan tu, tiep tuc khi mot channel loi; nen dung cac limit nho khi smoke test.

Enumeration la thao tac nhe: yt-dlp doc flat upload list, khong fetch full tung
video. Enrichment nang hon: moi video tao mot full metadata request nhung van
luon `download=False`. `enrich-channel` va `enrich-pending` bat buoc co
`--limit`; batch chay tuan tu va tiep tuc neu mot video loi.

Database mac dinh la `data/crawl_yt.db`. Channel metadata canonical duoc luu
trong `channels`; moi quan he channel/keyword/source duoc luu rieng trong
`channel_discoveries`, nen mot channel co the duoc tim thay boi nhieu tu khoa.
Video canonical duoc luu trong `videos`; enumeration dung flat extraction va
khong goi full metadata rieng cho tung video. Cac field khong co san se la NULL.
`metadata_enriched_at` chi duoc set sau full metadata request thanh cong.
Co the doi database bang bien moi truong:

```powershell
$env:DATABASE_URL = "sqlite:///data/another.db"
```

## Test

Unit test khong truy cap mang:

```powershell
python -m unittest discover -s tests -v
```

## Metadata hien tai

Discovery chi chap nhan YouTube channel ID on dinh dang `UC...`; uploader handle
mo ho bi bo qua. Subscriber count duoc luu neu co san trong search result.
Description, video count va view count co the de `NULL`; cac truong nay se duoc
bo sung sau qua YouTube Data API thay vi goi them mot request cho moi kenh.

Video enumeration thuong co `video_id`, title va URL. Description, publish time,
duration va cac count chi duoc luu neu flat yt-dlp result cung cap.
Selective enrichment co the bo sung description, publish time, duration, view,
like/comment count, thumbnail, availability, tags, categories va language.

## Cac lenh van la placeholder

```powershell
python main.py add-channel <youtube_channel_url>
```
