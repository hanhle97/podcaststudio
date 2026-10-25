# Auto Podcast Studio

Sản xuất video podcast tiếng Việt từ **một file `script.md`**: giọng clone qua Voice Studio,
nhạc nền có ducking, animation nhẹ (Remotion) và phụ đề. Chạy local bằng Docker, n8n điều phối.

> README đang được hoàn thiện theo từng phase. Trạng thái hiện tại: **Phase 1 – scaffold + parser**.

## Chạy nhanh

```bash
cp .env.example .env        # điền VOICE_STUDIO_*, ANTHROPIC_API_KEY
docker compose up -d --build
curl http://localhost:8000/health
curl -F "file=@examples/script_demo.md" http://localhost:8000/episodes
```

- Media service: http://localhost:8000/docs (Swagger)
- n8n: http://localhost:5678

GPU (faster-whisper trên CUDA): đặt `COMPOSE_PROFILES=gpu` trong `.env`.

## Voice Studio (giọng clone)

Voice Studio (OmniVoice) phải **đang mở** khi chạy pipeline. API của nó tương thích OpenAI
(`POST /v1/audio/speech`), cổng mặc định 3900, gọi từ máy này không cần key.

| Voice Studio chạy ở đâu | `VOICE_STUDIO_URL` |
|---|---|
| Cùng máy với Docker | `http://host.docker.internal:3900` (ghi `http://localhost:3900` cũng được, service tự đổi) |
| Máy khác trong LAN | `http://192.168.x.x:3900`, bật **Settings → Sharing & Remote Access** trong Voice Studio và đặt `VOICE_STUDIO_API_KEY` nếu bên đó dùng `OMNIVOICE_API_KEY` |

`VOICE_STUDIO_VOICE_ID` là id giọng clone (xem bằng `GET {url}/v1/audio/voices`).
Kiểm tra kết nối:

```bash
curl http://localhost:8000/tts/health
docker compose exec media-service-cpu python scripts/probe_voice_studio.py   # hoặc media-service-gpu
```

Giọng được cache theo hash (text + voice + tham số) trong `data/cache/tts/`, nên sửa một câu
chỉ tạo lại câu đó. `config/pronunciation.yaml` đổi cách đọc (ví dụ `n8n` → "en ây en")
nhưng phụ đề vẫn hiện chữ gốc. Người nói khác (`[Lan]: ...`) gán giọng trong
`brand.yaml` → `tts.voices`.

## Nhạc nền

Bỏ file nhạc **có license** vào `assets/music/` (mp3/wav/m4a/ogg/flac). Mỗi tập dùng bài ghi trong
frontmatter (`music: ten_bai.mp3`), nếu không ghi thì chọn ngẫu nhiên, nhưng cố định theo tập
nên chạy lại vẫn ra cùng bài. Bài ngắn hơn giọng sẽ được loop.

> `assets/music/placeholder_ambient.mp3` chỉ là tone sine tạo bằng ffmpeg để test.
> Hãy xóa nó và thay bằng nhạc của bạn.

Mức nhạc chỉnh trong `brand.yaml` → `audio.music`:
- `level_db`: nhạc nằm dưới giọng bao nhiêu dB khi đang nói (mặc định -22).
- `duck_db`: nhạc lên bao nhiêu ở `[music up]` và giữa chapter (mặc định 10).
- `duck_release_ms`: nhạc lên chậm hay nhanh khi giọng dừng. Khoảng nghỉ ngắn giữa câu hầu như không làm nhạc đổi mức.
- `intro` / `outro`: file trong `assets/music/`, nối bằng crossfade.

## Timing từng từ và phụ đề

Phụ đề **luôn là chữ trong kịch bản**, không dùng LLM sửa. faster-whisper chỉ được dùng để lấy
timing: chạy trên clip riêng của từng câu, rồi căn chỉnh với từ trong kịch bản. Từ nào whisper nghe
lệch thì lấy khoảng thời gian whisper nghe được; từ nào không nghe thấy thì nội suy theo số ký tự.
Mức khớp ghi trong `align_report.json`.

- `WHISPER_MODEL=large-v3-turbo` (mặc định). Model tải về volume `whisper_models` ở lần chạy đầu.
- `WHISPER_DEVICE=auto` dùng CUDA nếu có, không thì CPU. Với GPU, đặt `COMPOSE_PROFILES=gpu`
  (cần Docker Desktop + WSL2 + driver NVIDIA). Trên RTX 5060 Ti, align tập 1 phút mất ~14 giây.

Đầu ra: `words.json` (`[{word, start, end, sentence_id, chapter}]`), `subtitles.srt` (≤ 42 ký
tự/dòng, ≤ 2 dòng, ngắt theo câu và dấu phẩy, không vắt chapter), `subtitles.ass` + `karaoke.json`
(cụm 2–4 từ, tô sáng từng từ, cho Shorts).

