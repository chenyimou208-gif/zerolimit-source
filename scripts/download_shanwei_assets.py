from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import json
import shutil
import time
import random
import hashlib

from PIL import Image, ImageOps

ROOT = Path("shanwei-video-assets")
ORIG = ROOT / "originals"
REFS = ROOT / "gemini_refs_16x9"
ORIG.mkdir(parents=True, exist_ok=True)
REFS.mkdir(parents=True, exist_ok=True)

ASSETS = [
    {"id":"01","out":"01_honghaiwan_2022.jpg","file":"Hong Hai Wan, Shanwei, Aug 2022.jpg","scene":"汕尾红海湾，沙滩与海面","author":"HMGiovanniV","license":"CC BY-SA 4.0"},
    {"id":"02","out":"02_honghaiwan_2014_a.jpg","file":"Shanwei Zhelang Honghaiwan 2014.01.18 14-28-16.jpg","scene":"汕尾遮浪红海湾","author":"Zhangzhugang","license":"CC BY-SA 4.0"},
    {"id":"03","out":"03_honghaiwan_2014_b.jpg","file":"Shanwei Zhelang Honghaiwan 2014.01.18 14-58-32.jpg","scene":"汕尾遮浪红海湾","author":"Zhangzhugang","license":"CC BY-SA 4.0"},
    {"id":"04","out":"04_honghaiwan_2014_c.jpg","file":"Shanwei Zhelang Honghaiwan 2014.01.18 15-03-43.jpg","scene":"汕尾遮浪红海湾","author":"Zhangzhugang","license":"CC BY-SA 4.0"},
    {"id":"05","out":"05_shanwei_fishing_port_a.jpg","file":"Shanwei Yugang 2014.01.18 11-18-05.jpg","scene":"汕尾渔港","author":"Zhangzhugang","license":"CC BY-SA 4.0"},
    {"id":"06","out":"06_shanwei_fishing_port_b.jpg","file":"Shanwei Yugang 2014.01.18 11-47-43.jpg","scene":"汕尾渔港","author":"Zhangzhugang","license":"CC BY-SA 4.0"},
    {"id":"07","out":"07_shanwei_fishing_port_c.jpg","file":"Shanwei Yugang 2014.01.18 11-54-45.jpg","scene":"汕尾渔港","author":"Zhangzhugang","license":"CC BY-SA 4.0"},
    {"id":"08","out":"08_shanwei_avenue.jpg","file":"Shanwei 2018 11 part12.jpg","scene":"汕尾大道","author":"Qwer132477","license":"CC BY-SA 4.0"},
    {"id":"09","out":"09_shanwei_city_panorama.jpg","file":"Shanwei 2018 11 part26.jpg","scene":"俯瞰汕尾城区全景","author":"Qwer132477","license":"CC BY-SA 4.0"},
    {"id":"10","out":"10_shanwei_road_intersection.jpg","file":"Shanwei 2018 11 part31.jpg","scene":"汕尾通航路与汕尾大道交界","author":"Qwer132477","license":"CC BY-SA 4.0"},
    {"id":"11","out":"11_shanwei_city_building.jpg","file":"Shanwei 2018 11 part11.jpg","scene":"汕尾城区建筑与城市环境","author":"Qwer132477","license":"CC BY-SA 4.0"},
    {"id":"12","out":"12_shanwei_city_montage.jpg","file":"Shan Wei 2018.jpg","scene":"汕尾市代表性建筑拼图","author":"Qwer132477","license":"CC BY-SA 4.0"},
    {"id":"13","out":"13_shanwei_coast.jpg","file":"汕尾沿海海景一瞥.jpg","scene":"汕尾海岸一景","author":"TrutH SuiTeR","license":"CC BY-SA 4.0"},
    {"id":"14","out":"14_pinqing_lake_sunset.jpg","file":"汕尾风光1.jpg","scene":"汕尾品清湖夕阳","author":"GEO-GZHU","license":"CC BY-SA 4.0"},
]

UA = "ShanweiVideoAssetCollector/1.0 (educational media project; GitHub Actions)"

def source_page(filename: str) -> str:
    return "https://commons.wikimedia.org/wiki/File:" + quote(filename.replace(" ", "_"), safe="_(),.-")

