# 每周自动更新

仓库：`jhuang7908/therasik-inlight`  
默认分支：`main`  
线上：https://inlight.therasik.com/

这个仓库没有 GitHub Actions 定时任务。每周由助手在本机跑命令，不要再加 schedule。

## 安装

```bash
# 配图质控需要系统 OCR（一次性）：
sudo apt-get install -y tesseract-ocr
python -m pip install -r requirements.txt
```

`requirements.txt` 含 Pillow、pytesseract，以及 `rapidocr-onnxruntime`（无 tesseract 时的后备）。配图前脚本会检查这两项；缺 Pillow 或 OCR 则闭门跳过全部生成，绝不写 1×1 空白图。

Windows 上如果 `python` 不在 PATH 里，用 `py -3` 代替 `python`。

密钥放在环境变量里，不要写进文件，也不要提交。可以参照 `.env.example` 在本机导出，`.env` 已被 git 忽略。

| 变量 | 用在哪 |
| --- | --- |
| `ANTHROPIC_API_KEY` | `run_weekly.py` 筛选、写中文深度解读、数字/主张核对（Claude） |
| `OPENAI_API_KEY` | `run_weekly.py` 配图和公众号封面（图像模型不变） |
| `ANTHROPIC_MODEL` | 可选，默认 `claude-sonnet-5-5`（脚本启动时会检查模型可用性） |
| `OPENAI_IMAGE_MODEL` | 可选；默认先试 `gpt-image-2`，不可用再回退 `gpt-image-1`。1536×1024、quality=high、不透明白底，再裁到 1600×989 |
| `GEMINI_API_KEY` | 深度解读的独立 ACIR 质控（只审不写）。缺了就闭门失败，不发该篇 |
| `GEMINI_MODEL` | 可选；默认 `gemini-3.1-pro-preview`（`sources.yaml` 的 `gemini_model`） |
| `INLIGHT_EXTRA_ENV_FILE` | 可选。生产机 `inlight.env` 没有 Gemini 密钥时，指向另一份只读 env。只读取 `GEMINI_API_KEY`，不写文件，不把内容打进日志 |
| `WECHAT_APPID` | 只有 `publish_wechat.py` 需要 |
| `WECHAT_APPSECRET` | 只有 `publish_wechat.py` 需要 |

生产服务器上的每周 `inlight.env` 不会被改。若密钥已经在同一台机器的另一份 env 里，把路径写到 `INLIGHT_EXTRA_ENV_FILE` 或 `sources.yaml` 的 `extra_env_file`。加载失败或两处都没有密钥时，深度解读质控闭门失败。

## 每周运行

```bash
python run_weekly.py
```

这条命令**默认**走新文章深度管线（enrich + triage + 逐篇生成 + `validate_depth` + writing-standard 审计）。`sources.yaml` 的 `min_deep` 会打开严格门控。不需要再加 `--use-new-pipeline`。

顺序是：读 `sources.yaml`，抓最近 7 天，调用 Claude 筛选并写中文深度解读，调用 OpenAI 图像接口画配图和封面，把结果写入网站内容目录，并写出一份公众号 HTML。这条命令不推送 git，也不调用公众号接口。

调试时若要强制走旧的整周 `claude_draft` 路径：

```bash
python run_weekly.py --use-legacy-pipeline
```

只想先看生成结果、不改网站文件：

```bash
python run_weekly.py --dry-run
```

`--dry-run` 仍会调用 Claude 和 OpenAI（会消耗额度），但只写到 `preview/weekly/日期/`。它不修改 `content/`，也不推送公众号。

确认内容可以上站之后，在仓库里提交并推送 `main`。GitHub Pages 监听 `main` 的根目录，推送后自动重新构建，站点跟着更新。HTTPS 已启用并强制跳转。

公众号只进草稿箱，不群发：

```bash
python publish_wechat.py
python publish_wechat.py --week 2026-10-07
```

不写 `--week` 时，用 `content/weekly/` 里日期最新的一周。

## 新闻源

名单在 `sources.yaml`。每条有名称、学术或行业、`rss` 或 `pubmed`、首页和 feed。改来源只改这个文件。

## 输出文件

正式运行：

- `content/weekly/YYYY-MM-DD/articles.json` 学术文章
- `content/weekly/YYYY-MM-DD/deals.json` 行业动态
- `content/weekly/YYYY-MM-DD/images/` 配图
- `content/weekly/YYYY-MM-DD/wechat/article.html` 公众号正文
- `content/weekly/YYYY-MM-DD/wechat/cover.png` 公众号封面
- `content/weekly/YYYY-MM-DD/qc_report.json` 每篇质控项的通过/失败、Gemini 分数和丢弃原因
- `content/latest.json` 网站实际读取的最近一批；首页、领域和存档会把这里的新条目叠在原有内容前面

预览运行：同样的结构在 `preview/weekly/YYYY-MM-DD/`。`preview/` 不进 git。

文章只保留来源列表里出现过的链接。模型补出来的地址会被丢掉，并写进日志。

## 去重

脚本会自动跳过已有内容：

1. `content/latest.json` 里的文章和动态 URL
2. `index.html` 里 CAT 数组中的 DOI 链接

这样避免重复抓取同一篇论文。如果一篇文章从不同来源抓到（例如 Nature RSS 和 PubMed），会保留先到的那条。