## LLM: metadata, chapters, chọn clip (Claude Code subscription)

LLM chạy qua **Claude Code CLI** (cài sẵn trong image) bằng gói Claude subscription của bạn,
không cần API key. Tạo token một lần:

```bash
docker compose exec -it media-service-gpu claude setup-token   # hoặc media-service-cpu
```

Mở link, đăng nhập, dán mã vào terminal. Token `sk-ant-oat01-…` in ra thì dán vào `.env`
(`CLAUDE_CODE_OAUTH_TOKEN=`), rồi chạy `docker compose up -d` để nạp lại. Đổi model bằng
`LLM_MODEL` (mặc định `sonnet`). Mỗi tập gọi khoảng 2–3 lần, trừ vào hạn mức subscription.

- `metadata.json`: tiêu đề (dùng `title` trong frontmatter nếu có, không thì ≤ 70 ký tự), mô tả
  kèm mục lục YouTube `00:00 Tên`, 10–15 tags, 3 phương án text thumbnail. Chapters lấy từ `##`
  kèm timestamp thật; chỉ khi không có heading thì LLM mới chia (mỗi chương ≥ 10 giây).
- `clips.json`: 3–5 đoạn 30–60 giây cho Shorts (tập ngắn thì ít hơn). LLM chọn theo **id câu**,
  nên timestamp luôn khớp ranh giới câu; code kiểm tra độ dài và chồng lấn.
- Mọi output được validate bằng Pydantic; sai thì hỏi lại kèm lỗi (tối đa 3 lần).
- `thumbnail.png` 1280×720: ảnh nền đầu tiên trong `assets/backgrounds/` (hoặc gradient), logo,
  text thumbnail đầu tiên.

## Video (Remotion)

Hai composition trong `remotion/src/compositions/`: `PodcastLandscape` (1920×1080, 30fps)
và `PodcastShort` (1080×1920, nhận `startSec`/`endSec`). Service gọi `npx remotion bundle` một
lần rồi `npx remotion render` cho từng video:

```bash
curl -X POST "http://localhost:8000/episodes/<id>/render?format=all"   # landscape | short | all
```

**Nền** (frontmatter `background:` hoặc `brand.yaml` → `video.background`):
- `kenburns`: ảnh trong `assets/backgrounds/` (jpg/png/webp), mỗi chapter một ảnh, zoom/pan chậm.
- `loop`: video đầu tiên trong `assets/backgrounds/` (mp4/mov/webm), lặp vô hạn.
- `gradient`: gradient chuyển màu theo `brand.yaml` → `colors.gradient`. Kiểu chọn mà chưa có asset thì tự rơi về gradient.

> Ảnh `placeholder_0*.jpg`, `placeholder_loop.mp4` và `assets/logo.png` được tạo bằng
> `scripts/make_placeholders.py`. Hãy thay bằng ảnh/logo của bạn.

Font: `assets/fonts/BeVietnamPro-*.ttf` (SIL OFL, xem `assets/fonts/OFL.txt`).

Sửa giao diện: `remotion/src` được mount vào container nên chỉ cần render lại, không phải build lại
image. Xem trước bằng Remotion Studio (cần Node trên máy): `cd remotion && npm i && npm run studio`.

> Remotion miễn phí cho cá nhân và công ty đến 3 người; tổ chức lớn hơn cần
> [company license](https://www.remotion.dev/license).

Kiểm tra ducking của một tập:

```bash
docker compose exec media-service-cpu python scripts/check_ducking.py /data/episodes/<id>   # hoặc media-service-gpu
```

## Cấu trúc

| Thư mục | Nội dung |
|---|---|
| `config/brand.yaml` | màu, font, logo, loudness, mức nhạc, nền mặc định (có comment từng trường) |
| `config/pronunciation.yaml` | từ điển phát âm áp dụng trước khi gửi TTS |
| `assets/` | nhạc, nền, font, logo do bạn tự bỏ vào (có license) |
| `media_service/` | FastAPI + ffmpeg + faster-whisper |
| `remotion/` | composition video |
| `n8n/workflows/` | workflow điều phối |
| `data/inbox/` | thả `script.md` vào đây là chạy |
| `data/episodes/{id}/` | mọi file trung gian và đầu ra của một tập |

## Phát triển (không cần Docker)

```bash
python -m venv .venv
.venv/Scripts/pip install -r media_service/requirements-dev.txt
cd media_service && ../.venv/Scripts/python -m pytest
../.venv/Scripts/uvicorn app.main:app --reload
```
