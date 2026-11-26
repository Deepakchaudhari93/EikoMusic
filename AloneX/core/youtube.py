# Copyright (c) 2025 TheHamkerAlone
# Licensed under the MIT License.
# This file is part of AloneXMusic
# ALONE-CODER
# Download backend replaced: search via py-yt-search, download via the
# AloneMusic (Ritesh) API when configured, else direct yt-dlp. The previous
# third-party download API was unreachable from this host, and direct
# yt-dlp hits YouTube's datacenter bot-check.

import asyncio
import logging
import os
import re
from pathlib import Path

import aiohttp
import yt_dlp
from py_yt import Playlist, Video, VideosSearch

from AloneX.helpers._dataclass import Media, Track

logger = logging.getLogger(__name__)

COOKIE_FILE = Path("/tmp/cookies.txt")
_YT_RE = re.compile(
    r"(https?://)?(www\.|m\.|music\.)?(youtube\.com|youtu\.be|youtube-nocookie\.com)/.+",
    re.IGNORECASE,
)
_URL_KEYS = (
    "download_url",
    "downloadUrl",
    "audio_url",
    "audioUrl",
    "video_url",
    "videoUrl",
    "stream_url",
    "streamUrl",
    "file_url",
    "fileUrl",
    "url",
    "link",
)


def _fmt_duration(sec) -> str:
    try:
        sec = int(sec or 0)
    except (TypeError, ValueError):
        sec = 0
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _parse_duration(value):
    """Return (duration_str, duration_sec) from py_yt/yt-dlp shapes."""
    if value is None:
        return "0:00", 0
    if isinstance(value, dict):
        # Video.get: {"secondsText": "225", ...}
        for key in ("secondsText", "seconds", "simpleText", "text"):
            if value.get(key) not in (None, ""):
                return _parse_duration(value.get(key))
        return "0:00", 0
    if isinstance(value, (int, float)):
        return _fmt_duration(value), int(value)
    text = str(value).strip()
    if text.isdigit():
        return _fmt_duration(int(text)), int(text)
    parts = text.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return "0:00", 0
    sec = 0
    for n in nums:
        sec = sec * 60 + n
    return text, sec


def _thumb_url(thumbnails):
    if isinstance(thumbnails, list) and thumbnails:
        last = thumbnails[-1]
        if isinstance(last, dict):
            return last.get("url")
        return str(last)
    return None


def _find_url(payload):
    if isinstance(payload, dict):
        for key in _URL_KEYS:
            val = payload.get(key)
            if isinstance(val, str) and val.startswith(("http://", "https://")):
                return val.strip()
        for nested in payload.values():
            found = _find_url(nested)
            if found:
                return found
    elif isinstance(payload, list):
        for nested in payload:
            found = _find_url(nested)
            if found:
                return found
    return None


