#!/usr/bin/env python3
"""Stable channel full analysis for a YouTube channel."""

from __future__ import annotations

import datetime as dt
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
ACCOUNT = HERE / "youtube_account.json"
REPORT = HERE / "channel_full_analysis_report.md"


def configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def load_account() -> dict:
    if not ACCOUNT.exists():
        print("[ERROR] youtube_account.json 파일이 없습니다.")
        sys.exit(1)
    try:
        return json.loads(ACCOUNT.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        print(f"[ERROR] youtube_account.json 읽기 실패: {exc}")
        sys.exit(1)


def normalize_channel_refs(account: dict) -> tuple[str, str]:
    handle = str(account.get("MY_CHANNEL_HANDLE", "")).strip()
    channel_id = str(account.get("MY_CHANNEL_ID", "")).strip()
    if channel_id.startswith("@") and not handle:
        handle = channel_id
        channel_id = ""
    if channel_id.startswith("@"):
        channel_id = ""
    return handle, channel_id


def resolve_channel_id(youtube, handle: str, channel_id: str) -> str | None:
    if channel_id:
        return channel_id
    if not handle:
        return None
    query = handle.lstrip("@")
    try:
        response = youtube.search().list(part="snippet", q=query, type="channel", maxResults=1).execute()
        items = response.get("items", [])
        if items:
            return items[0]["snippet"]["channelId"]
    except Exception as exc:
        print(f"[WARN] 채널 ID 조회 실패: {exc}")
    return None


def parse_iso_duration(value: str) -> int:
    import re

    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not match:
        return 0
    hours, minutes, seconds = match.groups()
    return int(hours or 0) * 3600 + int(minutes or 0) * 60 + int(seconds or 0)


def fmt_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


def fetch_recent_upload_ids(youtube, uploads_playlist: str, cutoff: dt.datetime, limit: int = 30) -> list[str]:
    video_ids: list[str] = []
    next_token = None
    while len(video_ids) < limit:
        kwargs = {
            "part": "snippet,contentDetails",
            "playlistId": uploads_playlist,
            "maxResults": 50,
        }
        if next_token:
            kwargs["pageToken"] = next_token
        result = youtube.playlistItems().list(**kwargs).execute()
        items = result.get("items", [])
        if not items:
            break
        stop = False
        for item in items:
            published_at = item["snippet"]["publishedAt"]
            published_dt = dt.datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            if published_dt < cutoff:
                stop = True
                break
            video_ids.append(item["contentDetails"]["videoId"])
            if len(video_ids) >= limit:
                stop = True
                break
        if stop:
            break
        next_token = result.get("nextPageToken")
        if not next_token:
            break
    return video_ids


def build_report(channel_title: str, subs: int, total_views: int, video_count: int, created_date: str, videos: list[dict]) -> str:
    views = [video["views"] for video in videos]
    median_views = int(statistics.median(views)) if views else 0
    mean_views = int(statistics.mean(views)) if views else 0
    avg_duration = int(sum(video["duration_sec"] for video in videos) / len(videos)) if videos else 0
    avg_engagement = (
        sum(video["engagement_rate"] for video in videos) / len(videos) * 100 if videos else 0
    )

    weekday_counts = Counter(video["published_at"].strftime("%A") for video in videos)
    weekday_kr = {
        "Monday": "월",
        "Tuesday": "화",
        "Wednesday": "수",
        "Thursday": "목",
        "Friday": "금",
        "Saturday": "토",
        "Sunday": "일",
    }
    top_day = weekday_counts.most_common(1)
    top_day_text = f"{weekday_kr.get(top_day[0][0], top_day[0][0])}요일 ({top_day[0][1]}개)" if top_day else "데이터 부족"

    hot = [video for video in videos if video["views"] >= max(1, median_views) * 1.5]
    cold = [video for video in videos if video["views"] < max(1, median_views) * 0.5]

    lines = [
        f"# 채널 전체 분석 ({time.strftime('%Y-%m-%d %H:%M')})",
        "",
        "## 1. 채널 개요",
        f"- 채널명: **{channel_title}**",
        f"- 구독자: **{subs:,}명**",
        f"- 총 조회수: **{total_views:,}회**",
        f"- 전체 업로드 수: **{video_count}개**",
        f"- 채널 개설일: **{created_date}**",
        "",
        "## 2. 최근 30일 업로드 패턴",
        f"- 분석 영상 수: **{len(videos)}개**",
        f"- 주 업로드 요일: **{top_day_text}**",
        f"- 평균 영상 길이: **{fmt_duration(avg_duration)}**",
        "",
        "## 3. 성과 통계",
        f"- 조회수 중간값: **{median_views:,}회**",
        f"- 조회수 평균: **{mean_views:,}회**",
        f"- 평균 참여율(좋아요+댓글/조회수): **{avg_engagement:.2f}%**",
        "",
        f"## 4. 상위 성과 영상 ({len(hot)}개)",
    ]

    if hot:
        for video in hot[:5]:
            lines.append(f"- {video['views']:,}회 | {video['title']}")
    else:
        lines.append("- 뚜렷한 상위 성과 영상 없음")

    lines += [
        "",
        f"## 5. 저성과 영상 ({len(cold)}개)",
    ]

    if cold:
        for video in cold[:5]:
            lines.append(f"- {video['views']:,}회 | {video['title']}")
    else:
        lines.append("- 뚜렷한 저성과 영상 없음")

    actions: list[str] = []
    if hot:
        actions.append(f"상위 영상의 제목/주제 패턴을 확장하세요. 기준 영상: {hot[0]['title']}")
    if cold:
        actions.append("저성과 영상은 썸네일/제목을 A/B 테스트해보세요.")
    if avg_engagement < 2.0:
        actions.append("영상 후반부에 명확한 CTA를 추가해 참여율을 끌어올리세요.")
    if len(videos) < 4:
        actions.append("최근 업로드 수가 적어 최소 주 1회 업로드를 권장합니다.")
    if not actions:
        actions.append("현재 패턴을 유지하되, 상위 주제의 파생 아이디어를 더 수집하세요.")

    lines += [
        "",
        "## 6. 다음 액션 추천",
    ]
    lines.extend(f"- {action}" for action in actions)
    return "\n".join(lines)


def main() -> None:
    configure_stdio()
    account = load_account()

    api_key = str(account.get("YOUTUBE_API_KEY", "")).strip()
    if not api_key:
        print("[ERROR] YOUTUBE_API_KEY가 비어 있습니다.")
        sys.exit(1)

    handle, channel_id = normalize_channel_refs(account)
    if not (handle or channel_id):
        print("[ERROR] MY_CHANNEL_HANDLE 또는 MY_CHANNEL_ID가 필요합니다.")
        sys.exit(1)

    try:
        from googleapiclient.discovery import build
    except ImportError:
        print("[ERROR] google-api-python-client가 설치되지 않았습니다.")
        print("        설치: pip install google-api-python-client requests")
        sys.exit(1)

    youtube = build("youtube", "v3", developerKey=api_key)
    resolved_id = resolve_channel_id(youtube, handle, channel_id)
    if not resolved_id:
        print("[ERROR] 채널 ID를 찾지 못했습니다. 핸들 또는 채널 ID를 다시 확인해 주세요.")
        sys.exit(1)

    print(f"[Channel Full Analysis] 분석 시작: {handle or resolved_id}")

    channel_resp = youtube.channels().list(
        part="snippet,statistics,contentDetails",
        id=resolved_id,
    ).execute()
    items = channel_resp.get("items", [])
    if not items:
        print("[ERROR] 채널 데이터를 가져오지 못했습니다.")
        sys.exit(1)

    channel = items[0]
    snippet = channel.get("snippet", {})
    stats = channel.get("statistics", {})
    content = channel.get("contentDetails", {})

    uploads = content.get("relatedPlaylists", {}).get("uploads")
    if not uploads:
        print("[ERROR] 업로드 플레이리스트를 찾지 못했습니다.")
        sys.exit(1)

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
    recent_video_ids = fetch_recent_upload_ids(youtube, uploads, cutoff)
    if not recent_video_ids:
        print("[WARN] 최근 30일 내 업로드된 영상이 없습니다.")
        sys.exit(0)

    videos: list[dict] = []
    for index in range(0, len(recent_video_ids), 50):
        chunk = recent_video_ids[index:index + 50]
        response = youtube.videos().list(
            part="snippet,statistics,contentDetails",
            id=",".join(chunk),
        ).execute()
        for video in response.get("items", []):
            video_stats = video.get("statistics", {})
            video_snippet = video.get("snippet", {})
            video_content = video.get("contentDetails", {})
            views = int(video_stats.get("viewCount", 0))
            likes = int(video_stats.get("likeCount", 0))
            comments = int(video_stats.get("commentCount", 0))
            published_at = dt.datetime.fromisoformat(video_snippet.get("publishedAt", "").replace("Z", "+00:00"))
            videos.append(
                {
                    "title": video_snippet.get("title", "").strip(),
                    "views": views,
                    "likes": likes,
                    "comments": comments,
                    "duration_sec": parse_iso_duration(video_content.get("duration", "")),
                    "published_at": published_at,
                    "engagement_rate": (likes + comments) / views if views > 0 else 0,
                }
            )

    videos.sort(key=lambda item: item["views"], reverse=True)

    report = build_report(
        channel_title=snippet.get("title", "(이름 없음)"),
        subs=int(stats.get("subscriberCount", 0)),
        total_views=int(stats.get("viewCount", 0)),
        video_count=int(stats.get("videoCount", 0)),
        created_date=snippet.get("publishedAt", "")[:10],
        videos=videos,
    )

    print(report)
    REPORT.write_text(report + "\n", encoding="utf-8")
    print(f"[OK] 보고서 저장 완료: {REPORT}")


if __name__ == "__main__":
    main()
