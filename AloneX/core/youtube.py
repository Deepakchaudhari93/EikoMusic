# Copyright (c) 2025 TheHamkerAlone
# Licensed under the MIT License.
# This file is part of AloneXMusic
# ALONE-CODER
# Download backend replaced: direct yt-dlp (the previous third-party
# download API was unreachable from this host).

import asyncio
import logging
import re
from pathlib import Path

import aiohttp
import yt_dlp

from AloneX.helpers._dataclass import Media, Track

logger = logging.getLogger(__name__)

COOKIE_FILE = Path("/tmp/cookies.txt")
_YT_RE = re.compile(
    r"(https?://)?(www\.|m\.|music\.)?(youtube\.com|youtu\.be|youtube-nocookie\.com)/.+",
    re.IGNORECASE,
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


class YouTube:
    def __init__(self):
        self.cookie_file = str(COOKIE_FILE) if COOKIE_FILE.exists() else None

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

    def _extract(self, target, opts):
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(target, download=False)

    async def search(self, query, message_id, video=False):
        target = query if self.valid(query) else f"ytsearch1:{query}"
        opts = self._base_opts()
        opts["skip_download"] = True
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
        dur = info.get("duration") or 0
        return Media(
            id=vid,
            duration=_fmt_duration(dur),
            duration_sec=int(dur or 0),
            file_path=None,
            message_id=message_id,
            title=info.get("title") or "Unknown",
            url=info.get("webpage_url")
            or f"https://www.youtube.com/watch?v={vid}",
            video=video,
        )

    async def playlist(self, limit, user, url, video=False):
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
            dur = entry.get("duration") or 0
            tracks.append(
                Track(
                    id=vid,
                    channel_name=entry.get("uploader")
                    or entry.get("channel")
                    or "Unknown",
                    duration=_fmt_duration(dur),
                    duration_sec=int(dur or 0),
                    title=entry.get("title") or "Unknown",
                    url=entry.get("url")
                    or entry.get("webpage_url")
                    or f"https://www.youtube.com/watch?v={vid}",
                    thumbnail=entry.get("thumbnail"),
                    view_count=(
                        str(entry.get("view_count"))
                        if entry.get("view_count") is not None
                        else None
                    ),
                    user=user,
                    video=video,
                )
            )
        return tracks

    def _download_sync(self, video_id, video):
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

    async def download(self, video_id, video=False):
        try:
            return await asyncio.to_thread(self._download_sync, video_id, video)
        except Exception as e:
            logger.error(f"Download exception for ID {video_id}: {e}")
            return None
