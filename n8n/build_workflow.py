"""Generate n8n/workflows/podcast_pipeline.json (n8n 2.x).

Edit this file, then:  python n8n/build_workflow.py
Credentials are not stored here. n8n/import_workflow.sh looks up the ids of the
Telegram / YouTube credentials you created in n8n and attaches them by type.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

OUT = Path(__file__).parent / "workflows" / "podcast_pipeline.json"
LOCAL_OUT = Path(__file__).parent / "workflows" / "podcast_pipeline.local.json"  # gitignored
WORKFLOW_ID = "PodcastPipeline1"
MEDIA = "={{ $env.MEDIA_SERVICE_URL }}"
EP = "$('Episode').item.json.episode_id"

nodes: list[dict] = []
connections: dict[str, dict] = {}


def uid(seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"podcast-studio/{seed}"))


def node(name: str, type_: str, version: float, pos: tuple[int, int], params: dict,
         **extra) -> str:
    nodes.append({"parameters": params, "id": uid(name), "name": name,
                  "type": f"n8n-nodes-base.{type_}", "typeVersion": version,
                  "position": list(pos), **extra})
    return name


def link(src: str, dst: str, out: int = 0) -> None:
    outputs = connections.setdefault(src, {"main": []})["main"]
    while len(outputs) <= out:
        outputs.append([])
    outputs[out].append({"node": dst, "type": "main", "index": 0})


def cond(left: str, op: str, right=None, type_: str = "string") -> dict:
    c = {"id": uid(left + op + str(right)), "leftValue": left,
         "operator": {"type": type_, "operation": op}}
    if right is not None:
        c["rightValue"] = right
    else:
        c["operator"]["singleValue"] = True
    return c


def if_node(name: str, pos, *conditions: dict) -> str:
    return node(name, "if", 2.2, pos, {
        "conditions": {"options": {"caseSensitive": True, "leftValue": "",
                                   "typeValidation": "loose", "version": 2},
                       "conditions": list(conditions), "combinator": "and"},
        "looseTypeValidation": True, "options": {}})


def http(name: str, pos, method: str, url: str, *, json_body: str | None = None,
         full: bool = True, never_error: bool = True, fmt: str | None = None,
         extra: dict | None = None, **node_extra) -> str:
    response: dict = {"fullResponse": full, "neverError": never_error}
    if fmt:
        response["responseFormat"] = fmt
    params: dict = {"method": method, "url": url,
                    "options": {"response": {"response": response}, "timeout": 60000}}
    if json_body is not None:
        params.update({"sendBody": True, "specifyBody": "json", "jsonBody": json_body})
    params.update(extra or {})
    return node(name, "httpRequest", 4.2, pos, params, **node_extra)


def set_node(name: str, pos, fields: dict[str, str]) -> str:
    return node(name, "set", 3.4, pos, {
        "assignments": {"assignments": [
            {"id": uid(name + k), "name": k, "value": v, "type": "string"}
            for k, v in fields.items()]},
        "options": {}})


def code(name: str, pos, js: str, **node_extra) -> str:
    return node(name, "code", 2, pos, {"jsCode": js.strip()}, **node_extra)


def telegram(name: str, pos, text: str, keyboard: list[tuple[str, str]] | None = None) -> str:
    params: dict = {"resource": "message", "operation": "sendMessage",
                    "chatId": "={{ $env.TELEGRAM_CHAT_ID }}", "text": text,
                    "additionalFields": {"appendAttribution": False,
                                         "disable_web_page_preview": True,
                                         "parse_mode": "HTML"}}
    if keyboard:
        params["replyMarkup"] = "inlineKeyboard"
        params["inlineKeyboard"] = {"rows": [{"row": {"buttons": [
            {"text": label, "additionalFields": {"url": url}} for label, url in keyboard]}}]}
    return node(name, "telegram", 1.2, pos, params)


# --- triggers ---------------------------------------------------------------------------

node("Thả file vào inbox", "localFileTrigger", 1, (0, 0), {
    "triggerOn": "folder", "path": "/data/inbox", "events": ["add", "change"],
    "options": {"awaitWriteFinish": True, "usePolling": True, "depth": 0,
                "ignored": "**/{done,rejected,failed}/**"}})
if_node("Chỉ file .md", (220, 0), cond("={{ $json.path }}", "endsWith", ".md"))
http("Tạo episode (inbox)", (440, 0), "POST", f"{MEDIA}/inbox/episodes",
     json_body="={{ JSON.stringify({ path: $json.path }) }}")

node("Upload kịch bản (webhook)", "webhook", 2, (0, 260), {
    "httpMethod": "POST", "path": "podcast/upload", "responseMode": "onReceived",
    "options": {"responseData": "Đã nhận kịch bản, đang xử lý."}},
    webhookId=uid("webhook-upload"))
http("Tạo episode (webhook)", (440, 260), "POST", f"{MEDIA}/episodes", extra={
    "sendBody": True, "contentType": "multipart-form-data",
    "bodyParameters": {"parameters": [{"parameterType": "formBinaryData", "name": "file",
                                       "inputDataFieldName": "={{ Object.keys($binary)[0] }}"}]}})

if_node("Kịch bản hợp lệ?", (660, 120), cond("={{ $json.statusCode }}", "equals", 201, "number"))
set_node("Episode", (880, 100), {
    "episode_id": "={{ $json.body.episode.id }}",
    "inbox_file": "={{ $json.body.inbox_file || '' }}",
    "title": "={{ $json.body.summary.title || $json.body.episode.id }}",
    "warnings": "={{ ($json.body.warnings || []).join('\\n') }}"})

# --- pipeline ---------------------------------------------------------------------------

http("Kiểm tra Voice Studio", (1100, 100), "GET", f"{MEDIA}/tts/health")
if_node("Voice Studio sẵn sàng?", (1320, 100), cond("={{ $json.statusCode }}", "equals", 200,
                                                     "number"))
http("Chạy pipeline", (1540, 80), "POST", f"={{{{ $env.MEDIA_SERVICE_URL }}}}/episodes/"
     f"{{{{ {EP} }}}}/pipeline/run")
set_node("Job", (1760, 80), {"job_id": "={{ $json.body.job_id }}"})
node("Đợi 15 giây", "wait", 1.1, (1980, 80), {"amount": 15, "unit": "seconds"},
     webhookId=uid("wait-poll"))
http("Trạng thái job", (2200, 80), "GET",
     "={{ $env.MEDIA_SERVICE_URL }}/jobs/{{ $('Job').item.json.job_id }}",
     full=False, never_error=False)
node("Phân loại trạng thái", "switch", 3.2, (2420, 80), {
    "rules": {"values": [
        {"conditions": {"options": {"caseSensitive": True, "leftValue": "",
                                    "typeValidation": "strict", "version": 2},
                        "conditions": [cond("={{ $json.status }}", "equals", "succeeded")],
                        "combinator": "and"}, "renameOutput": True, "outputKey": "xong"},
        {"conditions": {"options": {"caseSensitive": True, "leftValue": "",
                                    "typeValidation": "strict", "version": 2},
                        "conditions": [cond("={{ $json.status }}", "equals", "failed")],
                        "combinator": "and"}, "renameOutput": True, "outputKey": "lỗi"}]},
    "options": {"fallbackOutput": "extra", "renameFallbackOutput": "đang chạy"}})
if_node("Quá thời gian?", (2640, 260), cond(
    "={{ ($now.toMillis() - DateTime.fromISO($json.created_at).toMillis()) / 60000 }}",
    "gt", "={{ Number($env.PIPELINE_TIMEOUT_MIN || 120) }}", "number"))

# --- draft + approval -------------------------------------------------------------------

http("Tóm tắt tập", (2640, -100), "GET",
     f"={{{{ $env.MEDIA_SERVICE_URL }}}}/episodes/{{{{ {EP} }}}}/summary",
     full=False, never_error=False)
code("Soạn bản nháp", (2860, -100), r"""
const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const s = $input.first().json;
const body = (s.description_body || '').slice(0, 700);
const shorts = (s.shorts || []).map((c, i) => `  ${i + 1}. ${esc(c.url)}`).join('\n');
const text = [
  '🎙 <b>Bản nháp podcast mới</b>',
  `<b>${esc(s.title)}</b>  (${esc(s.duration_text)})`,
  '',
  esc(body) + (s.description_body && s.description_body.length > 700 ? '…' : ''),
  '',
  `<b>Chương</b>\n${esc(s.chapters_text)}`,
  '',
  `📺 Video: ${esc(s.urls.video)}`,
  `🖼 Thumbnail: ${esc(s.urls.thumbnail)}`,
  `🎧 Audio: ${esc(s.urls.audio)}`,
  shorts ? `📱 Shorts:\n${shorts}` : '',
  '',
  'Bấm <b>Duyệt</b> để upload YouTube (riêng tư), hoặc <b>Từ chối</b>.',
].join('\n');
// n8n 2.x resume URLs already carry ?signature=..., so append with & when needed
const base = $execution.resumeUrl;
const sep = base.includes('?') ? '&' : '?';
return [{ json: { text, approve_url: `${base}${sep}decision=approve`, reject_url: `${base}${sep}decision=reject` } }];
""")
telegram("Gửi bản nháp", (3080, -100), "={{ $json.text }}",
         [("✅ Duyệt", "={{ $json.approve_url }}"), ("❌ Từ chối", "={{ $json.reject_url }}")])
http("Lưu link duyệt", (3190, 60), "POST",
     f"={{{{ $env.MEDIA_SERVICE_URL }}}}/episodes/{{{{ {EP} }}}}/approval",
     json_body="={{ JSON.stringify({ approve_url: $('Soạn bản nháp').first().json.approve_url, "
               "reject_url: $('Soạn bản nháp').first().json.reject_url, hours: 48 }) }}")
node("Chờ duyệt", "wait", 1.1, (3300, -100), {
    "resume": "webhook", "httpMethod": "GET", "responseMode": "onReceived",
    "limitWaitTime": True, "limitType": "afterTimeInterval", "resumeAmount": 48,
    "resumeUnit": "hours",
    "options": {"ignoreBots": True,
                "responseData": "Đã ghi nhận lựa chọn của bạn. Có thể đóng trang này."}},
    webhookId=uid("wait-approval"))
if_node("Được duyệt?", (3520, -100), cond("={{ $json.query?.decision ?? '' }}", "equals",
                                           "approve"))

# --- publish ----------------------------------------------------------------------------

node("Đọc video", "readWriteFile", 1.1, (3740, -260), {
    "operation": "read", "fileSelector": "={{ $('Tóm tắt tập').item.json.paths.video }}",
    "options": {"dataPropertyName": "data"}})
node("Upload YouTube", "youTube", 1, (3960, -260), {
    "resource": "video", "operation": "upload",
    "title": "={{ $('Tóm tắt tập').item.json.title }}", "regionCode": "VN",
    "categoryId": "={{ $env.YOUTUBE_CATEGORY_ID || '22' }}", "binaryProperty": "data",
    "options": {"description": "={{ $('Tóm tắt tập').item.json.description }}",
                "tags": "={{ $('Tóm tắt tập').item.json.tags_csv }}",
                "privacyStatus": "={{ $env.YOUTUBE_PRIVACY || 'private' }}",
                "defaultLanguage": "vi", "selfDeclaredMadeForKids": False,
                "notifySubscribers": False}})
http("Lấy phụ đề SRT", (4180, -260), "GET",
     "={{ $('Tóm tắt tập').item.json.urls.srt.replace(/^https?:\\/\\/[^/]+/, $env.MEDIA_SERVICE_URL) }}",
     full=False, never_error=False, fmt="text")
code("Soạn phụ đề", (4400, -260), r"""
const boundary = 'podcast_studio_srt';
// the n8n YouTube node returns the new video's id as uploadId
const yt = $('Upload YouTube').first().json;
const videoId = yt.uploadId || yt.id;
const meta = { snippet: { videoId, language: 'vi', name: 'Tiếng Việt', isDraft: false } };
const srt = $input.first().json.data;
const body = `--${boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n` +
  `${JSON.stringify(meta)}\r\n--${boundary}\r\nContent-Type: application/x-subrip\r\n\r\n` +
  `${srt}\r\n--${boundary}--`;
