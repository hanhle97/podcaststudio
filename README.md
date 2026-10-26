# Auto Podcast Studio

Sản xuất video podcast tiếng Việt từ **một file `script.md`**. Bạn chỉ viết kịch bản; hệ thống tự
làm giọng đọc (giọng clone của bạn qua Voice Studio), nhạc nền có ducking, phụ đề khớp từng chữ,
video 16:9 + Shorts 9:16 có animation nhẹ, metadata YouTube, thumbnail, gửi bản nháp qua Telegram
và upload YouTube khi bạn bấm **Duyệt**. Chạy local bằng Docker, n8n điều phối.

**Mỗi tập cho ra** (trong `data/episodes/<id>/`):

| File | Nội dung |
|---|---|
| `episode_16x9.mp4` | video 1920×1080, 30fps |
| `shorts/clip_01..05_9x16.mp4` | 3–5 clip dọc 1080×1920, 30–60 giây (tập ngắn thì ít hơn) |
| `final_audio.mp3` / `.wav` | bản audio podcast, -16 LUFS |
| `subtitles.srt` / `.ass`, `words.json`, `karaoke.json` | phụ đề và timing từng từ |
| `metadata.json` | tiêu đề, mô tả, chương `00:00 Tên`, tags, 3 phương án text thumbnail |
| `thumbnail.png` | 1280×720 |

---

## Mục lục

