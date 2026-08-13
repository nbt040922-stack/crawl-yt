# crawl-yt

Nen tang Phase 1D cho YouTube Intelligence Engine. Du an tim channel, liet ke
video, bo sung metadata va luu YouTube captions theo timestamp. Khong tai
video/audio, khong Whisper va chua chay phan tich.

## Cai dat tren Windows

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Project hien da co `.venv` cuc bo voi dependency duy nhat la `yt-dlp`.

## Lenh Phase 1D

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
python main.py transcript VIDEO_ID
python main.py transcript VIDEO_ID --lang en
python main.py transcript VIDEO_ID --lang en --force
python main.py transcript-channel UCxxxxxxxxxxxx --limit 20 --lang en
python main.py transcript-pending --limit 50 --lang en
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

Transcript mac dinh uu tien ngon ngu `en`, `en-US`, `en-GB`. Manual caption
duoc uu tien hon auto-generated caption; khong dung subtitle da dich. Lenh don
dung transcript da luu neu phu hop, con `--force` se fetch va upsert lai.
`transcript-channel` va `transcript-pending` bat buoc co `--limit`, chay tuan tu
va tiep tuc neu mot video khong co subtitle. Chua co Whisper fallback.

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

Transcript luu ca full text va danh sach segment co `start`, `end`, `text`.
Subtitle markup va whitespace duoc lam sach; rolling captions chi duoc dedup
bao thu khi cac cue overlap.

## Cac lenh van la placeholder

```powershell
python main.py add-channel <youtube_channel_url>
```