return [{ json: { body, boundary, videoId } }];
""")
http("Upload phụ đề", (4620, -260), "POST",
     "https://www.googleapis.com/upload/youtube/v3/captions?part=snippet&uploadType=multipart",
     full=True, never_error=True, extra={
         "authentication": "predefinedCredentialType", "nodeCredentialType": "youTubeOAuth2Api",
         "sendBody": True, "contentType": "raw",
         "rawContentType": "=multipart/related; boundary={{ $json.boundary }}",
         "body": "={{ $json.body }}"})
http("Chuyển script vào done", (4840, -260), "POST", f"{MEDIA}/inbox/archive",
     json_body="={{ JSON.stringify({ path: $('Episode').item.json.inbox_file, to: 'done' }) }}")
# --- YouTube Shorts (first YOUTUBE_SHORTS_MAX clips; API quota ~6 uploads/day) -----------

code("Danh sách Shorts", (5060, -260), r"""
const s = $('Tóm tắt tập').first().json;
const yt = $('Upload YouTube').first().json;
const mainId = yt.uploadId || yt.id;
const max = Number($env.YOUTUBE_SHORTS_MAX ?? 3);
return (s.shorts || []).slice(0, max).map((c) => ({ json: {
  path: c.path, name: c.name,
  title: `${(c.title || s.title).slice(0, 90)} #Shorts`,
  description: `${c.hook || ''}\n\nXem tập đầy đủ: https://youtu.be/${mainId}\n\n#Shorts`.trim(),
} }));
""", alwaysOutputData=True)
if_node("Có Short để đăng?", (5280, -260), cond("={{ $json.path ?? '' }}", "notEmpty"))
node("Đọc Short", "readWriteFile", 1.1, (5500, -360), {
    "operation": "read", "fileSelector": "={{ $json.path }}",
    "options": {"dataPropertyName": "data"}})
node("Upload Short YouTube", "youTube", 1, (5720, -360), {
    "resource": "video", "operation": "upload",
    "title": "={{ $('Danh sách Shorts').item.json.title }}", "regionCode": "VN",
    "categoryId": "={{ $env.YOUTUBE_CATEGORY_ID || '22' }}", "binaryProperty": "data",
    "options": {"description": "={{ $('Danh sách Shorts').item.json.description }}",
                "tags": "={{ $('Tóm tắt tập').first().json.tags_csv }}",
                "privacyStatus": "={{ $env.YOUTUBE_PRIVACY || 'private' }}",
                "defaultLanguage": "vi", "selfDeclaredMadeForKids": False,
                "notifySubscribers": False}},
    onError="continueRegularOutput")
code("Gom Shorts", (5940, -260), r"""
const items = $input.all().map((i) => i.json);
const ids = items.map((j) => j.uploadId || j.id).filter(Boolean);
// n8n's message is generic ("Bad request"); YouTube's own reason (e.g. uploadLimitExceeded)
// is in description
const errors = items.filter((j) => j.error).map((j) =>
  String(j.error.description || j.error.message || j.error).slice(0, 200));
