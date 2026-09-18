"""Validate real media files, refresh Commons credits, and package ready-to-use assets.

Run from the repository root. Requires Pillow and ffmpeg/ffprobe.
A missing/corrupt file or unverifiable license is a hard failure, not a green build.
"""
from pathlib import Path
from urllib.parse import urlencode, unquote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from html import escape, unescape
import hashlib
import json
import re
import subprocess
import time
import zipfile

from PIL import Image

ROOT = Path('shanwei-video-assets')
OUT = Path('downloads')
REPO = 'https://github.com/chenyimou208-gif/zerolimit-source'
UA = 'ShanweiAssetVerifier/2.0 (' + REPO + '; educational media archive)'


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def clean(value):
    return unescape(re.sub(r'<[^>]+>', '', str(value))).strip()


def safe_media_path(value):
    p = Path(value)
    if not p.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError('Media path leaves asset directory: ' + str(value))
    if not p.is_file() or p.stat().st_size < 1024:
        raise ValueError('Missing or empty media file: ' + str(value))
    return p


def image_info(path):
    with Image.open(path) as im:
        im.verify()
    with Image.open(path) as im:
        im.load()
        width, height = im.size
        fmt = im.format
    if fmt != 'JPEG' or min(width, height) < 300:
        raise ValueError('Unexpected image format or size: ' + str(path))
    return {'path': path.as_posix(), 'width': width, 'height': height,
            'bytes': path.stat().st_size, 'sha256': sha256(path)}


def query_metadata(titles):
    params = {'action': 'query', 'format': 'json', 'formatversion': '2',
              'redirects': '1', 'prop': 'imageinfo',
              'iiprop': 'url|size|extmetadata', 'titles': '|'.join(titles)}
    url = 'https://commons.wikimedia.org/w/api.php?' + urlencode(params)
    error = None
    for attempt in range(3):
        try:
            req = Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
            with urlopen(req, timeout=90) as r:
                payload = json.load(r)
            if 'error' in payload:
                raise ValueError(str(payload['error']))
            pages = payload['query']['pages']
            found = {p['title'].replace('_', ' '): p['imageinfo'][0]
                     for p in pages if p.get('imageinfo')}
            aliases = payload['query'].get('normalized', []) + payload['query'].get('redirects', [])
            for _ in range(2):
                for item in aliases:
                    source, target = item['from'].replace('_', ' '), item['to'].replace('_', ' ')
                    if target in found:
                        found[source] = found[target]
            return found
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError) as exc:
            error = exc
            if attempt == 2:
                break
            delay = 15 * (attempt + 1)
            if isinstance(exc, HTTPError) and exc.headers.get('Retry-After', '').isdigit():
                delay = max(delay, int(exc.headers['Retry-After']))
            if delay > 180:
                raise RuntimeError('Source requests a longer cooldown; retry later') from exc
            print('Metadata retry:', repr(exc), 'wait:', delay, flush=True)
            time.sleep(delay)
    raise RuntimeError('Cannot verify source licenses') from error


def credit_for(title, metadata):
    info = metadata.get(title.replace('_', ' '))
    if not info:
        raise ValueError('No source metadata for ' + title)
    ext = info.get('extmetadata', {})
    def get(key):
        return clean(ext.get(key, {}).get('value', ''))
    license_url = get('LicenseUrl').replace('http://', 'https://')
    if not re.match(r'^https://creativecommons\.org/licenses/by(?:-sa)?/[1-4]\.0(?:/|$)', license_url):
        raise ValueError('Unverified redistribution license for ' + title + ': ' + license_url)
    artist = get('Artist')
    if not artist:
        raise ValueError('Missing artist credit for ' + title)
    return {'author': artist, 'license': get('LicenseShortName'),
            'license_url': license_url, 'source_date': get('DateTimeOriginal') or get('DateTime'),
            'source_description': get('ImageDescription'),
            'source_original_url': info['url'],
            'source_width': info.get('width'), 'source_height': info.get('height')}


def markdown_table(rows):
    return '\n'.join('| ' + ' | '.join(str(v).replace('|', '\\|').replace('\n', ' ') for v in row) + ' |' for row in rows)


