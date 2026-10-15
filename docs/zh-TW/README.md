[English](../../README.md)

# stock-data-downloader

對外專案與未來儲存庫名稱為 `stock-data-downloader`，目前支援台灣市場；此名稱不表示已實作其他市場。現有本機候選位置維持不變，公開與排程另待確認。

獨立的台灣上市／上櫃市場資料管線，使用 Shioaji 與交易所官方來源，
產出分鐘／日價格、公司行動、證券分類、月營收、法人買賣與產業價值鏈證據。
資料以 CSV、SQLite、Parquet 與 JSON 保存在本機。

## ⚙️ 如何初始化

需要 Python **3.13 以上**與 uv。在專案根目錄執行：

```powershell
uv sync
uv run pre-commit install
```

正式下載環境若尚無 `.env`，才從 `.env.example` 複製並私下填入憑證。
不要覆蓋既有設定，也不要輸出或提交憑證內容。

## 🚀 快速開始

只在正式資料所在的主要 checkout 執行日更新：

```powershell
uv run scripts/run_daily.py
```

順序為下載 → 分鐘轉日線 → 公司行動 → 官方日價格 → Parquet 重建 →
月營收 → 法人買賣 → 產業價值鏈 → 完整性檢查 → 備份。
**官方價格擷取必須先於 Parquet 重建。**

開發工作區的 `data/` 是部分實體快照，不會同步更新。缺少分鐘 CSV、
`raw/` 或官方資料庫，不代表可以刪除來源或重建正式快取。正式環境可在自己的
`.env` 以 `SHIOAJI_DATA_DIR` 把資料目錄放在專案外；開發工作區不會繼承這個設定，
詳見 [資料位置](../operations.md#data-location)。

## 📂 專案結構

| 位置                            | 用途                                               |
| ------------------------------- | -------------------------------------------------- |
| `scripts/`                      | 所有管線 CLI，以 `uv run scripts/<name>.py` 呼叫   |
| `shp_utils/`、`config/`         | 共用處理工具，以及固定解析到專案根目錄的資料路徑   |
| `tests/`                        | 離線測試與小型測試資料                             |
| `docs/`                         | 操作說明、來源證據、行為規格與歷史變更紀錄         |
| `data/`                         | 資料與證據（或 `SHIOAJI_DATA_DIR` 指定的外部目錄） |
| `logs/`、`.tmp/`、`.worktrees/` | 本機日誌、研究／交接材料與工作區                   |
| `cron/`                         | Windows 排程入口及安裝腳本                         |

Ruff 設定集中在 `pyproject.toml`。忽略檔、同名檔或封存不等於垃圾；
現有資料、憑證、研究證據、下載進度與備份均應保留。

提交時另有 UTF-8／文字損壞檢查，合法中文不會被擋，也不會改寫檔案或輸出
原文。Python 檔案會執行針對本次提交的 Mypy 與 Pyright 型別檢查；設定在
`pyproject.toml`，使用本專案的 uv 環境。局部通過不代表全部舊程式已無型別問題。
Mypy 保留匯入模組的型別資訊，但只回報明確指定檔案的問題。一般提交會自動
執行這些檢查，不必另跑一次；修改檢查本身或排查失敗時再針對問題驗證。

## 開發與 PR

遵守 [AGENTS.md](../../AGENTS.md) 與 [Git 工作流程](../en/git-workflow.md)：
在非 main 分支開發，執行相關驗證，提交自己的變更並發 PR，經 review
後由擁有者決定是否合併。這是協作流程，不宣稱 GitHub 已強制阻擋 main 推送。

## 📐 行為基準

[文件索引](../README.md) 彙整全部行為規格與證據指南。
行為契約見 `docs/contracts/`；歷史提案見 `docs/decisions/legacy/`，其實作狀態尚未核實。

## 🛠 自動化排程 (Windows)

[排程與日誌](../operations.md#windows-scheduling)：日更新為週一至週五
18:00，歷史回補為每日 18:25；兩者可能重疊。排程腳本集中在 `cron/`；合併並 pull 後才從正式工作區更新排程入口，不應從開發工作區註冊。

## ⚡ 整合 Parquet 快取

[管線與資料契約](../pipeline.md#consolidated-parquet-cache)：官方資料優先，
Shioaji 日線補足缺口。每列保留來源，不能單靠 CSV 檔案時間判定優先權。

## 🛡️ 資料安全與備份

[正式資料、工作區與備份限制](../operations.md)：加法式備份不刪除目的地，
但仍會更新檔案，且逐檔複製不保證正在寫入的 SQLite 一致性。舊工作區封存
不代表正式資料已完整備份。碰資料前需確認實際寫入者、備份方法與擁有者授權。

[詳細操作指南](../pipeline.md) 保留各來源的獨立 CLI 用法與資料欄位說明；
[唯讀進度檢查](../data-progress.md) 不會因資料庫缺席而建立空資料庫。

## 📝 License

[Apache License 2.0](../../LICENSE)。
