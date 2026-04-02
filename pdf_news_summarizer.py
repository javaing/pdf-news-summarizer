#!/usr/bin/env python3
"""
PDF 報刊摘要工具
從 Google Drive 資料夾下載所有 PDF，逐一生成繁體中文摘要（500字以內，5-7個重點）

使用前準備：
  1. 設定 ANTHROPIC_API_KEY 環境變數
  2. 設定 GOOGLE_API_KEY 環境變數（免費取得：https://console.cloud.google.com/apis/credentials）
     → 建立專案 → 建立憑證 → API 金鑰 → 啟用「Google Drive API」
     （若不設定，只能下載前50個檔案）
"""

import os
import sys
import io
import time
import base64
import json
from pathlib import Path
from dotenv import load_dotenv
import anthropic

# 讓終端機支援 UTF-8 輸出（Windows cp950 問題）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 載入同目錄的 .env 檔
load_dotenv(Path(__file__).parent / ".env", override=True)

FOLDER_ID = "1jhxGiWPxRzotiEQVyBuGHn7_R_V40-la"
DOWNLOAD_DIR = Path("gdrive_pdfs")
SUMMARIES_DIR = Path("summaries")
PROGRESS_FILE = Path("progress.json")

SUMMARY_PROMPT = """請根據這份報刊的封面和內容，用繁體中文生成摘要。

要求：
- 總字數不超過500字
- 列出5個重點（最少5個，最多7個）
- 每個重點簡潔扼要，一句話說明
- 先寫整體概述（一句話），再列重點

輸出格式：
【概述】（一句話描述本期主題或最重要的新聞）

【重點】
1. ...
2. ...
3. ...
4. ...
5. ..."""


def load_progress():
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_progress(done: set):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(list(done), f, ensure_ascii=False)


def list_files_via_api(api_key: str) -> list[dict]:
    """使用 Google Drive API v3 列出所有檔案（支援分頁，無50筆限制）"""
    from googleapiclient.discovery import build

    service = build("drive", "v3", developerKey=api_key)
    files = []
    page_token = None

    import threading

    print("使用 Google Drive API 列出所有檔案...", end="", flush=True)

    stop_spinner = threading.Event()

    def spinner():
        chars = [".", "..", "...", "   "]
        i = 0
        while not stop_spinner.is_set():
            print(f"\r使用 Google Drive API 列出所有檔案{chars[i % 4]}", end="", flush=True)
            i += 1
            stop_spinner.wait(0.5)

    t = threading.Thread(target=spinner, daemon=True)
    t.start()

    while True:
        params = {
            "q": f"'{FOLDER_ID}' in parents and trashed=false",
            "fields": "nextPageToken, files(id, name, mimeType)",
            "pageSize": 100,
        }
        if page_token:
            params["pageToken"] = page_token

        result = service.files().list(**params).execute()
        batch = result.get("files", [])
        files.extend(batch)

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    stop_spinner.set()
    t.join()
    print(f"\r使用 Google Drive API 列出所有檔案... 完成          ")

    print(f"資料夾共 {len(files)} 個項目")
    non_pdfs = [f for f in files if not f["name"].lower().endswith(".pdf")]
    if non_pdfs:
        print(f"  非 PDF 檔案 ({len(non_pdfs)} 個)：")
        for f in non_pdfs:
            print(f"    {f['name']} [{f['mimeType']}]")
    pdfs = [f for f in files if f["name"].lower().endswith(".pdf")]
    print(f"共找到 {len(pdfs)} 個 PDF 檔案\n")
    return pdfs


def download_files_via_api(api_key: str, files: list[dict]) -> list[Path]:
    """使用 Google Drive API 直接下載檔案"""
    import requests

    DOWNLOAD_DIR.mkdir(exist_ok=True)
    downloaded = []

    for i, f in enumerate(files, 1):
        local_path = DOWNLOAD_DIR / f["name"]
        if local_path.exists():
            print(f"  [{i}/{len(files)}] 已存在，略過：{f['name']}")
            downloaded.append(local_path)
            continue

        print(f"  [{i}/{len(files)}] 下載：{f['name']}")
        url = f"https://www.googleapis.com/drive/v3/files/{f['id']}?alt=media&key={api_key}"
        try:
            r = requests.get(url, stream=True, timeout=120)
            r.raise_for_status()
            with open(local_path, "wb") as fp:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    fp.write(chunk)
            downloaded.append(local_path)
        except Exception as e:
            print(f"    ⚠ 下載失敗：{e}")

    return downloaded


