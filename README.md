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
docker compose exec media-service-cpu python scripts/probe_voice_studio.py
```

Giọng được cache theo hash (text + voice + tham số) trong `data/cache/tts/`, nên sửa một câu
chỉ tạo lại câu đó. `config/pronunciation.yaml` đổi cách đọc (ví dụ `n8n` → "en ây en")
nhưng phụ đề vẫn hiện chữ gốc. Người nói khác (`[Lan]: ...`) gán giọng trong
`brand.yaml` → `tts.voices`.

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