class YouTube:
    def __init__(self):
        self.cookie_file = str(COOKIE_FILE) if COOKIE_FILE.exists() else None
        self._titles = {}
        self.api_url = os.getenv(
            "ALONEMUSIC_API_URL", "https://api.riteshyt.in"
        ).rstrip("/")
        self.api_key = os.getenv("ALONEMUSIC_API_KEY", "").strip()

    def valid(self, url) -> bool:
        if not url:
            return False
        return bool(_YT_RE.match(str(url).strip()))

    def _base_opts(self) -> dict:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "geo_bypass": True,
            "extractor_args": {
                "youtube": {"player_client": ["android", "ios", "web"]}
            },
        }
        if self.cookie_file and Path(self.cookie_file).exists():
            opts["cookiefile"] = self.cookie_file
        return opts

    async def save_cookies(self, urls) -> None:
        if not urls:
            return
        try:
            async with aiohttp.ClientSession() as session:
                for i, url in enumerate(urls):
                    try:
                        async with session.get(url) as resp:
                            if resp.status == 200:
                                data = await resp.text()
                                target = (
                                    COOKIE_FILE
                                    if i == 0
                                    else Path(f"/tmp/cookies_{i}.txt")
                                )
                                target.write_text(data, encoding="utf-8")
                                if i == 0:
                                    self.cookie_file = str(target)
                    except Exception as e:
                        logger.error(f"Cookie download failed for {url}: {e}")
        except Exception as e:
            logger.error(f"save_cookies failed: {e}")

    # ---------------- search ----------------

    async def search(self, query, message_id, video=False):
        try:
            if self.valid(query):
                info = await Video.get(query)
            else:
                res = await VideosSearch(query, limit=1).next()
                items = (res or {}).get("result") or []
                info = items[0] if items else None
        except Exception as e:
            logger.error(f"Search exception for {query}: {e}")
            info = None
        if not info or not info.get("id"):
            return await self._search_ytdlp(query, message_id, video)
        vid = info["id"]
        title = info.get("title") or "Unknown"
        self._titles[vid] = title
        dur_str, dur_sec = _parse_duration(info.get("duration"))
        return Media(
            id=vid,
            duration=dur_str,
            duration_sec=dur_sec,
            file_path=None,
            message_id=message_id,
            title=title,
            url=info.get("link") or f"https://www.youtube.com/watch?v={vid}",
            video=video,
        )

    def _extract(self, target, opts):
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(target, download=False)

    async def _search_ytdlp(self, query, message_id, video=False):
        target = query if self.valid(query) else f"ytsearch1:{query}"
        opts = self._base_opts()
        opts["skip_download"] = True
        opts["extract_flat"] = True
        try:
            info = await asyncio.to_thread(self._extract, target, opts)
        except Exception as e:
            logger.error(f"Search exception for {query}: {e}")
            return None
        if not info:
            return None
        if info.get("_type") == "playlist" or "entries" in info:
            entries = [e for e in (info.get("entries") or []) if e]
            if not entries:
                return None
            info = entries[0]
        vid = info.get("id")
        if not vid:
            return None
        title = info.get("title") or "Unknown"
        self._titles[vid] = title
        dur_str, dur_sec = _parse_duration(info.get("duration"))
        return Media(
            id=vid,
            duration=dur_str,
            duration_sec=dur_sec,
            file_path=None,
            message_id=message_id,
            title=title,
            url=info.get("webpage_url")
            or info.get("url")
            or f"https://www.youtube.com/watch?v={vid}",
            video=video,
        )

    # ---------------- playlist ----------------

    async def playlist(self, limit, user, url, video=False):
        try:
            data = await Playlist.getVideos(url)
            videos = (data or {}).get("videos") or []
        except Exception as e:
            logger.error(f"Playlist exception for {url}: {e}")
            videos = []
        if not videos:
            return await self._playlist_ytdlp(limit, user, url, video)
        tracks = []
        for entry in videos[: int(limit) if limit else None]:
            vid = entry.get("id")
            if not vid:
                continue
            title = entry.get("title") or "Unknown"
            self._titles[vid] = title
            dur_str, dur_sec = _parse_duration(entry.get("duration"))
            channel = entry.get("channel") or {}
            tracks.append(
                Track(
                    id=vid,
                    channel_name=channel.get("name")
                    or entry.get("uploader")
                    or "Unknown",
                    duration=dur_str,
                    duration_sec=dur_sec,
                    title=title,
                    url=entry.get("link")
                    or f"https://www.youtube.com/watch?v={vid}",
                    thumbnail=_thumb_url(entry.get("thumbnails")),
                    user=user,
                    video=video,
                )
            )
        return tracks

    async def _playlist_ytdlp(self, limit, user, url, video=False):
        opts = self._base_opts()
        opts["noplaylist"] = False
        opts["extract_flat"] = True
        opts["skip_download"] = True
        if limit:
            opts["playlistend"] = int(limit)
        try:
            info = await asyncio.to_thread(self._extract, url, opts)
        except Exception as e:
            logger.error(f"Playlist exception for {url}: {e}")
            return []
        if not info:
            return []
        tracks = []
        for entry in info.get("entries") or []:
            if not entry:
                continue
            vid = entry.get("id")
            if not vid:
                continue
            title = entry.get("title") or "Unknown"
            self._titles[vid] = title
            dur_str, dur_sec = _parse_duration(entry.get("duration"))
            tracks.append(
                Track(
                    id=vid,
                    channel_name=entry.get("uploader")
                    or entry.get("channel")
                    or "Unknown",
                    duration=dur_str,
                    duration_sec=dur_sec,
                    title=title,
                    url=entry.get("url")
                    or entry.get("webpage_url")
                    or f"https://www.youtube.com/watch?v={vid}",
                    thumbnail=entry.get("thumbnail"),
                    user=user,
                    video=video,
                )
            )
        return tracks

    # ---------------- download ----------------

    async def download(self, video_id, video=False):
        ext = "mp4" if video else "mp3"
        out = Path("downloads") / f"{video_id}.{ext}"
        if out.exists():
            return str(out)
        try:
            if self.api_key and self._titles.get(video_id):
                path = await self._download_api(video_id, video, out)
                if path:
                    return path
            return await asyncio.to_thread(self._download_ytdlp, video_id, video)
        except Exception as e:
            logger.error(f"Download exception for ID {video_id}: {e}")
            return None

    async def _download_api(self, video_id, video, out: Path):
        title = self._titles.get(video_id, "")
        if not title:
            return None
        dl_type = "video" if video else "audio"
        url = f"{self.api_url}/download"
        params = {"query": title, "dl_type": dl_type, "api_key": self.api_key}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
        }
        timeout = aiohttp.ClientTimeout(total=600 if video else 300)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status < 200 or resp.status >= 300:
                    body = (await resp.text())[:200]
                    raise RuntimeError(f"API HTTP {resp.status}: {body}")
                ctype = (resp.headers.get("Content-Type") or "").lower()
                if "json" in ctype:
                    payload = await resp.json(content_type=None)
                    media_url = _find_url(payload)
                    if not media_url:
                        raise RuntimeError("API JSON had no download URL")
                    return await self._save_url(session, media_url, out, headers)
                await self._write_stream(resp, out)
        return self._validate(out)

    async def _save_url(self, session, media_url, out: Path, headers):
        async with session.get(media_url, headers=headers) as resp:
            if resp.status < 200 or resp.status >= 300:
                raise RuntimeError(f"Media HTTP {resp.status}")
            await self._write_stream(resp, out)
        return self._validate(out)

    async def _write_stream(self, resp, out: Path):
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "wb") as fh:
            async for chunk in resp.content.iter_chunked(64 * 1024):
                fh.write(chunk)

    def _validate(self, out: Path):
        if out.exists() and out.stat().st_size > 10_000:
            return str(out)
        if out.exists():
            out.unlink(missing_ok=True)
        return None

    def _download_ytdlp(self, video_id, video):
        ext = "mp4" if video else "mp3"
        out = Path("downloads") / f"{video_id}.{ext}"
        if out.exists():
            return str(out)
        opts = self._base_opts()
        opts["outtmpl"] = f"downloads/{video_id}.%(ext)s"
        if video:
            opts["format"] = "best[ext=mp4]/best"
        else:
            opts["format"] = "bestaudio/best"
            opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ]
        url = f"https://www.youtube.com/watch?v={video_id}"
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
        if out.exists():
            return str(out)
        for p in Path("downloads").glob(f"{video_id}.*"):
            return str(p)
        return None
