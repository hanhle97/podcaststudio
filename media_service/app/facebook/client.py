"""Facebook Page publishing through the Graph API: a long video post plus Reels.

Docs: developers.facebook.com/docs/facebook-login/guides/advanced/manual-flow,
      /docs/facebook-login/guides/access-tokens/get-long-lived (page tokens),
      /docs/video-api/guides/publishing (page videos),
      /docs/video-api/guides/reels-publishing (three-phase Reels upload)
Login: the page admin signs in once (/facebook/auth/start); the short-lived user token
is exchanged for a long-lived one, and the Page token taken from it does not expire.
While the Meta app is in development mode, http://localhost redirects are allowed, so
no tunnel is needed.
"""

from __future__ import annotations

import json
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

SCOPES = "pages_show_list,pages_read_engagement,pages_manage_posts,business_management"
POLL_SECONDS = 10


class FacebookError(Exception):
    pass


@dataclass
class PageToken:
    page_id: str
    page_name: str
    access_token: str
    pages: list[dict[str, str]]  # every page the admin manages: [{id, name}]
    connected_at: float = 0.0


class TokenStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> PageToken | None:
        if not self.path.exists():
            return None
        return PageToken(**json.loads(self.path.read_text(encoding="utf-8")))

    def save(self, token: PageToken) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(token.__dict__, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)


def pick_page(pages: list[dict[str, Any]], wanted: str) -> dict[str, Any]:
    """The page named by FACEBOOK_PAGE_ID (id or exact name), else the only/first one."""
    if not pages:
        raise FacebookError("Tài khoản này không quản lý fanpage nào, hoặc chưa cấp quyền "
                            "cho fanpage khi đăng nhập.")
    if wanted:
        for p in pages:
            if wanted in (p.get("id"), p.get("name")):
                return p
        names = ", ".join(f"{p.get('name')} ({p.get('id')})" for p in pages)
        raise FacebookError(f"Không thấy fanpage '{wanted}'. Các trang được cấp quyền: {names}.")
    return pages[0]


