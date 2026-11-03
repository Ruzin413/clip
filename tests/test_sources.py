import re

import pytest

from clipstudio.models import MEDIA_KEY_PATTERN, Platform
from clipstudio.sources import UnsupportedSourceError, parse_source

YT = Platform.YOUTUBE
TW = Platform.TWITCH
KI = Platform.KICK
TWITCH_SLUG = "InventiveSucculentEelTwitchRaid-4wKybJuInPl3814e"
KICK_UUID = "01a19e29-f7e8-7227-9f0a-21d915872a88"
KICK_CLIP = "clip_01M64B2EM73ZDKW12FKGZNXER8"

ACCEPTED = [
    ("https://www.youtube.com/watch?v=jNQXAC9IVRw", YT, "video", "jNQXAC9IVRw"),
    ("https://youtube.com/watch?feature=share&v=jNQXAC9IVRw&t=10s", YT, "video", "jNQXAC9IVRw"),
    ("https://m.youtube.com/watch?v=jNQXAC9IVRw", YT, "video", "jNQXAC9IVRw"),
    ("https://youtu.be/jNQXAC9IVRw?t=10", YT, "video", "jNQXAC9IVRw"),
    ("https://www.youtube.com/shorts/jNQXAC9IVRw", YT, "video", "jNQXAC9IVRw"),
    ("https://www.youtube.com/embed/jNQXAC9IVRw", YT, "video", "jNQXAC9IVRw"),
    ("https://www.youtube.com/live/jNQXAC9IVRw?si=abc", YT, "video", "jNQXAC9IVRw"),
    ("youtube.com/watch?v=jNQXAC9IVRw", YT, "video", "jNQXAC9IVRw"),
    ("https://www.twitch.tv/videos/2912355720", TW, "video", "2912355720"),
    ("https://m.twitch.tv/videos/2912355720?t=1h2m3s", TW, "video", "2912355720"),
    ("https://www.twitch.tv/shroud/v/2912355720", TW, "video", "2912355720"),
    (f"https://clips.twitch.tv/{TWITCH_SLUG}", TW, "clip", TWITCH_SLUG),
    (f"https://www.twitch.tv/shroud/clip/{TWITCH_SLUG}", TW, "clip", TWITCH_SLUG),
    (f"https://go.twitch.tv/shroud/clip/{TWITCH_SLUG}?filter=clips", TW, "clip", TWITCH_SLUG),
    (f"https://kick.com/xqc/videos/{KICK_UUID}", KI, "video", KICK_UUID),
    (f"https://www.kick.com/xqc/videos/{KICK_UUID}/", KI, "video", KICK_UUID),
    (f"https://kick.com/xqc/clips/{KICK_CLIP}", KI, "clip", KICK_CLIP),
    (f"https://kick.com/xqc?clip={KICK_CLIP}", KI, "clip", KICK_CLIP),
]

REJECTED = [
    "https://www.twitch.tv/shroud",
    "https://www.twitch.tv/shroud/videos",
    "https://kick.com/xqc",
    "https://kick.com/xqc/videos",
    "https://vimeo.com/76979871",
    "https://www.youtube.com/@jawed",
    "https://www.youtube.com/watch?v=short",
    "https://notyoutube.com/watch?v=jNQXAC9IVRw",
    "https://youtube.com.evil.example/watch?v=jNQXAC9IVRw",
    "not a url",
    "",
]


@pytest.mark.parametrize(("url", "platform", "kind", "source_id"), ACCEPTED)
def test_parse_source_accepts_supported_links(url, platform, kind, source_id):
    ref = parse_source(url)
    assert (ref.platform, ref.kind, ref.id) == (platform, kind, source_id)
    assert ref.media_key == f"{platform}_{source_id}"
    assert re.match(MEDIA_KEY_PATTERN, ref.media_key)


@pytest.mark.parametrize("url", REJECTED)
def test_parse_source_rejects_unsupported_links(url):
    with pytest.raises(UnsupportedSourceError) as exc_info:
        parse_source(url)
    message = str(exc_info.value)
    assert all(name in message for name in ("YouTube", "Twitch", "Kick"))


def test_parse_source_url_drops_fragment():
    ref = parse_source("https://www.youtube.com/watch?v=jNQXAC9IVRw#__youtubedl_smuggle=%7B%7D")
    assert ref.url == "https://www.youtube.com/watch?v=jNQXAC9IVRw"


def test_parse_source_url_gains_scheme():
    assert parse_source("kick.com/xqc/clips/" + KICK_CLIP).url == f"https://kick.com/xqc/clips/{KICK_CLIP}"