def download_via_gdown_fallback() -> list[Path]:
    """備用方案：用 gdown download_folder（最多50個）"""
    import gdown

    DOWNLOAD_DIR.mkdir(exist_ok=True)
    url = f"https://drive.google.com/drive/folders/{FOLDER_ID}"
    print("⚠ 未設定 GOOGLE_API_KEY，改用 gdown（只能取得前50個檔案）")
    print(f"下載中：{url}\n")
    try:
        gdown.download_folder(
            url, output=str(DOWNLOAD_DIR), quiet=False,
            use_cookies=False, remaining_ok=True, resume=True
        )
    except Exception as e:
        print(f"下載錯誤：{e}，繼續處理已下載檔案...")

    pdfs = sorted(DOWNLOAD_DIR.rglob("*.pdf"))
    print(f"\n找到 {len(pdfs)} 個 PDF 檔案\n")
    return pdfs


def download_all_pdfs() -> list[Path]:
    api_key = os.environ.get("GOOGLE_API_KEY")
    if api_key:
        try:
            files = list_files_via_api(api_key)
            return sorted(download_files_via_api(api_key, files))
        except Exception as e:
            print(f"⚠ Google Drive API 失敗（{e}），改用 gdown...\n")

    return download_via_gdown_fallback()


MAX_PDF_BYTES = 20 * 1024 * 1024  # 20MB 上限


def trim_pdf(raw: bytes) -> bytes:
    """若 PDF 超過大小限制，逐步減少頁數直到符合限制"""
    from pypdf import PdfReader, PdfWriter
    import io

    if len(raw) <= MAX_PDF_BYTES:
        return raw

    reader = PdfReader(io.BytesIO(raw))
    total = len(reader.pages)
    # 從全部頁數開始，每次減少 2 頁
    for n in range(total, 0, -2):
        writer = PdfWriter()
        for i in range(n):
            writer.add_page(reader.pages[i])
        buf = io.BytesIO()
        writer.write(buf)
        trimmed = buf.getvalue()
        if len(trimmed) <= MAX_PDF_BYTES:
            print(f"  (PDF 過大，取前 {n}/{total} 頁)")
            return trimmed

    # 保底：只取第 1 頁
    writer = PdfWriter()
    writer.add_page(reader.pages[0])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def summarize_pdf(client: anthropic.Anthropic, pdf_path: Path) -> str:
    with open(pdf_path, "rb") as f:
        data = f.read()
    data = trim_pdf(data)
    pdf_data = base64.standard_b64encode(data).decode("utf-8")
    print(f"  上傳中... ({len(data)/1024/1024:.1f} MB)")

    full_text = ""
    print("  Claude 辨識中...", flush=True)
    with client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_data,
                        },
                    },
                    {"type": "text", "text": SUMMARY_PROMPT},
                ],
            }
        ],
    ) as stream:
        first = True
        for text in stream.text_stream:
            if first:
                print("  摘要輸出：\n")
                first = False
            print(text, end="", flush=True)
            full_text += text

    print()
    return full_text


def save_summary(pdf_path: Path, summary: str):
    out = pdf_path.with_suffix(".txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"{pdf_path.name}\n{'='*40}\n{summary}\n")
    return out


def main():
    test_mode = "--test" in sys.argv

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("錯誤：請設定環境變數 ANTHROPIC_API_KEY")
        sys.exit(1)

    pdfs = download_all_pdfs()
    if not pdfs:
        print("未找到任何 PDF 檔案，結束。")
        return

    client = anthropic.Anthropic()
    done = load_progress()

    # TXT 不存在、或內容含「處理失敗」，都需要重做
    def needs_summary(p: Path) -> bool:
        txt = p.with_suffix(".txt")
        if not txt.exists():
            return True
        return "處理失敗" in txt.read_text(encoding="utf-8", errors="ignore")

    remaining = [p for p in pdfs if needs_summary(p)]

    if test_mode:
        remaining = remaining[:1]
        print("【測試模式】只處理第一個檔案\n")

    print(f"總共 {len(pdfs)} 個檔案，已完成 {len(done)} 個，剩餘 {len(remaining)} 個\n")
    print("=" * 60)

    for i, pdf_path in enumerate(remaining, 1):
        print(f"\n[{i}/{len(remaining)}] 正在摘要：{pdf_path.name}")
        print("-" * 40)

        try:
            summary = summarize_pdf(client, pdf_path)
            out = save_summary(pdf_path, summary)
            done.add(pdf_path.name)
            save_progress(done)
            print(f"✓ 已儲存至 {out}")

            if i < len(remaining):
                time.sleep(15)

        except anthropic.RateLimitError:
            print("\n速率限制，等待 90 秒後繼續...")
            time.sleep(90)
            try:
                summary = summarize_pdf(client, pdf_path)
                out = save_summary(pdf_path, summary)
                done.add(pdf_path.name)
                save_progress(done)
            except Exception as e2:
                print(f"重試失敗：{e2}")
                save_summary(pdf_path, f"處理失敗：{e2}")

        except Exception as e:
            print(f"\n錯誤：{e}")
            save_summary(pdf_path, f"*處理失敗：{e}*")

    print("\n" + "=" * 60)
    print(f"完成！摘要存放於：{SUMMARIES_DIR.absolute()}")
    print(f"共處理 {len(done)} 個檔案")


if __name__ == "__main__":
    main()
