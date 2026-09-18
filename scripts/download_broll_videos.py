from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import json, shutil, subprocess, time

ROOT = Path("shanwei-video-assets")
SRC = ROOT / "video_sources"
OUT = ROOT / "broll_generic"
SRC.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)

VIDEOS = [
    {
        "id": "v01",
        "file": "2020年8月16日 海南疍家人开渔：传统祭海仪式后鸣笛出海.webm",
        "out": "v01_south_china_fishing_departure.mp4",
        "label": "通用华南渔港/渔船出海氛围镜头（不是汕尾实拍）",
        "author": "中国新闻网",
        "license": "CC BY 3.0",
        "page": "https://commons.wikimedia.org/wiki/File:2020年8月16日_海南疍家人开渔：传统祭海仪式后鸣笛出海.webm",
        "start": "00:00:35",
        "duration": "8",
    },
    {
        "id": "v02",
        "file": "秦皇岛山海关老龙头景区的沙滩海水.webm",
        "out": "v02_generic_beach_waves.mp4",
        "label": "通用海浪/沙滩过渡镜头（不是汕尾实拍）",
        "author": "Liuxingy",
        "license": "CC BY-SA 4.0",
        "page": "https://commons.wikimedia.org/wiki/File:秦皇岛山海关老龙头景区的沙滩海水.webm",
        "start": "00:00:00",
        "duration": "5.5",
    },
    {
        "id": "v03",
        "file": "竹灣海灘的海浪聲.webm",
        "out": "v03_generic_waves_with_sound.mp4",
        "label": "通用海浪与环境声镜头（不是汕尾实拍）",
        "author": "LN9267",
        "license": "CC BY 3.0",
        "page": "https://commons.wikimedia.org/wiki/File:竹灣海灘的海浪聲.webm",
        "start": "00:00:04",
        "duration": "8",
    },
]

UA = "ShanweiVideoAssetCollector/1.0 (educational project)"

def resolve_url(filename):
    params = {
        "action": "query",
        "format": "json",
        "prop": "imageinfo",
        "iiprop": "url|mime|mediatype",
        "titles": "File:" + filename,
    }
    api = "https://commons.wikimedia.org/w/api.php?" + urlencode(params)
    req = Request(api, headers={"User-Agent": UA, "Accept": "application/json"})
    with urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode("utf-8"))
    page = next(iter(data["query"]["pages"].values()))
    return page["imageinfo"][0]["url"]

def download(url, target):
    last = None
    for attempt in range(1, 5):
        try:
            req = Request(url, headers={"User-Agent": UA})
            with urlopen(req, timeout=240) as r, target.open("wb") as f:
                shutil.copyfileobj(r, f)
            return
        except (HTTPError, URLError) as e:
            last = e
            print("download failed", e, "attempt", attempt)
            time.sleep(15 * attempt)
    raise last

manifest = []
for v in VIDEOS:
    print("Resolving", v["file"])
    url = resolve_url(v["file"])
    src = SRC / (v["id"] + ".webm")
    if not src.exists():
        download(url, src)
    out = OUT / v["out"]
    cmd = [
        "ffmpeg", "-y",
        "-ss", v["start"], "-i", str(src),
        "-t", v["duration"],
        "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720",
        "-r", "30",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(out)
    ]
    subprocess.run(cmd, check=True)
    manifest.append({
        "id": v["id"],
        "path": str(out),
        "label": v["label"],
        "author": v["author"],
        "license": v["license"],
        "source_page": v["page"],
        "source_url": url,
        "note": "Generic B-roll only; do not present this clip as footage shot in Shanwei."
    })
    time.sleep(5)

(ROOT / "video_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

lines = [
    "# 视频素材说明",
    "",
    "本目录的 `broll_generic/` 是公开许可的通用补充镜头，**不是汕尾实拍**。",
    "汕尾地标真实性请以 `originals/`、`gemini_refs_16x9/` 内的汕尾真实图片作为参考，并在 Gemini 中做图生视频。",
    "",
    "| 文件 | 用途 | 作者 | 许可 | 来源 |",
    "|---|---|---|---|---|",
]
for x in manifest:
    lines.append(f'| {Path(x["path"]).name} | {x["label"]} | {x["author"]} | {x["license"]} | [Commons]({x["source_page"]}) |')
(ROOT / "VIDEO_ATTRIBUTION.md").write_text("\n".join(lines), encoding="utf-8")
print("Done", len(manifest))
