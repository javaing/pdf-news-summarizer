# PDF News Summarizer

從 Google Drive 資料夾批次下載報刊 PDF，使用 Claude AI 自動生成繁體中文摘要。

## 功能

- 自動從 Google Drive 共享資料夾下載所有 PDF
- 每個 PDF 生成獨立摘要檔（`.txt`）
- 500字以內，5～7個重點
- 斷點續跑：中斷後重新執行會跳過已完成的
- 自動重試失敗的檔案

## 安裝

```bash
pip install anthropic google-api-python-client python-dotenv pypdf requests
```

## 設定

複製 `.env.example` 為 `.env` 並填入 API Key：

```bash
cp .env.example .env
```

- `ANTHROPIC_API_KEY`：從 [Anthropic Console](https://console.anthropic.com/) 取得
- `GOOGLE_API_KEY`：從 [Google Cloud Console](https://console.cloud.google.com/) 取得，需啟用 Google Drive API（不設定則只能下載前50個檔案）

## 使用

```bash
# 測試（只處理第一個檔案）
python pdf_news_summarizer.py --test

# 全部執行
python pdf_news_summarizer.py
```

## 輸出

每個 PDF 對應一個同名 `.txt` 摘要檔，存放於 `gdrive_pdfs/` 資料夾中。

```
【概述】本期主題為...

【重點】
1. ...
2. ...
3. ...
4. ...
5. ...
```