def main():
    images = read_json(ROOT / 'manifest.json')
    videos = read_json(ROOT / 'video_manifest.json')
    expected_ids = {f'{n:02d}' for n in range(1, 15)} - {'12'}
    if len(images) != 13 or {x['id'] for x in images} != expected_ids:
        raise ValueError('Expected 13 distinct selected Shanwei photos; refusing an incomplete or empty package')
    if len(videos) != 3 or len({v['id'] for v in videos}) != 3:
        raise ValueError('Expected 3 separately labelled generic B-roll clips')

    checked_images = []
    for item in images:
        source = image_info(safe_media_path(item['original_path']))
        ref = image_info(safe_media_path(item['gemini_ref_path']))
        if (ref['width'], ref['height']) != (1920, 1080):
            raise ValueError('Reference is not 1920x1080: ' + ref['path'])
        checked_images.append({'id': item['id'], 'working_copy': source, 'reference_16x9': ref})
    if len({x['working_copy']['sha256'] for x in checked_images}) != 13:
        raise ValueError('Duplicate images detected')

    checked_videos = []
    for item in videos:
        p = safe_media_path(item['path'])
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                'format=duration:stream=codec_type,codec_name,width,height', '-of', 'json', str(p)],
                check=True, capture_output=True, text=True, timeout=45)
        data = json.loads(probe.stdout)
        stream = next(s for s in data['streams'] if s.get('codec_type') == 'video')
        duration = float(data['format']['duration'])
        if not 1 <= duration <= 60:
            raise ValueError('Unexpected clip duration: ' + str(p))
        subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(p),
                        '-map', '0:v:0', '-f', 'null', '-'], check=True, timeout=90)
        checked_videos.append({'id': item['id'], 'path': p.as_posix(),
                'duration_seconds': duration, 'width': stream['width'], 'height': stream['height'],
                'codec': stream['codec_name'], 'bytes': p.stat().st_size,
                'sha256': sha256(p), 'is_shanwei_footage': False})

    titles = ['File:' + i['file'] for i in images]
    titles += ['File:' + unquote(v['source_page'].split('File:', 1)[1]).replace('_', ' ') for v in videos]
    metadata = query_metadata(titles)
    for item, check, title in zip(images, checked_images, titles[:len(images)]):
        item.update(credit_for(title, metadata))
        item['working_copy_dimensions'] = [check['working_copy']['width'], check['working_copy']['height']]
        item['reference_dimensions'] = [1920, 1080]
        item['working_copy_sha256'] = check['working_copy']['sha256']
        item['reference_sha256'] = check['reference_16x9']['sha256']
        item['is_full_resolution_original'] = item['working_copy_dimensions'] == [item['source_width'], item['source_height']]
        item['modifications'] = 'Commons working-resolution JPEG; reference copy center-cropped and resized to 1920x1080. No AI reconstruction.'
    for item, check, title in zip(videos, checked_videos, titles[len(images):]):
        item.update(credit_for(title, metadata))
        item.update({k: check[k] for k in ['duration_seconds', 'width', 'height', 'sha256', 'is_shanwei_footage']})
        item['modifications'] = 'Excerpt selected, center-cropped/resized to 1280x720, converted to H.264/AAC MP4 at 30fps.'
    write_json(ROOT / 'manifest.json', images)
    write_json(ROOT / 'video_manifest.json', videos)

    image_rows = [['编号', '画面', '作者', '源文件日期', '许可', '来源'], ['---'] * 6]
    for item in images:
        image_rows.append([item['id'], item['scene'], item['author'], item['source_date'],
            '[' + item['license'] + '](' + item['license_url'] + ')', '[Commons](' + item['source_page'] + ')'])
    (ROOT / 'ATTRIBUTION.md').write_text('# 图片来源与许可（逐项核验）\n\n'
        '13 张不同的汕尾照片，另有 13 张 16:9 裁切参考版，不是 26 个独立画面。\n\n'
        '`originals/` 是沿用的目录名，里面是工作分辨率副本，并不都为网站原始大图。实际像素见 manifest.json。'
        ' 1920×1080 参考图是裁切/缩放版，不是原生高清升级。\n\n' + markdown_table(image_rows) +
        '\n\n修改说明：工作副本来自 Commons；参考图居中裁切并缩放，未进行 AI 重建。'
        '使用、改编或发布时，请保留作者、来源、许可链接与修改说明；BY-SA 素材的改编须遵守相同方式共享要求。'
        '作者及 Wikimedia 不为本作品背书。\n', encoding='utf-8')
    video_rows = [['文件', '用途与地点限制', '作者', '许可', '来源'], ['---'] * 5]
    for item in videos:
        video_rows.append([Path(item['path']).name, item['label'], item['author'],
            '[' + item['license'] + '](' + item['license_url'] + ')', '[Commons](' + item['source_page'] + ')'])
    (ROOT / 'VIDEO_ATTRIBUTION.md').write_text('# 通用视频：非汕尾实拍\n\n'
        '这三段分别取自海南、秦皇岛及竹湾海滩相关源视频。不能标注为红海湾、品清湖或汕尾渔港实拍。'
        '严格的汕尾实景宣传片建议不使用，或仅作明确标识的通用示意/环境声参考。\n\n' + markdown_table(video_rows) +
        '\n\n修改说明：截取片段、居中裁切/缩放到 1280×720、30fps，转换为 H.264/AAC MP4。'
        '署名、许可与相同方式共享要求按各文件所列许可执行。\n', encoding='utf-8')

    guide = '''# 开始使用：这是已经下载好的素材，不是下载器

## 包内实际内容
- 13 张不同的汕尾实景照片，以及各自的 1920×1080 裁切参考版。
- 3 段通用 MP4 视频，单独放在 broll_generic，全部不是汕尾实拍。
- 图片/视频来源、作者、逐项许可、真实像素、文件哈希、校验结果。
- index.html：解压后双击，用浏览器离线预览图片和视频。

## 直接使用
到 gemini_refs_16x9 选一张照片，上传到你已打开的 Gemini 视频界面，再使用 GEMINI_GUIDE.md 中的相应提示词。
本包本身无需安装 Python、无需运行下载命令；视频生成服务仍由你在自己的账号中使用。

## 文件编号
01–04：红海湾；05–07：汕尾渔港；08–11：汕尾道路/城区；13：海岸；14：品清湖夕阳。
12 是早前未下载成功的城市拼图编号，故意不列入本次 13 张精选素材，不用占位图补数。
这些编号是素材编号，不等于前面 11 镜头合成脚本的镜头编号，不能直接按同号代入。

## 真实性与缺口
这一批没有金町湾、二马路、美食特写、红宫红场/非遗的对应实拍素材，不要把一般街景或外地视频冒充这些地点。
部分照片摄于 2014、2018 年，具体日期见 ATTRIBUTION.md，不能写成 2026 年现状。
originals 是历史目录名，其中是工作尺寸副本；1920×1080 版本可能是从较小工作图放大，不代表新增细节。
图生视频新增的运动属于 AI 合成，不是现场实拍，建议在作品说明中标明“实景照片 + AI 动态演绎”。

## 许可
使用前阅读 ATTRIBUTION.md 和 VIDEO_ATTRIBUTION.md。保留来源、作者、许可链接、修改说明；BY-SA 改编还须满足相同方式共享要求。
'''
    (ROOT / 'START_HERE.md').write_text(guide, encoding='utf-8')
    prompts = '''# 图生视频提示词

先上传对应图片，再复制一条。建议逐镜头生成，不假设一次可连续生成整部影片；时长以账号界面为准。

## 01–04 / 红海湾
以所上传的汕尾红海湾实景照片为唯一地点参考。保持原有海岸、礁石、建筑的位置关系。固定机位或极轻微慢推，海浪自然起伏，保持原图光线与天气，不凭空添加船只、建筑或新地标。16:9，写实摄影，无新增文字。运动是基于实景照片的 AI 演绎。

## 05–07 / 汕尾渔港
保留参考照片中船只外观、数量、相对位置和岸边环境。轻微水波，船身克制起伏，稳定慢镜头，不让船穿模、不增加渔船。保留照片年代感，不改造为现代码头。16:9，无新增文字。

## 08–11 / 城区与道路
保持参考照片中建筑、街道和招牌的位置及形状，不改变地点，不新增建筑。固定机位或极轻微推进，光影自然变化，不生成新的可读文字，不把历史画面改称今日城市。16:9，纪录片式画面。

## 13 / 海岸
严格保留参考照片的地形和海岸线。仅让海水轻轻起伏，保持原有天气和色调。固定机位，避免大角度绕拍和虚构画外地标。16:9，真实克制的运动，无新增文字。

## 14 / 品清湖夕阳
保持品清湖参考照片中的建筑轮廓、水面和太阳位置关系。固定机位，水面倒影轻柔变化，缓慢自然的光影过渡。不要把湖景变成礁石海岸，不增加建筑。16:9，无新增文字。

生成后人工检查地形、建筑、船只和文字有无变形；不满意的镜头可直接用原照片做普通推拉。新增动态不能视为真实现场记录。
'''
    (ROOT / 'GEMINI_GUIDE.md').write_text(prompts, encoding='utf-8')

    gallery = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
        '<title>汕尾素材预览</title><style>body{font-family:system-ui,sans-serif;margin:32px;line-height:1.6;background:#f5f6f7;color:#16232c}section{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:20px}article{background:white;padding:16px;border-radius:12px}img,video{width:100%;height:auto}small{display:block}a{color:#075a91}</style>',
        '<h1>汕尾实景照片素材</h1><p>13 张不同照片。参考图为裁切/缩放版；照片年代、原始来源和许可均列于每项下方。素材不是完整成片。</p><section>']
    for item in images:
        rel = Path(item['gemini_ref_path']).relative_to(ROOT).as_posix()
        gallery.append('<article><a href="' + escape(rel, quote=True) + '"><img loading="lazy" src="' + escape(rel, quote=True) + '"></a><h3>' + escape(item['id'] + ' · ' + item['scene']) + '</h3><small>' + escape(item['author'] + ' · ' + item['source_date']) + '</small><small>工作副本：' + escape(str(item['working_copy_dimensions'])) + '；参考版：1920×1080</small><a href="' + escape(item['source_page'], quote=True) + '">来源</a> · <a href="' + escape(item['license_url'], quote=True) + '">' + escape(item['license']) + '</a></article>')
    gallery.append('</section><h2>通用视频：不是汕尾实拍</h2><p>不得冒充红海湾、汕尾渔港等真实地点。严格的本地实景片建议不用。</p><section>')
    for item in videos:
        rel = Path(item['path']).relative_to(ROOT).as_posix()
        gallery.append('<article><video controls preload="metadata" src="' + escape(rel, quote=True) + '"></video><p>' + escape(item['label']) + '</p><small>' + escape(item['author'] + ' · ' + item['license']) + '</small><a href="' + escape(item['source_page'], quote=True) + '">来源</a></article>')
    gallery.append('</section><p>请阅读 START_HERE.md、ATTRIBUTION.md 与 VIDEO_ATTRIBUTION.md。所有参考图均有尺寸变换；视频有截取及转码。</p></html>')
    (ROOT / 'index.html').write_text('\n'.join(gallery), encoding='utf-8')

    media_bytes = sum(x[k]['bytes'] for x in checked_images for k in ['working_copy', 'reference_16x9']) + sum(v['bytes'] for v in checked_videos)
    report = {'status': 'PASS', 'distinct_shanwei_photos': 13, 'jpeg_files': 26,
        'reference_dimensions': [1920, 1080], 'generic_mp4_files': 3,
        'shanwei_live_action_video_files': 0, 'media_bytes': media_bytes,
        'video_total_seconds': round(sum(v['duration_seconds'] for v in checked_videos), 3),
        'checks': ['All JPEG files verified and fully decoded', 'Distinct photo SHA-256 checked',
                   'All MP4 files probed and fully video-decoded', '16 source author/license records refreshed from Commons API'],
        'excluded_selection_ids': ['12'], 'images': checked_images, 'videos': checked_videos}
    write_json(ROOT / 'VALIDATION_REPORT.json', report)
    (ROOT / 'VALIDATION_REPORT.md').write_text('# 实际文件校验结果\n\n'
        '状态：PASS\n\n'
        '汕尾不同照片：13 张。JPEG 文件：26 个（13 个工作副本 + 13 个 1920×1080 参考副本）。\n\n'
        '通用 MP4：3 段。汕尾现场实拍视频：0 段。视频总时长：' + str(report['video_total_seconds']) + ' 秒。\n\n'
        '全部 JPEG 已完整解码；全部 MP4 已经 ffprobe 检查并完整解码画面；16 个来源记录已查询 Commons API 核验作者和许可。'
        '文件尺寸、实际像素与 SHA-256 见 VALIDATION_REPORT.json。\n\n'
        '历史失败的城市拼图编号 12 未列入精选包；未用占位图或外地素材冒充。\n', encoding='utf-8')

    package_files = []
    for directory in ['originals', 'gemini_refs_16x9', 'broll_generic']:
        package_files.extend(sorted((ROOT / directory).glob('*')))
    package_files.extend(ROOT / n for n in ['START_HERE.md', 'GEMINI_GUIDE.md', 'ATTRIBUTION.md',
        'VIDEO_ATTRIBUTION.md', 'manifest.json', 'video_manifest.json', 'VALIDATION_REPORT.json',
        'VALIDATION_REPORT.md', 'index.html'])
    package_files = [p for p in package_files if p.is_file()]
    checksums = ROOT / 'SHA256SUMS.txt'
    checksums.write_text(''.join(sha256(p) + '  ' + p.relative_to(ROOT).as_posix() + '\n' for p in package_files), encoding='utf-8')
    package_files.append(checksums)
    OUT.mkdir(exist_ok=True)
    archive = OUT / 'shanwei_materials_ready.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in package_files:
            z.write(p, p.as_posix())
    with zipfile.ZipFile(archive) as z:
        bad = z.testzip()
        if bad:
            raise ValueError('ZIP integrity failure: ' + bad)
        assert len([n for n in z.namelist() if n.endswith('.jpg')]) == 26
        assert len([n for n in z.namelist() if n.endswith('.mp4')]) == 3
    package_info = {'path': archive.as_posix(), 'bytes': archive.stat().st_size,
        'sha256': sha256(archive), 'zip_entries': len(package_files), 'zip_crc_test': 'PASS',
        'distinct_photos': 13, 'jpeg_files': 26, 'mp4_files': 3,
        'download_url': REPO + '/raw/refs/heads/main/' + archive.as_posix()}
    write_json(OUT / 'package_info.json', package_info)
    (OUT / 'SHA256SUMS.txt').write_text(package_info['sha256'] + '  ' + archive.name + '\n', encoding='utf-8')
    Path('README.md').write_text('# 汕尾宣传片素材工作区\n\n'
        '**图片和视频文件已入库，不是只有下载脚本。**\n\n'
        '[直接下载已校验素材 ZIP](' + package_info['download_url'] + ')\n\n'
        '[素材目录](shanwei-video-assets/) · [开始使用](shanwei-video-assets/START_HERE.md) · '
        '[校验报告](shanwei-video-assets/VALIDATION_REPORT.md)\n\n'
        '13 张不同的汕尾实景照片 + 13 张对应的 1920×1080 裁切参考图 + 3 段通用 MP4。'
        'ZIP 约 ' + str(round(package_info['bytes'] / 1024 / 1024, 2)) + ' MiB。解压后双击 index.html 可离线预览。\n\n'
        '**3 段视频不是汕尾实拍。** 本批没有金町湾、二马路、美食和红宫红场/非遗的对应实拍素材。'
        '部分照片为 2014/2018 年影像，不应描述为 2026 年现状。工作图并非全为原始大图，参考版放大不会增加真实细节。\n\n'
        '版权与修改说明见 [图片署名](shanwei-video-assets/ATTRIBUTION.md) 和 [视频署名](shanwei-video-assets/VIDEO_ATTRIBUTION.md)。'
        '实际尺寸、SHA-256 与 ZIP 校验结果见 [下载信息](downloads/package_info.json)。\n', encoding='utf-8')
    print(json.dumps(package_info, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
