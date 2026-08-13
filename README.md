# crawl-yt

Nen tang Phase 2A cho YouTube Intelligence Engine. Du an tim channel, liet ke
video, bo sung metadata va luu transcript theo timestamp. YouTube captions la
duong mac dinh re; audio va ASR cuc bo luon la fallback dat tien, phai bat ro.

## Cai dat tren Windows

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Project hien da co `.venv` cuc bo voi dependency bat buoc duy nhat la `yt-dlp`.
Local ASR la tuy chon va khong duoc cai tu dong:

```powershell
python -m pip install -e ".[asr]"
```

Lenh tren cai `faster-whisper` vao virtualenv cua project. Khong can thay doi
CUDA driver/toolkit he thong.

## Lenh Phase 2A

```powershell
python main.py doctor
python main.py discover "retirement"
python main.py discover "retirement" --limit 100
python main.py discover "retirement" --dry-run
python main.py crawl UCxxxxxxxxxxxx --limit 20
python main.py crawl UCxxxxxxxxxxxx
python main.py crawl UCxxxxxxxxxxxx --full
python main.py crawl UCxxxxxxxxxxxx --known-stop-threshold 5
python main.py crawl-due --limit 20
python main.py crawl-all --max-channels 3 --limit-per-channel 10
python main.py enrich VIDEO_ID
python main.py enrich-channel UCxxxxxxxxxxxx --limit 20
python main.py enrich-pending --limit 50
python main.py transcript VIDEO_ID
python main.py transcript VIDEO_ID --lang en
python main.py transcript VIDEO_ID --lang en --force
python main.py transcript VIDEO_ID --fallback
python main.py transcript VIDEO_ID --lang en --fallback --allow-audio
python main.py transcript-channel UCxxxxxxxxxxxx --limit 20 --lang en
python main.py transcript-channel UCxxxxxxxxxxxx --limit 5 --fallback --allow-audio
python main.py transcript-pending --limit 50 --lang en
python main.py transcript-pending --limit 10 --fallback
python main.py stats
```

Mac dinh `discover` xem toi da 50 ket qua video. `--dry-run` thuc hien discovery
nhung khong ghi channel vao database.

`crawl` chi chap nhan channel ID da co trong database (hoac URL
`/channel/UC...`). Lan crawl dau tien la full; cac lan sau mac dinh incremental.
Incremental doc upload tu moi den cu va dung sau 5 video lien tiep da co trong
database. `--known-stop-threshold` doi nguong nay; mot video moi se reset bo dem.
Toi uu nay dua tren thu tu upload newest-first cua YouTube/yt-dlp.

`--full` tat early-stop va cho phep liet ke toan bo lich su; `--limit` van gioi
han so entry trong ca hai mode. Provider tra iterator lazy de service co the
dung ma khong tao list hang nghin video trong bo nho.

`crawl-due --limit N` chi crawl tuan tu cac channel co `next_crawl_at` da den,
va tiep tuc neu mot channel loi. Khoang lap Phase 2A mac dinh la 24 gio; khong
co scheduler nen lenh khong tu chay. `crawl-all` duoc giu de tuong thich, con
`crawl-due` la lua chon van hanh uu tien.

Enumeration la thao tac nhe: yt-dlp doc flat upload list, khong fetch full tung
video. Enrichment nang hon: moi video tao mot full metadata request nhung van
luon `download=False`. `enrich-channel` va `enrich-pending` bat buoc co
`--limit`; batch chay tuan tu va tiep tuc neu mot video loi.

Transcript mac dinh uu tien ngon ngu `en`, `en-US`, `en-GB`. Manual caption
duoc uu tien hon auto-generated caption; khong dung subtitle da dich. Loi tam
thoi khi tai caption duoc thu toi da 3 lan. Lenh don dung transcript da luu neu
phu hop, con `--force` se fetch va upsert lai.

Chinh sach fallback duoc tach ro de an toan khi crawl quy mo lon:

- Khong co flag: chi YouTube manual/auto captions.
- `--fallback`: captions, sau do OpenCLI neu executable co san.
- `--fallback --allow-audio`: them tai `bestaudio` tam thoi va ASR cuc bo bang
  `faster-whisper`. Audio bi xoa sau ca thanh cong lan that bai.
- `--allow-audio` dung mot minh bi tu choi.

`transcript-channel` va `transcript-pending` bat buoc co `--limit`, chay tuan tu
va tiep tuc neu mot video loi. Audio ASR khong bao gio tu chay chi vi bat
`--fallback`, vi chi phi tai va suy luan cao hon captions rat nhieu.

Database mac dinh la `data/crawl_yt.db`. Channel metadata canonical duoc luu
trong `channels`; moi quan he channel/keyword/source duoc luu rieng trong
`channel_discoveries`, nen mot channel co the duoc tim thay boi nhieu tu khoa.
Video canonical duoc luu trong `videos`; enumeration dung flat extraction va
khong goi full metadata rieng cho tung video. Cac field khong co san se la NULL.
`metadata_enriched_at` chi duoc set sau full metadata request thanh cong.
Trang thai crawl nam rieng trong `channel_crawl_state`, gom lan start/success/
failure, video moi nhat da thay, failure lien tiep, tong lan crawl va lich tiep
theo. Schema duoc tao additive, khong sua/xoa du lieu Phase 1.
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

Nguon transcript duoc giu nguyen: `youtube_manual`, `youtube_auto`, `opencli`,
hoac `local_whisper`. `transcript_attempts` ghi lich su provider, trang thai va
loai loi de chan doan; Phase 1E chua tu dong suppress cac video tung that bai.
OpenCLI chi duoc dung khi tim thay executable. Neu output khong co timestamp,
segments duoc luu rong thay vi tao timestamp gia.

## Cac lenh van la placeholder

```powershell
python main.py add-channel <youtube_channel_url>
```