1. [Cách hoạt động](#1-cách-hoạt-động)
2. [Cài đặt](#2-cài-đặt)
3. [Cấu hình](#3-cấu-hình)
4. [Viết kịch bản](#4-viết-kịch-bản)
5. [Sử dụng hằng ngày](#5-sử-dụng-hằng-ngày)
6. [Nhạc, nền, logo, font, brand](#6-nhạc-nền-logo-font-brand)
7. [Kiểm thử](#7-kiểm-thử)
8. [Xử lý sự cố](#8-xử-lý-sự-cố)
9. [Cấu trúc repo và API](#9-cấu-trúc-repo-và-api)

---

## 1. Cách hoạt động

```
data/inbox/tap.md ─▶ n8n ─▶ media-service (FastAPI)
                              parse → tts → mix → align → subtitles → metadata → clips → thumbnail → render
                     n8n ◀── /jobs/{id}
                     Telegram: bản nháp + [Duyệt] [Từ chối]
                     Duyệt ─▶ YouTube (private, có chương + phụ đề) ─▶ data/inbox/done/
```

| Bước | Làm gì |
|---|---|
| **parse** | đọc kịch bản, báo lỗi theo dòng/cột kèm gợi ý |
| **tts** | đọc 1–3 câu cùng đoạn trong một lượt (giữ nhịp tự nhiên), faster-whisper kiểm tra sót chữ rồi thử seed khác hoặc đọc từng câu, cắt lại thành từng câu; áp dụng từ điển phát âm; cache theo hash |
| **mix** | ghép câu với khoảng nghỉ chuẩn, xử lý nhẹ (highpass, compressor), nhạc nền có ducking, loudnorm 2-pass |
| **align** | faster-whisper (GPU nếu có) lấy timing từng từ, căn với chữ trong kịch bản |
| **subtitles** | SRT (≤ 42 ký tự/dòng, ≤ 2 dòng), ASS karaoke, karaoke.json |
| **metadata** | Claude viết tiêu đề, mô tả, tags; chương lấy từ `##` (không có heading thì Claude chia) |
| **clips** | Claude chọn 3–5 đoạn Shorts theo ranh giới câu |
| **thumbnail** | Pillow, 1280×720 |
| **render** | Remotion: nền, waveform, phụ đề tô từ đang đọc, tên chương, progress bar |

Mỗi bước có thể chạy lại riêng. `pipeline/run` bỏ qua bước nào kết quả còn hợp lệ (đầu vào không
đổi), nên chạy lại một tập không sửa gì thì xong ngay.

---

## 2. Cài đặt

**Cần có**

- Windows 10/11 (hoặc Linux/macOS) với **Docker Desktop**. Trên Windows cần **WSL 2**: mở
  Terminal (Admin) chạy `wsl --install --no-distribution` rồi khởi động lại máy.
- **Voice Studio** (OmniVoice) đã clone giọng của bạn, chạy trên máy này hoặc máy khác trong LAN.
- Gói **Claude subscription** (dùng Claude Code để viết metadata), không cần API key.
- Tùy chọn: GPU NVIDIA để faster-whisper chạy nhanh (driver Windows mới, Docker Desktop tự chuyển GPU vào container).

**Chạy lần đầu**

```bash
git clone <repo> podcast-studio && cd podcast-studio
cp .env.example .env
```

Mở `.env`, điền tối thiểu `VOICE_STUDIO_URL` và `VOICE_STUDIO_VOICE_ID` (xem mục 3), đặt
`COMPOSE_PROFILES=gpu` nếu có GPU NVIDIA, rồi:

```bash
docker compose up -d --build
curl http://localhost:8000/health        # {"ok":true}
curl http://localhost:8000/tts/health    # Voice Studio sẵn sàng?
```

Lần build đầu mất 10–20 phút (ffmpeg, Node, Chromium, Remotion, CUDA). Lần chạy align đầu tiên tải
model whisper (~1,6 GB) vào volume `whisper_models`.

- Media service: http://localhost:8000/docs (Swagger)
- n8n: http://localhost:5678

Tên container là `media-service-cpu` hoặc `media-service-gpu` tùy `COMPOSE_PROFILES`; các lệnh bên
dưới ghi `media-service-gpu`, đổi thành `-cpu` nếu bạn dùng CPU.

---

## 3. Cấu hình

Mọi biến nằm trong `.env` (mẫu ở `.env.example`, có comment). File `.env` không được commit.

### 3.1 Voice Studio

Voice Studio phải **đang mở** khi chạy pipeline. Service gọi API gốc `POST /generate` (nhận được
các tham số biểu cảm như `class_temperature`), cổng mặc định 3900, gọi từ cùng máy không cần key.
Mỗi lượt đọc được lưu vào lịch sử Generate của Voice Studio.

Độ tự nhiên chỉnh trong `config/brand.yaml` → `tts`: `speed` (0.88 ≈ 4 tiếng/giây),
`group_max_chars` (độ dài một lượt đọc), `sampling.class_temperature` (0 = rõ chữ nhất, cao hơn thì
biểu cảm hơn nhưng dễ nuốt chữ), `sampling.guidance_scale` (cao hơn = bám chữ, tròn vành hơn) và
`audio.gaps_ms.joined` (nghỉ thêm giữa các câu đọc chung một lượt). Đoạn ghi âm mẫu của voice profile
quyết định nhiều nhất: mẫu đọc phẳng thì giọng clone cũng phẳng.

| Voice Studio chạy ở đâu | `VOICE_STUDIO_URL` |
|---|---|
| Cùng máy với Docker | `http://host.docker.internal:3900` (ghi `http://localhost:3900` cũng được, service tự đổi) |
| Máy khác trong LAN | `http://192.168.x.x:3900`; bật *Settings → Sharing & Remote Access* trong Voice Studio, và đặt `VOICE_STUDIO_API_KEY` nếu bên đó dùng `OMNIVOICE_API_KEY` |

`VOICE_STUDIO_VOICE_ID` là id giọng clone. Xem danh sách: `curl http://localhost:3900/v1/audio/voices`.
Thử kết nối và nghe một câu mẫu:

```bash
docker compose exec media-service-gpu python scripts/probe_voice_studio.py
```

Giọng đọc dùng `seed` cố định (`brand.yaml` → `tts.seed`) để cùng một câu luôn ra cùng giọng.

### 3.2 Claude (metadata, chương, chọn clip)

Chạy qua Claude Code CLI có sẵn trong image, dùng gói subscription của bạn. Tạo token một lần:

```bash
docker compose exec -it media-service-gpu claude setup-token
```

Mở link, đăng nhập, dán mã vào terminal. Token `sk-ant-oat01-…` in ra thì dán vào
`.env` → `CLAUDE_CODE_OAUTH_TOKEN=`, rồi `docker compose up -d`. Đổi model bằng `LLM_MODEL`
(mặc định `sonnet`). Mỗi tập gọi 2–3 lần, trừ vào hạn mức subscription.

### 3.3 n8n, Telegram, YouTube

Credentials **không nằm trong repo**; bạn tạo trong giao diện n8n.

1. Mở http://localhost:5678, tạo tài khoản owner.
2. **Telegram**
   - Nhắn @BotFather `/newbot` để lấy bot token → n8n: *Credentials → Create → Telegram API*, dán token.
   - Mở bot của bạn, bấm **Start** (bot không được nhắn trước cho người chưa chat với nó).
   - Nhắn @userinfobot để lấy **ID của bạn** (chỉ gồm số) → `.env` → `TELEGRAM_CHAT_ID=`.
3. **YouTube** (Google Cloud Console)
   - Tạo project → bật **YouTube Data API v3**.
   - *Google Auth Platform → Audience*: External, thêm email Google sở hữu kênh vào **Test users**.
   - *Clients → Create client*: Web application, Authorized redirect URI
     `http://localhost:5678/rest/oauth2-credential/callback`.
   - n8n: *Credentials → Create → YouTube OAuth2 API*, dán Client ID/Secret, bấm
     **Sign in with Google** (mở n8n bằng `localhost`, không phải IP LAN), gặp cảnh báo
     "chưa xác minh" thì bấm Tiếp tục, chọn đúng kênh.
4. **Link cho nút Duyệt**: Telegram không nhận link `localhost`, và điện thoại phải mở được link.
   - Cùng WiFi: `N8N_WEBHOOK_URL=http://<IP-LAN>:5678/`, `MEDIA_PUBLIC_URL=http://<IP-LAN>:8000`
     (xem IP bằng `ipconfig`; lần đầu Windows Firewall có thể hỏi mở cổng 5678/8000).
   - Ở ngoài nhà: dùng Cloudflare Tunnel / ngrok, đặt hai biến trên theo URL công khai.
   - Giữ `N8N_EDITOR_BASE_URL=http://localhost:5678/`: Google OAuth không chấp nhận IP LAN.
5. Áp dụng và import workflow (tự gắn credential Telegram/YouTube vừa tạo, chỉ đọc id):

```bash
docker compose up -d
sh n8n/import_workflow.sh
```

### 3.4 Shorts lên YouTube và TikTok

Sau khi bấm **Duyệt** và video chính lên YouTube, workflow upload tiếp **tối đa
`YOUTUBE_SHORTS_MAX` Short** (mặc định 3) với tiêu đề clip + `#Shorts` và link về tập đầy đủ.
Hạn mức YouTube API mặc định là 10.000 đơn vị/ngày, mỗi lần upload ~1.600, nên một tập
(video + phụ đề + 3 Short) dùng khoảng 7.000. Short lỗi không chặn phần còn lại.

**TikTok** (tùy chọn, Content Posting API chính thức). Chưa cấu hình thì bước này được bỏ qua.

Mặc định (`TIKTOK_MODE=inbox`) mỗi Short được gửi vào **hộp nháp TikTok** của bạn: mở app TikTok →
Hộp thư → thông báo bản nháp → dán caption (tin Telegram có sẵn caption gợi ý cho từng clip: tiêu đề,
hook, hashtag) → chọn nhạc, chế độ hiển thị → Đăng. Chế độ này dùng được với tài khoản công khai mà
không cần TikTok duyệt app. `TIKTOK_MODE=direct` đăng thẳng, nhưng app chưa qua audit chỉ đăng được lên
**tài khoản đang để Private** (lỗi `unaudited_client_can_only_post_to_private_accounts`). TikTok giới
hạn tối đa 5 bản nháp chờ đăng trong 24 giờ.

1. Vào [developers.tiktok.com](https://developers.tiktok.com) → *Manage apps* → tạo app. TikTok
   yêu cầu icon, mô tả, link *Terms of Service* và *Privacy Policy* (một trang web của bạn).
2. Thêm sản phẩm **Login Kit** và **Content Posting API**, scope `user.info.basic` và
   `video.upload` (chế độ `direct` cần bật *Direct Post* và scope `video.publish`).
3. Tạo **Sandbox**, thêm tài khoản TikTok của bạn làm *target user* (chấp nhận lời mời trong app TikTok).
4. Điền `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET` vào `.env`.
5. Kết nối tài khoản (redirect URI của TikTok bắt buộc HTTPS, nên mở tunnel tạm thời):

   ```bash
   docker run --rm cloudflare/cloudflared:2025.9.1 tunnel --url http://host.docker.internal:8000
   ```

   Lệnh in ra `https://<tên-ngẫu-nhiên>.trycloudflare.com`. Đăng ký
   `https://<tên>.trycloudflare.com/tiktok/auth/callback` làm Redirect URI trong Login Kit, đặt đúng
   chuỗi đó vào `TIKTOK_REDIRECT_URI`, chạy `docker compose up -d`, rồi mở
   `https://<tên>.trycloudflare.com/tiktok/auth/start` và đăng nhập TikTok. Thấy "Đã kết nối TikTok"
   thì **tắt tunnel ngay** (Ctrl+C): trong lúc tunnel bật, media-service mở ra Internet không mật khẩu.
   Lỗi hay gặp: `non_sandbox_target` (tài khoản chưa có trong Target users), `redirect_uri` (URI chưa
   lưu đúng từng ký tự), `scope` (app chưa có scope mà chế độ đang dùng cần).
6. Kiểm tra: `curl http://localhost:8000/tiktok/status` → `"connected": true`. Đổi `TIKTOK_MODE`
   thì phải kết nối lại (bước 5) vì token cần scope khác.

Token được lưu ở `data/secrets/tiktok_token.json` và tự làm mới (refresh token sống 365 ngày); chỉ
cần làm lại bước 5 khi hết hạn hoặc bị thu hồi. App **chưa qua audit** của TikTok chỉ đăng được video
**riêng tư** (`TIKTOK_PRIVACY=SELF_ONLY`); muốn đăng công khai phải gửi app cho TikTok duyệt.
Đăng lại một tập không tạo video trùng (`data/episodes/<id>/publish/tiktok.json`).

### 3.5 Facebook fanpage

(Tùy chọn, Graph API chính thức.) Sau khi bạn **Duyệt**, `episode_16x9.mp4` được đăng **công khai**
thành bài video trên fanpage (tiêu đề + mô tả có mục lục), và tối đa `FACEBOOK_MAX_REELS` Short
(3–90 giây) được đăng thành **Reels** kèm caption + hashtag. Chưa cấu hình thì bước này được bỏ qua;
lỗi Facebook không chặn YouTube/TikTok. Không cần tunnel: khi app Meta còn ở chế độ phát triển,
Facebook cho phép redirect về `http://localhost`.

1. Vào [developers.facebook.com/apps](https://developers.facebook.com/apps) → **Create app**. Chọn use
   case **Manage everything on your Page** (hoặc loại app *Business*), không cần gắn Business portfolio.
   Để app ở chế độ **Development**: chỉ bạn (admin app) dùng, không cần Meta duyệt.
2. Trong app → *Use cases* → *Customize*: kiểm tra `pages_show_list`, `pages_read_engagement`,
   `pages_manage_posts` đang ở trạng thái *Ready for testing* (không cần gửi App Review).
   Mục *Facebook Login* → *Settings*: thêm `http://localhost:8000/facebook/auth/callback` vào
   *Valid OAuth Redirect URIs* (nếu ô này có).
3. *App settings → Basic*: chép **App ID** và **App secret** vào `.env` (`FACEBOOK_APP_ID`,
   `FACEBOOK_APP_SECRET`). Quản lý nhiều trang thì ghi tên hoặc id trang vào `FACEBOOK_PAGE_ID`.
   Chạy `docker compose up -d`.
4. Trên máy chạy Docker, mở http://localhost:8000/facebook/auth/start, đăng nhập Facebook, chọn
   fanpage và cho phép mọi quyền. Thấy "Đã kết nối fanpage …" là xong.
5. Kiểm tra: `curl http://localhost:8000/facebook/status` → `"connected": true`.

Token của trang được lưu ở `data/secrets/facebook_token.json` và không hết hạn; chỉ cần làm lại bước
4 khi đổi mật khẩu Facebook, gỡ quyền app, hoặc muốn đổi sang trang khác. Đăng lại một tập không tạo
bài trùng (`data/episodes/<id>/publish/facebook.json`). Không muốn đăng video dài thì đặt
`FACEBOOK_POST_VIDEO=false` (chỉ đăng Reels).

Các biến khác: `YOUTUBE_PRIVACY` (private | unlisted | public, mặc định private),
`YOUTUBE_CATEGORY_ID` (22 People & Blogs, 27 Education, 28 Science & Technology),
`PIPELINE_TIMEOUT_MIN` (mặc định 120).

---

## 4. Viết kịch bản

Một file Markdown UTF-8. Mọi thứ đều tùy chọn trừ lời đọc; **file chỉ có text thuần cũng chạy được**.

```markdown
---
title: n8n là gì? Tự động hóa công việc trong 5 phút   # không có thì Claude tự đặt
background: kenburns                                  # kenburns | loop | gradient
music: chill_lofi.mp3                                 # file trong assets/music/, bỏ trống = ngẫu nhiên
---

## Mở đầu
Xin chào các bạn, hôm nay mình sẽ nói về n8n.
[pause 1s]

## n8n là gì
n8n là một công cụ tự động hóa quy trình theo kiểu kéo thả.
[music up]

[Lan]: Có cần biết lập trình không?
[Minh]: Không bắt buộc, nhưng biết một chút JavaScript sẽ giúp bạn đi xa hơn.

## Kết luận
Cảm ơn các bạn đã nghe.
```

| Cú pháp | Ý nghĩa |
|---|---|
| `---` … `---` đầu file | frontmatter YAML: `title`, `background`, `music` (đều tùy chọn) |
| `## Tên chương` | mỗi heading là một chương (timestamp thật trong mô tả YouTube). Không có heading nào → Claude tự chia chương |
| `# Tiêu đề` | dùng làm tiêu đề nếu frontmatter không có `title` |
| `[pause 1s]`, `[pause 1.5s]`, `[pause 500ms]`, `[nghỉ 2 giây]` | im lặng thêm N giây (tối đa 30) |
| `[music up]`, `[music up 5s]` | khoảng nghỉ cho nhạc nền lên (mặc định 3 giây) |
| `[Tên]: câu thoại` | người nói khác; gán giọng trong `brand.yaml` → `tts.voices` (chưa gán thì dùng giọng của bạn) |
| dòng trống | ngắt đoạn (nghỉ 600 ms thay vì 250 ms giữa câu) |
| `<!-- ghi chú -->` | không được đọc |

**Mẹo**

- Câu ngắn đọc tự nhiên hơn và phụ đề đẹp hơn; parser cảnh báo câu dài hơn 300 ký tự.
- Từ viết tắt, tên tiếng Anh: thêm vào `config/pronunciation.yaml` (ví dụ `n8n: "en ây en"`).
  Phụ đề vẫn hiện chữ gốc.
- Giá trị frontmatter có dấu `:` thì đặt trong ngoặc kép: `title: "n8n: nhập môn"`.
- Markdown như `**đậm**`, `*nghiêng*`, `[link](url)`, danh sách `-` được bỏ ký hiệu khi đọc.

**Lỗi kịch bản** được báo đủ một lượt, có dòng/cột và gợi ý, ví dụ:

```
Kịch bản có 2 lỗi:
Dòng 6, cột 1: Tag [pasue 2s] không hợp lệ.
    > [pasue 2s]
    Gợi ý: Có phải ý bạn là [pause 2s]?
Dòng 7: Heading '##' không có tên chương.
    Gợi ý: Viết tên chương sau '## ', ví dụ: ## Mở đầu
```

Ví dụ đầy đủ: [`examples/script_demo.md`](examples/script_demo.md) (~62 giây, 3 chương).

---

## 5. Sử dụng hằng ngày

### Giao diện web: http://localhost:8000

Cách dễ nhất. Trang web do media-service phục vụ (không cần cài thêm gì):

- **Thanh trạng thái**: Voice Studio, Claude, n8n, TikTok, Facebook (xanh = sẵn sàng; di chuột vào để xem lý do).
- **+ Tập mới**: kéo thả file `.md` hoặc soạn trực tiếp → **Kiểm tra** (lỗi hiện theo dòng, bấm vào
  để nhảy tới dòng đó) → **Gửi đi** (kịch bản vào `data/inbox/`, n8n chạy như bình thường).
- **Danh sách tập**: trạng thái Đang xử lý (%) / Chờ duyệt / Đã đăng / Lỗi ở bước nào.
- **Trang của tập**: xem video và Shorts, thumbnail, tiêu đề/mô tả/tags (có nút Copy), caption TikTok,
  tải file đầu ra, **Duyệt / Từ chối** (tương đương nút trên Telegram), chạy lại pipeline hoặc một
  bước, gửi lại TikTok, đăng lại Facebook.

Cổng 8000 mở cho cả mạng LAN (để link trong Telegram mở được từ điện thoại), nên ai cùng mạng cũng
vào được trang này. Đừng dùng trên WiFi công cộng.

### Không dùng giao diện web

1. Mở Voice Studio.
2. Thả `tap-moi.md` vào `data/inbox/` (hoặc
   `curl -F file=@tap-moi.md http://localhost:5678/webhook/podcast/upload`).
3. Vài phút sau Telegram nhận bản nháp: tiêu đề, mô tả, chương, link xem video/Shorts/thumbnail.
4. Bấm **Duyệt** → upload YouTube (private) kèm chương và phụ đề; script chuyển vào
   `data/inbox/done/`. Bấm **Từ chối** → `data/inbox/rejected/`. Không bấm trong 48 giờ thì bỏ qua.
5. Kịch bản lỗi → Telegram báo dòng sai. Sửa file trong `data/inbox/` và lưu lại là chạy lại.

**Chạy không qua n8n** (API trực tiếp):

```bash
curl -F file=@tap-moi.md http://localhost:8000/episodes             # trả về episode id
curl -X POST http://localhost:8000/episodes/<id>/pipeline/run       # trả về job_id
curl http://localhost:8000/jobs/<job_id>                            # trạng thái, tiến độ, lỗi ở bước nào
curl http://localhost:8000/episodes/<id>/files                      # danh sách file đầu ra
```

Chạy lại một bước: `POST /episodes/<id>/{tts|mix|align|subtitles|metadata|clips/select|thumbnail}`
hoặc `POST /episodes/<id>/render?format=landscape|short|all`. Cùng nội dung kịch bản luôn ra cùng
episode id.

---

## 6. Nhạc, nền, logo, font, brand

> Asset đi kèm repo (`placeholder_ambient.mp3`, `placeholder_0*.jpg`, `placeholder_loop.mp4`,
> `logo.png`) chỉ là **placeholder** tạo bằng `scripts/make_placeholders.py` (tone sine, gradient).
> Hãy xóa và thay bằng asset **có license** của bạn. Không tự tải nhạc/ảnh trên mạng về dùng.

| Asset | Đặt ở đâu | Ghi chú |
|---|---|---|
| Nhạc nền | `assets/music/*.mp3\|wav\|m4a\|ogg\|flac` | frontmatter `music:` hoặc ngẫu nhiên (cố định theo tập); bài ngắn hơn giọng sẽ loop; tự chuẩn hóa mức |
| Intro/outro | `assets/music/`, khai báo ở `brand.yaml` → `audio.music.intro/outro` | nối bằng crossfade, timing phụ đề tự dời theo |
| Ảnh nền (`kenburns`) | `assets/backgrounds/*.jpg\|png\|webp` | mỗi chương một ảnh, zoom/pan chậm |
| Video nền (`loop`) | `assets/backgrounds/*.mp4\|mov\|webm` | lấy video đầu tiên, lặp vô hạn |
| Logo | `assets/logo.png` (vuông, nền trong suốt) | góc trên video, thumbnail |
| Font | `assets/fonts/` | mặc định Be Vietnam Pro (SIL OFL, xem `OFL.txt`); đổi trong `brand.yaml` → `fonts` |

Chưa có asset cho kiểu nền đã chọn thì tự dùng `gradient`.

**`config/brand.yaml`** (mỗi trường có comment): tên kênh, màu (highlight, nền, chữ, gradient),
font, nền mặc định, khoảng nghỉ giữa câu/đoạn/chương, compressor, loudness (-16 LUFS, TP -1.5),
mức nhạc (`level_db` dưới giọng, `duck_db` lên khi nghỉ), phụ đề, số lượng/độ dài Shorts.
Đổi brand rồi chạy lại pipeline: chỉ những bước bị ảnh hưởng chạy lại.

**Sửa giao diện video**: `remotion/src/` được mount vào container, sửa xong render lại là thấy, không
cần build image. Xem trước bằng Remotion Studio (cần Node trên máy): `cd remotion && npm i && npm run studio`.

> Remotion miễn phí cho cá nhân và công ty đến 3 người; tổ chức lớn hơn cần
> [company license](https://www.remotion.dev/license).

**Sửa workflow n8n**: chỉnh `n8n/build_workflow.py` rồi `sh n8n/import_workflow.sh`. Sửa trong giao
diện n8n cũng được nhưng sẽ bị ghi đè ở lần import sau.

---

## 7. Kiểm thử

```bash
# unit test (parser, filter graph, alignment, phụ đề, schema LLM, ...)
docker compose exec media-service-gpu sh -c "pip install -q pytest pytest-asyncio && python -m pytest -q"

# E2E: script_demo.md -> mọi đầu ra, tự kiểm tra; --clear-tts-cache để gọi Voice Studio thật
docker compose exec media-service-gpu python scripts/e2e.py --clear-tts-cache
```

E2E kiểm tra: đủ file, kích thước/thời lượng video và Shorts, clip khớp ranh giới câu, loudness
(`ebur128`), SRT ≤ 42 ký tự/2 dòng và **khớp từng chữ với kịch bản**, words.json, karaoke, tiêu đề,
tags, chương `00:00`, thumbnail. Kết quả ở `data/episodes/<id>/e2e/report.json`, kèm
`e2e/contact_sheet.png` (frame đầu mỗi chương, đoạn `[music up]`, Shorts, thumbnail) để soi dấu
tiếng Việt, phụ đề, waveform, tên chương.

Công cụ chẩn đoán khác:

```bash
docker compose exec media-service-gpu python scripts/check_ducking.py /data/episodes/<id>   # mức nhạc khi nói / khi nghỉ
```

Phát triển không cần Docker (unit test chạy được trên Windows với Python 3.11+):

```bash
python -m venv .venv
.venv/Scripts/pip install -r media_service/requirements-dev.txt
cd media_service && ../.venv/Scripts/python -m pytest
```

---

## 8. Xử lý sự cố

| Triệu chứng | Nguyên nhân / cách sửa |
|---|---|
| `docker` báo *Docker Desktop is unable to start* / *wsl is not installed* | Cài WSL: Terminal (Admin) `wsl --install --no-distribution`, khởi động lại, mở lại Docker Desktop |
| `/tts/health` 503, Telegram báo "Voice Studio không phản hồi" | Mở Voice Studio; kiểm tra `VOICE_STUDIO_URL`; chạy `scripts/probe_voice_studio.py` |
| "Không tìm thấy giọng …" | Sai `VOICE_STUDIO_VOICE_ID`; xem `GET /v1/audio/voices` của Voice Studio |
| Đổi `.env` mà không có tác dụng | Chạy `docker compose up -d` (tạo lại container), không chỉ `restart` |
| Bước metadata: "Chưa có CLAUDE_CODE_OAUTH_TOKEN" | Làm mục 3.2; token sai/hết hạn thì chạy lại `claude setup-token` |
| Telegram: *chat not found* | Chưa bấm **Start** với bot, hoặc `TELEGRAM_CHAT_ID` không phải ID của bạn |
| Telegram: *bot can't send messages to the bot* | `TELEGRAM_CHAT_ID` đang là ID bot / bot token; dùng ID từ @userinfobot |
| Telegram: *button URL … is invalid: Wrong HTTP URL* | `N8N_WEBHOOK_URL` đang là localhost; đổi sang IP LAN hoặc URL tunnel |
| Bấm Duyệt trên điện thoại không mở được trang | Điện thoại khác mạng, hoặc Windows Firewall chặn cổng 5678 |
| Google: *device_id and device_name are required for private IP* | OAuth đang dùng IP LAN; giữ `N8N_EDITOR_BASE_URL=http://localhost:5678/` và mở n8n bằng localhost |
| Google: *403 access_denied* / chưa hoàn tất xác minh | Thêm email vào *Audience → Test users* |
| Thả file vào inbox mà n8n không chạy | Workflow chưa publish: `sh n8n/import_workflow.sh`; file phải đuôi `.md` và nằm trực tiếp trong `data/inbox/` |
| Align chậm | Chưa dùng GPU: `COMPOSE_PROFILES=gpu`, kiểm tra `docker run --rm --gpus all python:3.11-slim nvidia-smi`; xem `align_report.json` → `device` |
| Chỉ có 1 Short | Tập ngắn: mỗi Short 30–60 giây và không chồng nhau, nên tập ~1 phút chỉ cắt được 1 clip; tập ≥ ~2 phút mới có 3–5 |
| Phụ đề YouTube "lỗi 400" | Kiểm tra quyền credential YouTube (đăng nhập lại *Sign in with Google*) |
| Render lỗi font / ô vuông thay chữ | Thiếu file font trong `assets/fonts/` theo `brand.yaml` → `fonts` |
| Job báo "Media service khởi động lại trong khi job đang chạy" | Container bị restart giữa chừng; chạy lại `pipeline/run`, các bước đã xong sẽ được bỏ qua |

Log: `docker compose logs -f media-service-gpu`, `docker compose logs -f n8n`; chi tiết mỗi job ở
`GET /jobs/<id>` (trường `log`, `error.step`, `error.detail`).

---

## 9. Cấu trúc repo và API

```
podcast-studio/
├── docker-compose.yml          # n8n + media-service (profile cpu | gpu)
├── .env.example
├── config/brand.yaml           # brand, audio, phụ đề, Shorts (có comment)
├── config/pronunciation.yaml   # từ điển phát âm
├── assets/{music,backgrounds,fonts}/, logo.png
├── examples/script_demo.md
├── media_service/
│   ├── app/                    # main.py, jobs.py, pipeline.py, script/, tts/, audio/, align/,
│   │                           # subtitles/, llm/, render/, thumbnail/, inbox.py, summary.py
│   ├── scripts/                # probe_voice_studio.py, e2e.py, check_ducking.py, make_placeholders.py
│   ├── tests/
│   └── Dockerfile
├── remotion/src/compositions/{PodcastLandscape,PodcastShort}.tsx
├── n8n/build_workflow.py, n8n/import_workflow.sh, n8n/workflows/podcast_pipeline.json
└── data/
    ├── inbox/{done,rejected,failed}/
    ├── episodes/<id>/          # mọi file trung gian và đầu ra
    ├── cache/tts/              # giọng đã tạo, theo hash
    └── jobs/                   # trạng thái job
```

| Endpoint | Mô tả |
|---|---|
| `POST /episodes` | upload `script.md` (multipart `file` hoặc form `script`), parse + validate |
| `POST /inbox/episodes` | tạo episode từ file trong `data/inbox/` (n8n dùng) |
| `POST /episodes/{id}/pipeline/run` | chạy toàn bộ, bỏ qua bước còn hợp lệ (`?force=true` để chạy lại hết) |
| `POST /episodes/{id}/{tts,mix,align,subtitles,metadata,clips/select,thumbnail}` | chạy lại một bước |
| `POST /episodes/{id}/render?format=landscape\|short\|all` | render video |
| `GET /jobs/{id}` | trạng thái, tiến độ, log, lỗi ở bước nào (`step_label`) |
| `GET /episodes/{id}/files`, `/files/{path}`, `/summary` | danh sách file, tải file, tóm tắt cho Telegram/YouTube |
| `POST /inbox/archive` | chuyển script sang `done/`, `rejected/`, `failed/` |
| `GET /health`, `GET /tts/health` | kiểm tra service và Voice Studio |