def direct_upload_url(filename: str) -> str:
    normalized = filename.replace(" ", "_")
    h = hashlib.md5(normalized.encode("utf-8")).hexdigest()
    encoded = quote(normalized, safe="_(),.-")
    # Wikimedia explicitly recommends thumbnail URLs for automated consumers.
    return "https://upload.wikimedia.org/wikipedia/commons/thumb/" + h[0] + "/" + h[:2] + "/" + encoded + "/1280px-" + encoded

def download(url: str, target: Path):
    last = None
    for attempt in range(1, 3):
        try:
            req = Request(url, headers={
                "User-Agent": UA,
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            })
            with urlopen(req, timeout=180) as r, target.open("wb") as f:
                shutil.copyfileobj(r, f)
            return
        except (HTTPError, URLError) as e:
            last = e
            wait = 2 * attempt + random.uniform(0, 1)
            print(f"  attempt {attempt} failed: {e}; waiting {wait:.1f}s")
            if target.exists():
                target.unlink()
            time.sleep(wait)
    raise last

def make_ref(src: Path, dst: Path):
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        fitted = ImageOps.fit(im, (1920, 1080), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        fitted.save(dst, "JPEG", quality=90, optimize=True)

manifest = []
failures = []
for idx, a in enumerate(ASSETS):
    target = ORIG / a["out"]
    print(f'Downloading {a["id"]}: {a["file"]}')
    try:
        if not target.exists():
            download(direct_upload_url(a["file"]), target)
        ref = REFS / a["out"]
        make_ref(target, ref)
        item = dict(a)
        item["source_page"] = source_page(a["file"])
        item["download_url"] = direct_upload_url(a["file"])
        item["original_path"] = str(target)
        item["gemini_ref_path"] = str(ref)
        manifest.append(item)
    except Exception as e:
        print(f'  FAILED {a["id"]}: {e}')
        failures.append({"id": a["id"], "file": a["file"], "error": repr(e)})
    time.sleep(1.2 + random.uniform(0, 0.8))

(ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
(ROOT / "failures.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")

lines = [
    "# 素材版权与署名 / Attribution",
    "",
    "本目录中的素材来自 Wikimedia Commons。每个素材均保留来源页、作者和许可信息。",
    "本仓库统一按列出的 **CC BY-SA 4.0** 许可选项使用这些素材；发布成片时请保留合理署名，并遵守相同方式共享等条款。",
    "",
    "| 编号 | 场景 | 作者 | 许可 | 来源 |",
    "|---|---|---|---|---|",
]
for a in manifest:
    lines.append(f'| {a["id"]} | {a["scene"]} | {a["author"]} | {a["license"]} | [Wikimedia Commons]({a["source_page"]}) |')
lines += [
    "",
    "许可链接：https://creativecommons.org/licenses/by-sa/4.0/",
    "",
    "说明：`gemini_refs_16x9/` 中的图片只是将原图中心裁切/缩放为 1920×1080，方便作为 Gemini 图生视频参考图；并未声称作者或 Wikimedia 对本项目背书。",
]
(ROOT / "ATTRIBUTION.md").write_text("\n".join(lines), encoding="utf-8")

guide = """# Gemini 使用建议

优先把 `gemini_refs_16x9/` 下的图片上传到 Gemini 的“视频”功能作为参考图。

推荐镜头：
- 01–04：红海湾/海岸
- 05–07：渔港与渔船
- 08–11：城市道路与城市新貌
- 09：汕尾城区全景
- 13：沿海风景
- 14：品清湖夕阳

提示词通用模板：

> 严格以参考照片中的真实汕尾地点为视觉依据，不改变主要地形、海岸线和建筑关系。让画面产生自然、克制的真实运动：海浪轻微起伏、云层缓慢移动、树叶和旗帜受海风轻摆，摄影机做稳定缓慢推进或横移。纪录片摄影，真实自然，不要生成文字、logo、水印或虚构地标，横向16:9。

注意：这些参考图片来自 Wikimedia Commons，使用前请阅读 ATTRIBUTION.md 并按要求署名。
"""
(ROOT / "GEMINI_GUIDE.md").write_text(guide, encoding="utf-8")

print(f"Done: {len(manifest)} downloaded, {len(failures)} failed")
if failures:
    print("Some assets failed, but successful assets will still be committed.")