## AI 工具使用

- Claude（`ANTHROPIC_MODEL`，默认 claude-sonnet-5-5）：选题、从全文起草深度解读、数字/主张核对。不换别的写作模型。
- Gemini（`gemini-3.1-pro-preview`）：只做独立 ACIR 复审（结构/深度/数字密度/机制与推测/局限/可追溯），不写正文。
- 图像生成：`gpt-image-2`（不可用则 `gpt-image-1`），quality=high，1536×1024 不透明白底，再按 house style v3 裁到 1600×989。机制图只给读过全文的深度解读。质控用主语包围盒占比（68–78%）和真实 OCR；OCR 不可用则闭门不发图。

## 静态文章页面（社交分享）

生成带 Open Graph 标签的静态 HTML 页面，便于社交媒体分享时显示正确的标题、摘要和缩略图：

```bash
python build_pages.py          # 写入 pages/article/*.html
python build_pages.py --dry-run  # 预览，不写文件
```

每篇文章会生成一个对应的 HTML 页面：
- 包含 og:title、og:description、og:image 等 Open Graph 标签
- 包含 Twitter Card 标签
- 访问时自动跳转到主站对应的文章 hash 路由

页面会检测 `img/{article-id}.webp`，如果存在则使用文章配图作为 og:image，否则使用默认图片。

分享链接格式：`https://inlight.therasik.com/pages/article/{article-id}.html`

## 出错时看哪里

日志在 `logs/`。

- 每周任务：`logs/weekly-日期-时间.log`
- 公众号草稿：`logs/wechat-日期-时间.log`

退出码：`1` 缺少密钥，`2` 这一窗口没有抓到条目或找不到一周目录，`3` 模型结果里没有可用条目，`4` 其他错误（接口返回、目录已存在、上传失败）。同日目录已存在时不会覆盖，先看日志再决定要不要换一天或清掉那一天的目录。

来源 feed 打不开时，这一条记警告，其余来源继续。全部失败才会以退出码 `2` 结束，并且不会改 `content/latest.json`。

---

## 每周 QA 评分表

每条内容逐项打分。**A 档（深度解读）满分 20 分，低于 16 分退回重写；B 档（速览）满分 12 分，低于 9 分退回重写。**

### 逐项评分标准

| # | 检查项 | A 档分值 | B 档分值 | 判定标准 |
|---|--------|----------|----------|----------|
| 1 | 写明研究类型/期别 | 2 | 1 | 数据卡 `study_type` 非空；缺项省略字段，不写占位套话 |
| 2 | 写明样本量 n | 2 | 1 | 含单位与分组；材料没有则省略该字段 |
| 3 | 对照与分组 | 1 | 1 | 单臂/随机/无对照已写明 |
| 4 | 干预方案（药名/剂量/途径/疗程） | 1 | — | |
| 5 | **主要终点 + 数值结果** | 3 | 2 | 必须有数字；缺则本条不得发布为解读 |
| 6 | 统计量（HR/CI/P） | 1 | — | 临床研究必填；基础研究可写"原文未报告统计学检验" |
| 7 | 安全性数据 | 1 | — | 临床研究必填 |
| 8 | 每个结果段都带数字 | 2 | 1 | `results` 每段至少 1 个数字 |
| 9 | 机制有交代且区分证明/推测 | 2 | 2 | brief档也需要适当交代机制 |
| 10 | **局限 ≥3 条且具体** | 3 | 2 | 空话不计分 |
| 11 | 意义用条件句、不夸大 | 1 | 1 | 出现"重磅/颠覆/碾压"直接扣光 |
| 12 | 出处完整（期刊+日期+DOI+证据等级） | 1 | 1 | |
| 13 | **数字可回溯** | 一票否决 | 一票否决 | 抽查 3 个数字，任意一个在来源里找不到 → 整条退回 |
| 14 | 字数达标 | 门槛 | 门槛 | A 档 1400–1900 字；B 档 450–650 字 |

### 每周整体检查

- [ ] 本周 deep 篇数 3–5，且每篇 `evidence_level` 为 `fulltext`（程序取到 Results 正文）
- [ ] 本周所有条目中，`evidence_level = press/secondary` 占比 ≤ 20%
- [ ] 零数字条目数 = 0
- [ ] 零局限条目数 = 0
- [ ] 已发布条目的更正记录当周清零

### 营销词汇黑名单

以下词汇禁止出现在标题、一句话结论、正文中：

- 重磅
- 颠覆
- 改写教科书
- 震撼
- 碾压
- 轰动
- 史诗级
- 划时代

检测到上述词汇时，`validate_depth()` 会自动标记并退回。

### 数字可回溯要求

每个生成的数字必须在 `data_points` 中登记，且 `source_quote` 必须是原始材料中的精确子串。校验流程：

1. 对每个 `data_points[].source_quote`，在原始材料中做空白符归一化后的子串匹配
2. 从正文提取所有数字（百分比、倍数、单位数、统计量）
3. 每个正文数字必须能在 `data_points[].value` 中找到对应项
4. 未登记的数字 → 校验失败

未通过校验的文章：重试一次（prompt 中附上问题清单）→ 仍不过则降档 → 再不过则丢弃并记日志。