return [{ json: { short_ids: ids, short_errors: errors } }];
""")

# --- TikTok, then Facebook (media-service does the API work; each is skipped when its
#     account is not connected, and a failure never stops the other) ------------------


def platform_chain(key: str, label: str, emoji: str, x: int, skip_reason: str) -> list[str]:
    """status -> connected? -> start publish job -> poll until done -> one text field.

    Returns the four outcome nodes; each sets `{key}_text` for the Telegram message.
    """
    text = f"{key}_text"
    job = f"Job {label}"
    http(f"Trạng thái {label}", (x, -260), "GET", f"{MEDIA}/{key}/status", full=False)
    if_node(f"{label} sẵn sàng?", (x + 220, -260), cond("={{ $json.connected === true }}", "true",
                                                         type_="boolean"))
    http(f"Đăng {label}", (x + 440, -360), "POST",
         f"={{{{ $env.MEDIA_SERVICE_URL }}}}/episodes/{{{{ {EP} }}}}/publish/{key}")
    set_node(job, (x + 660, -360), {"job_id": "={{ $json.body.job_id }}"})
    node(f"Đợi {label}", "wait", 1.1, (x + 880, -360), {"amount": 20, "unit": "seconds"},
         webhookId=uid(f"wait-{key}"))
    http(f"Kiểm tra {label}", (x + 1100, -360), "GET",
         f"={{{{ $env.MEDIA_SERVICE_URL }}}}/jobs/{{{{ $('{job}').item.json.job_id }}}}",
         full=False, never_error=False)
    node(f"Kết quả {label}", "switch", 3.2, (x + 1320, -360), {
        "rules": {"values": [
            {"conditions": {"options": {"caseSensitive": True, "leftValue": "",
                                        "typeValidation": "strict", "version": 2},
                            "conditions": [cond("={{ $json.status }}", "equals", "succeeded")],
                            "combinator": "and"}, "renameOutput": True, "outputKey": "xong"},
            {"conditions": {"options": {"caseSensitive": True, "leftValue": "",
                                        "typeValidation": "strict", "version": 2},
                            "conditions": [cond("={{ $json.status }}", "equals", "failed")],
                            "combinator": "and"}, "renameOutput": True, "outputKey": "lỗi"}]},
        "options": {"fallbackOutput": "extra", "renameFallbackOutput": "đang chạy"}})
    if_node(f"{label} quá lâu?", (x + 1540, -200), cond(
        "={{ ($now.toMillis() - DateTime.fromISO($json.created_at).toMillis()) / 60000 }}",
        "gt", 30, "number"))
    set_node(f"{label} xong", (x + 1540, -500), {text: (
        f"={{{{ $json.result.summary_text || '{emoji} {label}: xong' }}}}")})
    set_node(f"{label} lỗi", (x + 1540, -360), {
        text: f"={{{{ '⚠️ {label} lỗi: ' + ($json.error?.message || 'không rõ') }}}}"})
    set_node(f"{label} quá lâu", (x + 1760, -200), {text: f"⚠️ {label}: quá 30 phút chưa xong"})
    set_node(f"{label} bỏ qua", (x + 440, -160), {text: (
        f"={{{{ '{emoji} {label}: bỏ qua (' + ($json.configured ? ($json.error || "
        f"'{skip_reason}') : 'chưa cấu hình') + ')' }}}}")})
    link(f"Trạng thái {label}", f"{label} sẵn sàng?")
    link(f"{label} sẵn sàng?", f"Đăng {label}", 0)
    link(f"{label} sẵn sàng?", f"{label} bỏ qua", 1)
    link(f"Đăng {label}", job)
    link(job, f"Đợi {label}")
    link(f"Đợi {label}", f"Kiểm tra {label}")
    link(f"Kiểm tra {label}", f"Kết quả {label}")
    link(f"Kết quả {label}", f"{label} xong", 0)
    link(f"Kết quả {label}", f"{label} lỗi", 1)
    link(f"Kết quả {label}", f"{label} quá lâu?", 2)
    link(f"{label} quá lâu?", f"{label} quá lâu", 0)
    link(f"{label} quá lâu?", f"Đợi {label}", 1)
    return [f"{label} xong", f"{label} lỗi", f"{label} quá lâu", f"{label} bỏ qua"]


TIKTOK_DONE = platform_chain("tiktok", "TikTok", "🎵", 6160, "chưa kết nối tài khoản")
set_node("Gom TikTok", (7980, -260), {"tiktok_text": "={{ $json.tiktok_text }}"})
FACEBOOK_DONE = platform_chain("facebook", "Facebook", "📘", 8200, "chưa kết nối fanpage")
code("Soạn tin đã đăng", (10180, -260), r"""
const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const s = $('Tóm tắt tập').first().json;
const yt = $('Upload YouTube').first().json;
const cap = $('Upload phụ đề').first().json;
const shorts = $('Gom Shorts').first().json;
const ep = $('Episode').first().json;
const lines = [
  `✅ <b>Đã upload YouTube</b> (${esc($env.YOUTUBE_PRIVACY || 'private')})`,
  esc(s.title),
  `https://youtu.be/${yt.uploadId || yt.id}`,
  `Phụ đề: ${cap.statusCode === 200 ? 'đã thêm' : 'lỗi ' + cap.statusCode}`,
];
if (shorts.short_ids.length) {
  lines.push(`📱 YouTube Shorts (${shorts.short_ids.length}):`);
  shorts.short_ids.forEach((id) => lines.push(`  https://youtube.com/shorts/${id}`));
}
if (shorts.short_errors.length) {
  lines.push(`⚠️ ${shorts.short_errors.length} Short lỗi: ${esc(shorts.short_errors[0])}`);
}
lines.push(esc($input.first().json.tiktok_text));
if (ep.inbox_file) lines.push('Script đã chuyển vào inbox/done/');
return [{ json: { text: lines.join('\n'), tiktok_text: $input.first().json.tiktok_text } }];
""")

http("Chuyển script vào rejected", (3740, 60), "POST", f"{MEDIA}/inbox/archive",
     json_body="={{ JSON.stringify({ path: $('Episode').item.json.inbox_file, to: 'rejected' }) }}")
set_node("Tin từ chối", (3960, 60), {"text": (
    "={{ $('Chờ duyệt').item.json.query?.decision === 'reject' ? '🚫 Đã từ chối' : "
    "'⌛ Hết 48 giờ chưa duyệt, đã bỏ qua' }}: {{ $('Tóm tắt tập').item.json.title }}\n"
    "Không upload YouTube.")})

# --- report outcomes to media-service (web UI status); never blocks the run ------------

http("Ghi kết quả đăng", (10400, -100), "POST",
     f"={{{{ $env.MEDIA_SERVICE_URL }}}}/episodes/{{{{ {EP} }}}}/publish-status",
     json_body="={{ JSON.stringify({ state: 'published', "
               "youtube_id: $('Upload YouTube').first().json.uploadId || "
               "$('Upload YouTube').first().json.id, "
               "short_ids: $('Gom Shorts').first().json.short_ids, "
               "tiktok: $('Soạn tin đã đăng').first().json.tiktok_text || null, "
               "facebook: $('Soạn tin đã đăng').first().json.facebook_text || null }) }}")
http("Ghi kết quả từ chối", (4180, 200), "POST",
     f"={{{{ $env.MEDIA_SERVICE_URL }}}}/episodes/{{{{ {EP} }}}}/publish-status",
     json_body="={{ JSON.stringify({ state: $('Chờ duyệt').first().json.query?.decision "
               "=== 'reject' ? 'rejected' : 'expired' }) }}")

# --- errors -----------------------------------------------------------------------------

set_node("Lỗi kịch bản", (880, 300), {
    "step": "Đọc kịch bản",
    "detail": "={{ typeof $json.body?.detail === 'string' ? $json.body.detail : "
              "JSON.stringify($json.body ?? $json.error ?? 'Không gọi được media-service') }}"})
set_node("Lỗi Voice Studio", (1540, 300), {
    "step": "Kiểm tra Voice Studio",
    "detail": "={{ $json.body?.message || 'Voice Studio không phản hồi' }}"})
set_node("Lỗi pipeline", (2640, 80), {
    "step": "={{ $json.step_label || $json.step || 'pipeline' }}",
    "detail": "={{ $json.error?.message || 'Không rõ lỗi' }}"})
set_node("Lỗi quá thời gian", (2860, 260), {
    "step": "={{ $json.step_label || 'pipeline' }}",
    "detail": "={{ 'Pipeline chạy quá ' + ($env.PIPELINE_TIMEOUT_MIN || 120) + ' phút' }}"})
node("Lỗi bất ngờ", "errorTrigger", 1, (2860, 520), {})
set_node("Lỗi workflow", (3080, 520), {
    "step": "={{ $json.execution?.lastNodeExecuted || 'n8n' }}",
    "detail": "={{ $json.execution?.error?.message || 'Lỗi không rõ' }}"})
code("Soạn tin lỗi", (3300, 400), r"""
const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const e = $input.first().json;
let file = '';
try { file = $('Episode').first().json.inbox_file || $('Episode').first().json.episode_id; } catch (_) {}
if (!file) { try { file = $('Thả file vào inbox').first().json.path; } catch (_) {} }
const text = [
  `❌ <b>Lỗi ở bước: ${esc(e.step)}</b>`,
  file ? `Tập: ${esc(file)}` : '',
  '',
  `<pre>${esc(String(e.detail).slice(0, 3000))}</pre>`,
].filter((x, i) => x !== '' || i === 2).join('\n');
return [{ json: { text } }];
""")
telegram("Gửi thông báo", (3520, 400), "={{ $json.text }}")

# --- wiring -----------------------------------------------------------------------------

link("Thả file vào inbox", "Chỉ file .md")
link("Chỉ file .md", "Tạo episode (inbox)")
link("Tạo episode (inbox)", "Kịch bản hợp lệ?")
link("Upload kịch bản (webhook)", "Tạo episode (webhook)")
link("Tạo episode (webhook)", "Kịch bản hợp lệ?")
link("Kịch bản hợp lệ?", "Episode", 0)
link("Kịch bản hợp lệ?", "Lỗi kịch bản", 1)
link("Episode", "Kiểm tra Voice Studio")
link("Kiểm tra Voice Studio", "Voice Studio sẵn sàng?")
link("Voice Studio sẵn sàng?", "Chạy pipeline", 0)
link("Voice Studio sẵn sàng?", "Lỗi Voice Studio", 1)
link("Chạy pipeline", "Job")
link("Job", "Đợi 15 giây")
link("Đợi 15 giây", "Trạng thái job")
link("Trạng thái job", "Phân loại trạng thái")
link("Phân loại trạng thái", "Tóm tắt tập", 0)
link("Phân loại trạng thái", "Lỗi pipeline", 1)
link("Phân loại trạng thái", "Quá thời gian?", 2)
link("Quá thời gian?", "Lỗi quá thời gian", 0)
link("Quá thời gian?", "Đợi 15 giây", 1)
link("Tóm tắt tập", "Soạn bản nháp")
link("Soạn bản nháp", "Gửi bản nháp")
link("Gửi bản nháp", "Lưu link duyệt")
link("Lưu link duyệt", "Chờ duyệt")
link("Chờ duyệt", "Được duyệt?")
link("Được duyệt?", "Đọc video", 0)
link("Được duyệt?", "Chuyển script vào rejected", 1)
link("Đọc video", "Upload YouTube")
link("Upload YouTube", "Lấy phụ đề SRT")
link("Lấy phụ đề SRT", "Soạn phụ đề")
link("Soạn phụ đề", "Upload phụ đề")
link("Upload phụ đề", "Chuyển script vào done")
link("Chuyển script vào done", "Danh sách Shorts")
link("Danh sách Shorts", "Có Short để đăng?")
link("Có Short để đăng?", "Đọc Short", 0)
link("Có Short để đăng?", "Gom Shorts", 1)
link("Đọc Short", "Upload Short YouTube")
link("Upload Short YouTube", "Gom Shorts")
link("Gom Shorts", "Trạng thái TikTok")
for done in TIKTOK_DONE:
    link(done, "Gom TikTok")
link("Gom TikTok", "Trạng thái Facebook")
for done in FACEBOOK_DONE:
    link(done, "Soạn tin đã đăng")
link("Soạn tin đã đăng", "Gửi thông báo")
link("Soạn tin đã đăng", "Ghi kết quả đăng")
link("Chuyển script vào rejected", "Tin từ chối")
link("Tin từ chối", "Gửi thông báo")
link("Tin từ chối", "Ghi kết quả từ chối")
for err in ("Lỗi kịch bản", "Lỗi Voice Studio", "Lỗi pipeline", "Lỗi quá thời gian",
            "Lỗi workflow"):
    link(err, "Soạn tin lỗi")
link("Lỗi bất ngờ", "Lỗi workflow")
link("Soạn tin lỗi", "Gửi thông báo")

workflow = {
    "id": WORKFLOW_ID,
    "name": "Podcast pipeline",
    "nodes": nodes,
    "connections": connections,
    "active": False,
    "settings": {"executionOrder": "v1", "errorWorkflow": WORKFLOW_ID,
                 "saveManualExecutions": True, "timezone": "Asia/Ho_Chi_Minh"},
    "pinData": {},
    "meta": {"templateCredsSetupCompleted": False},
    "tags": [],
}

CREDENTIAL_SLOTS = {  # node -> credential type it needs
    "Gửi bản nháp": "telegramApi",
    "Gửi thông báo": "telegramApi",
    "Upload YouTube": "youTubeOAuth2Api",
    "Upload phụ đề": "youTubeOAuth2Api",
    "Upload Short YouTube": "youTubeOAuth2Api",
}


def attach_credentials(found: list[dict]) -> list[str]:
    """Point nodes at existing n8n credentials ([{id, name, type}]) of the right type."""
    by_type: dict[str, dict] = {}
    for c in found:
        by_type.setdefault(c["type"], c)
    attached = []
    for n in nodes:
        ctype = CREDENTIAL_SLOTS.get(n["name"])
        if ctype and ctype in by_type:
            n["credentials"] = {ctype: {"id": by_type[ctype]["id"], "name": by_type[ctype]["name"]}}
            attached.append(f"{n['name']} -> {by_type[ctype]['name']}")
    return attached


if __name__ == "__main__":
    import sys

    out = OUT
    if len(sys.argv) > 2 and sys.argv[1] == "--credentials":
        for line in attach_credentials(json.loads(sys.argv[2])):
            print(f"  credential: {line}")
        out = LOCAL_OUT  # credential ids stay out of the committed file
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
                   newline="\n")
    print(f"✓ {out} ({len(nodes)} nodes)")
