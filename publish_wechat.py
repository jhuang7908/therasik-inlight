#!/usr/bin/env python3
"""Upload one weekly digest to the WeChat official-account draft box.

Does not mass-send and does not call the publish API.

    python publish_wechat.py
    python publish_wechat.py --week 2026-10-07
"""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import sys
import traceback
import uuid
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API = "https://api.weixin.qq.com"


def setup_log() -> Path:
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    path = log_dir / f"wechat-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
    )
    return path


def require_env() -> tuple[str, str]:
    appid = os.environ.get("WECHAT_APPID", "")
    secret = os.environ.get("WECHAT_APPSECRET", "")
    if not appid or not secret:
        logging.error("缺少环境变量：WECHAT_APPID、WECHAT_APPSECRET")
        raise SystemExit(1)
    return appid, secret


def api_json(url: str, payload: dict | None = None, timeout: int = 60) -> dict:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if body.get("errcode"):
        logging.error("微信接口错误 %s", body)
        raise SystemExit(4)
    return body


def upload_file(url: str, path: Path, field: str = "media") -> dict:
    boundary = uuid.uuid4().hex
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{path.name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        url,
        data=head + path.read_bytes() + tail,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if body.get("errcode"):
        logging.error("上传失败 %s %s", path, body)
        raise SystemExit(4)
    return body


def token(appid: str, secret: str) -> str:
    query = urllib.parse.urlencode({
        "grant_type": "client_credential",
        "appid": appid,
        "secret": secret,
    })
    body = api_json(f"{API}/cgi-bin/token?{query}")
    access = body.get("access_token")
    if not access:
        logging.error("没有拿到 access_token：%s", body)
        raise SystemExit(4)
    return access


def latest_week(explicit: str | None) -> Path:
    if explicit:
        path = ROOT / "content" / "weekly" / explicit
    else:
        root = ROOT / "content" / "weekly"
        weeks = sorted(p for p in root.glob("*") if p.is_dir()) if root.exists() else []
        path = weeks[-1] if weeks else None
    if path is None or not path.is_dir():
        logging.error("找不到 content/weekly 下的一周目录")
        raise SystemExit(2)
    html = path / "wechat" / "article.html"
    cover = path / "wechat" / "cover.png"
    if not html.exists() or not cover.exists():
        logging.error("缺少 %s 或 %s", html, cover)
        raise SystemExit(2)
    return path


def rewrite_images(html: str, week: Path, access: str) -> str:
    images = week / "images"
    if not images.exists():
        return html
    for image in sorted(images.iterdir()):
        if image.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        marker = image.name
        if marker not in html and str(image.as_posix()) not in html:
            continue
        uploaded = upload_file(f"{API}/cgi-bin/media/uploadimg?access_token={access}", image)
        url = uploaded.get("url")
        if not url:
            logging.error("正文图没有返回 url：%s", image)
            raise SystemExit(4)
        rel = f"{week.relative_to(ROOT).as_posix()}/images/{image.name}"
        html = html.replace(rel, url)
    return html


def main() -> None:
    parser = argparse.ArgumentParser(description="把一周内容放进公众号草稿箱")
    parser.add_argument("--week", help="目录名，例如 2026-10-07。缺省用 content/weekly 里最新的一周")
    args = parser.parse_args()
    log_path = setup_log()
    logging.info("日志 %s", log_path)
    try:
        appid, secret = require_env()
        week = latest_week(args.week)
        access = token(appid, secret)
        cover = upload_file(
            f"{API}/cgi-bin/material/add_material?access_token={access}&type=image",
            week / "wechat" / "cover.png",
        )
        media_id = cover.get("media_id")
        if not media_id:
            logging.error("封面没有 media_id：%s", cover)
            raise SystemExit(4)
        html = (week / "wechat" / "article.html").read_text(encoding="utf-8")
        html = rewrite_images(html, week, access)
        articles = json.loads((week / "articles.json").read_text(encoding="utf-8"))
        deals = json.loads((week / "deals.json").read_text(encoding="utf-8")) if (week / "deals.json").exists() else []
        
        # Build a descriptive title from top article topics
        topics = []
        for art in articles[:3]:
            if art.get("t"):
                # Extract key topic from title
                t = art["t"]
                if "CAR-T" in t:
                    topics.append("CAR-T")
                elif "类器官" in t:
                    topics.append("类器官")
                elif "mRNA" in t or "LNP" in t:
                    topics.append("mRNA")
                elif "抗体" in t or "双抗" in t:
                    topics.append("抗体")
                elif "AI" in t or "机器学习" in t:
                    topics.append("AI")
        
        # Format date nicely
        week_parts = week.name.split("-")
        if len(week_parts) == 3:
            date_str = f"{week_parts[0]}年{int(week_parts[1])}月{int(week_parts[2])}日"
        else:
            date_str = week.name
        
        # Build title with topics
        if topics:
            unique_topics = list(dict.fromkeys(topics))[:3]  # dedupe, keep order
            title = f"前沿追踪 | {date_str}：{'、'.join(unique_topics)}等"
        else:
            title = f"前沿追踪 | {date_str}周报"
        
        digest = ""
        if articles:
            digest = (articles[0].get("lead") or articles[0].get("t") or "")[:116] + "…"
        
        # content_source_url for "阅读原文" link
        content_url = "https://inlight.therasik.com/#home"
        
        draft = api_json(
            f"{API}/cgi-bin/draft/add?access_token={access}",
            {"articles": [{
                "title": title[:64],
                "author": "前沿追踪",
                "digest": digest,
                "content": html,
                "thumb_media_id": media_id,
                "content_source_url": content_url,
                "need_open_comment": 1,
                "only_fans_can_comment": 0,
            }]},
        )
        logging.info("已放入草稿箱，media_id=%s。没有群发。", draft.get("media_id"))
    except SystemExit:
        raise
    except Exception:
        logging.error("未捕获的错误\n%s", traceback.format_exc())
        raise SystemExit(4)


if __name__ == "__main__":
    main()