class FacebookClient:
    def __init__(self, app_id: str, app_secret: str, redirect_uri: str, store: TokenStore,
                 page_wanted: str = "", version: str = "v23.0",
                 transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        self.app_id = app_id
        self.app_secret = app_secret
        self.redirect_uri = redirect_uri
        self.store = store
        self.page_wanted = page_wanted
        self.version = version
        self.clock = clock
        self.sleep = sleep
        self.graph = f"https://graph.facebook.com/{version}"
        self.graph_video = f"https://graph-video.facebook.com/{version}"
        self._http = httpx.Client(timeout=httpx.Timeout(600, connect=15), transport=transport)

    # --- login ---------------------------------------------------------------------------

    def configured(self) -> bool:
        return bool(self.app_id and self.app_secret and self.redirect_uri)

    def authorize_url(self, state: str) -> str:
        if not self.configured():
            raise FacebookError("Chưa cấu hình FACEBOOK_APP_ID / FACEBOOK_APP_SECRET trong .env.")
        query = {"client_id": self.app_id, "redirect_uri": self.redirect_uri, "state": state,
                 "scope": SCOPES, "response_type": "code"}
        return f"https://www.facebook.com/{self.version}/dialog/oauth?" + \
            urllib.parse.urlencode(query)

    def exchange_code(self, code: str) -> PageToken:
        short = self._get(f"{self.graph}/oauth/access_token", {
            "client_id": self.app_id, "client_secret": self.app_secret,
            "redirect_uri": self.redirect_uri, "code": code})["access_token"]
        long_lived = self._get(f"{self.graph}/oauth/access_token", {
            "grant_type": "fb_exchange_token", "client_id": self.app_id,
            "client_secret": self.app_secret, "fb_exchange_token": short})["access_token"]
        pages = self._get(f"{self.graph}/me/accounts", {
            "access_token": long_lived, "fields": "id,name,access_token,tasks",
            "limit": 100}).get("data", []) or self._other_pages(long_lived)
        if not pages:
            perms = self._get(f"{self.graph}/me/permissions",
                              {"access_token": long_lived}).get("data", [])
            granted = [p["permission"] for p in perms if p.get("status") == "granted"]
            declined = [p["permission"] for p in perms if p.get("status") != "granted"]
            raise FacebookError(
                "Facebook không trả về fanpage nào. Khi đăng nhập hãy chọn fanpage và bật đủ "
                "quyền, hoặc ghi ID trang vào FACEBOOK_PAGE_ID trong .env. Đã cấp: {', '.join(granted) or 'không có'}; bị từ chối: "
                f"{', '.join(declined) or 'không có'}.")
        page = pick_page(pages, self.page_wanted)
        if not page.get("access_token"):
            raise FacebookError(f"Không lấy được quyền đăng cho fanpage {page.get('name')}.")
        token = PageToken(page_id=page["id"], page_name=page.get("name", ""),
                          access_token=page["access_token"],
                          pages=[{"id": p["id"], "name": p.get("name", "")} for p in pages],
                          connected_at=self.clock())
        self.store.save(token)
        return token

    def _other_pages(self, user_token: str) -> list[dict[str, Any]]:
        """Pages that /me/accounts leaves out: the one named by id, and Business-portfolio pages.

        New-experience Pages and Pages owned by a Business portfolio often do not show up
        in /me/accounts even when the person has full control of them.
        """
        fields = "id,name,access_token"
        if self.page_wanted.isdigit():
            try:
                page = self._get(f"{self.graph}/{self.page_wanted}",
                                 {"access_token": user_token, "fields": fields})
                if page.get("access_token"):
                    return [page]
            except FacebookError:
                pass
        found: dict[str, dict[str, Any]] = {}
        try:
            businesses = self._get(f"{self.graph}/me/businesses",
                                   {"access_token": user_token, "fields": "id"}).get("data", [])
            for biz in businesses:
                for edge in ("owned_pages", "client_pages"):
                    for page in self._get(f"{self.graph}/{biz['id']}/{edge}", {
                            "access_token": user_token, "fields": fields,
                            "limit": 100}).get("data", []):
                        if page.get("access_token"):
                            found.setdefault(page["id"], page)
        except FacebookError:
            pass
        return list(found.values())

    def token(self) -> PageToken:
        token = self.store.load()
        if not token:
            raise FacebookError("Chưa kết nối fanpage. Mở http://localhost:8000/facebook/auth/start.")
        return token

    def page_info(self) -> dict[str, Any]:
        t = self.token()
        return self._get(f"{self.graph}/{t.page_id}", {"access_token": t.access_token,
                                                        "fields": "id,name,link"})

    # --- long video post -----------------------------------------------------------------

    def publish_video(self, path: Path, title: str, description: str,
                      progress: Callable[[str], None] = lambda m: None) -> dict[str, Any]:
        """Single-request upload (Graph limit: 1 GB / 20 min, plenty for an episode)."""
        t = self.token()
        progress(f"tải lên {path.stat().st_size / 1e6:.0f} MB")
        with path.open("rb") as f:
            res = self._post(f"{self.graph_video}/{t.page_id}/videos",
                             data={"access_token": t.access_token, "title": title,
                                   "description": description, "published": "true"},
                             files={"source": (path.name, f, "video/mp4")})
        video_id = res["id"]
        info = self.wait_ready(video_id, progress)
        return {"video_id": video_id, "status": info["status"],
                "url": info.get("permalink_url") or f"https://www.facebook.com/{video_id}"}

    # --- Reels -----------------------------------------------------------------------------

    def publish_reel(self, path: Path, description: str,
                     progress: Callable[[str], None] = lambda m: None) -> dict[str, Any]:
        t = self.token()
        start = self._post(f"{self.graph}/{t.page_id}/video_reels",
                           data={"upload_phase": "start", "access_token": t.access_token})
        video_id, upload_url = start["video_id"], start["upload_url"]
        size = path.stat().st_size
        progress(f"tải lên {size / 1e6:.1f} MB")
        r = self._http.post(upload_url, content=path.read_bytes(), headers={
            "Authorization": f"OAuth {t.access_token}", "offset": "0", "file_size": str(size)})
        body = _json(r)
        if r.status_code >= 400 or not body.get("success", True):
            raise FacebookError(f"Tải Reel lên thất bại: {_error(body, r)}")
        self._post(f"{self.graph}/{t.page_id}/video_reels", data={
            "upload_phase": "finish", "video_id": video_id, "video_state": "PUBLISHED",
            "description": description, "access_token": t.access_token})
        info = self.wait_ready(video_id, progress)
        return {"video_id": video_id, "status": info["status"],
                "url": info.get("permalink_url") or f"https://www.facebook.com/reel/{video_id}"}

    def wait_ready(self, video_id: str, progress: Callable[[str], None],
                   timeout_s: float = 1800) -> dict[str, Any]:
        """Poll until Facebook has processed and published the video."""
        deadline = self.clock() + timeout_s
        t = self.token()
        while True:
            info = self._get(f"{self.graph}/{video_id}", {
                "access_token": t.access_token, "fields": "status,permalink_url"})
            st = info.get("status") or {}
            state = st.get("video_status", "")
            if state == "error" or (st.get("processing_phase") or {}).get("status") == "error":
                raise FacebookError(f"Facebook báo lỗi xử lý video {video_id}: "
                                    f"{json.dumps(st, ensure_ascii=False)[:300]}")
            publishing = (st.get("publishing_phase") or {}).get("status")
            if state == "ready" and publishing in (None, "complete"):
                link = info.get("permalink_url", "")
                if link.startswith("/"):
                    link = "https://www.facebook.com" + link
                return {"status": "published", "permalink_url": link}
            if self.clock() > deadline:
                return {"status": f"processing ({state or 'unknown'})",
                        "permalink_url": info.get("permalink_url", "")}
            progress(f"Facebook đang xử lý ({state or '…'})")
            self.sleep(POLL_SECONDS)

    # --- http -----------------------------------------------------------------------------

    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            r = self._http.get(url, params=params)
        except httpx.HTTPError as e:
            raise FacebookError(f"Không gọi được Facebook ({e}).") from e
        return _checked(r)

    def _post(self, url: str, data: dict[str, Any], files: Any = None) -> dict[str, Any]:
        try:
            r = self._http.post(url, data=data, files=files)
        except httpx.HTTPError as e:
            raise FacebookError(f"Không gọi được Facebook ({e}).") from e
        return _checked(r)


def _json(r: httpx.Response) -> dict[str, Any]:
    try:
        body = r.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _error(body: dict[str, Any], r: httpx.Response) -> str:
    err = body.get("error")
    if isinstance(err, dict):
        msg = err.get("error_user_msg") or err.get("message") or ""
        code = err.get("code")
        if code == 190:
            msg += " (token hết hạn hoặc bị thu hồi, hãy kết nối lại fanpage)"
        elif code in (10, 200) or (isinstance(code, int) and 200 <= code < 300):
            msg += " (thiếu quyền, hãy kết nối lại và tick đủ quyền cho fanpage)"
        return f"{msg} [code {code}]".strip()
    return f"HTTP {r.status_code}: {r.text[:200]}"


def _checked(r: httpx.Response) -> dict[str, Any]:
    body = _json(r)
    if r.status_code >= 400 or "error" in body:
        raise FacebookError(_error(body, r))
    return body
