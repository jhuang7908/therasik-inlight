# 每周自动更新

仓库：`jhuang7908/therasik-inlight`  
默认分支：`main`  
线上：https://inlight.therasik.com/

这个仓库没有 GitHub Actions 定时任务。每周由助手在本机跑命令，不要再加 schedule。

## 安装

```bash
python -m pip install -r requirements.txt
```

Windows 上如果 `python` 不在 PATH 里，用 `py -3` 代替 `python`。

密钥放在环境变量里，不要写进文件，也不要提交。可以参照 `.env.example` 在本机导出，`.env` 已被 git 忽略。

| 变量 | 用在哪 |
| --- | --- |
| `ANTHROPIC_API_KEY` | `run_weekly.py` 筛选和写中文 |
| `OPENAI_API_KEY` | `run_weekly.py` 配图和公众号封面 |
| `ANTHROPIC_MODEL` | 可选，默认 `claude-sonnet-5-5`（脚本启动时会检查模型可用性） |
| `OPENAI_IMAGE_MODEL` | 可选，默认 `gpt-image-1` |
| `WECHAT_APPID` | 只有 `publish_wechat.py` 需要 |
| `WECHAT_APPSECRET` | 只有 `publish_wechat.py` 需要 |

## 每周运行

```bash
python run_weekly.py
```

顺序是：读 `sources.yaml`，抓最近 7 天，调用 Claude 筛选并写中文，调用 OpenAI 图像接口画配图和封面，把结果写入网站内容目录，并写出一份公众号 HTML。这条命令不推送 git，也不调用公众号接口。

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

## 写作规则（不写自说自话）

适用于每周自动生成的条目、r8 解读正文（`content/articles/*.md`）、首页和各栏目文案。`run_weekly.py` 的筛选提示词里有同样的规则。

- 只写论文和交易本身，不留编辑过程记录：不写“用户提供全文”“本文不补”“两处均保留”“Supplementary 未读”“摘录未给出……”，不写抓取或资料收集情况，也不用一句话解释为什么缺数据。
- 不写许可或版权自辩（如“许可为 CC BY，正文为改写，未逐句抄录”）。
- 必要的说明每页只出现一次，简短中性：不构成投资建议、配图由 AI 生成、来源说明。页脚已有的不在正文重复；同一页不放两条 AI 配图说明；核对记录只印一次（`build_pages.py` 与首页会从关键数据卡里去掉“证据等级 / 核对记录”，只保留“作者、出处与核对”里的那一条）。
- 数字不一致只写一次：“各来源数字不一：A 报 X，B 报 Y。”不写“本文按……”“口径冲突”“不作换算”之类的处理说明。
- 不写解释页面版式的话（“本页列出……”“下方卡片……”“点击……”），必要的界面标签除外。
- 不写“前沿追踪即为……样例”这类自我介绍；标语保持平实；赞助只写一句“赞助内容均有标注。”加合作方式，不为自身独立性辩护。
- 不用口语或俚语做自我评价。
- 引文、事实、导航和一次性的说明保留。

## 新闻源

名单在 `sources.yaml`。每条有名称、学术或行业、`rss` 或 `pubmed`、首页和 feed。改来源只改这个文件。

## 输出文件

正式运行：

- `content/weekly/YYYY-MM-DD/articles.json` 学术文章
- `content/weekly/YYYY-MM-DD/deals.json` 行业动态
- `content/weekly/YYYY-MM-DD/images/` 配图
- `content/weekly/YYYY-MM-DD/wechat/article.html` 公众号正文
- `content/weekly/YYYY-MM-DD/wechat/cover.png` 公众号封面
- `content/latest.json` 网站实际读取的最近一批；首页、领域和存档会把这里的新条目叠在原有内容前面

预览运行：同样的结构在 `preview/weekly/YYYY-MM-DD/`。`preview/` 不进 git。

文章只保留来源列表里出现过的链接。模型补出来的地址会被丢掉，并写进日志。

## 去重

脚本会自动跳过已有内容：

1. `content/latest.json` 里的文章和动态 URL
2. `index.html` 里 CAT 数组中的 DOI 链接
3. `content/catalog.json` 里已收录条目的 URL（含 hidden / excluded，避免把它们再抓回来）

这样避免重复抓取同一篇论文。如果一篇文章从不同来源抓到（例如 Nature RSS 和 PubMed），会保留先到的那条。

## AI 工具使用

- Claude：使用 tool_use 模式返回结构化 JSON，避免解析错误
- 图像生成：使用 gpt-image-1，提示词强调 BioRender 风格、无文字无标签

## 静态文章页面（社交分享）

```bash
python build_pages.py          # 写入 pages/article/*.html、sitemap.xml，并回写首页目录
python build_pages.py --dry-run  # 预览，不写文件
```

25 篇 r8 结构化解读是一等内容：`content/catalog.json` 里 `curated` + `locked`，正文在 `content/articles/<id>.json` 与 `.md`（不要改写）。`build_pages.py` 据此生成完整文章页（机制图、卡片图、领域色标），并只把这 25 篇写进首页 CAT / `sitemap.xml`。

没有完整正文的条目，以及排除的 `c7-cell-5`、`c5-am-6`，状态为 `no_fulltext` / `excluded`。数据留在 catalog 里，不出现在首页、领域页、latest feed 和 sitemap。

分享链接：`https://inlight.therasik.com/pages/article/{article-id}.html`

每周 `python run_weekly.py` 不会覆盖 locked 解读，也不会把 hidden id 重新显示。`update_latest` 在写出 `content/latest.json` 前调用 `inlight_catalog.protect_latest_payload`；保护失败则拒绝写文件（fail-closed）。首页 `mergeWeekly` 同样跳过 `LOCKED` / `HIDDEN`。回归：`python -m unittest tests.test_r8_golive`。

## 出错时看哪里

日志在 `logs/`。

- 每周任务：`logs/weekly-日期-时间.log`
- 公众号草稿：`logs/wechat-日期-时间.log`

退出码：`1` 缺少密钥，`2` 这一窗口没有抓到条目或找不到一周目录，`3` 模型结果里没有可用条目，`4` 其他错误（接口返回、目录已存在、上传失败）。同日目录已存在时不会覆盖，先看日志再决定要不要换一天或清掉那一天的目录。

来源 feed 打不开时，这一条记警告，其余来源继续。全部失败才会以退出码 `2` 结束，并且不会改 `content/latest.json`。

## 2026-10-11 周更修复与公告来源

一键运行（自动在仓库内建 `.venv` 并安装依赖，`.venv/` 已被 git 忽略）：

```bash
./weekly.sh --fetch-only                 # 只抓来源（含公告），写 preview/weekly/日期/candidates.json；不需要任何密钥
./weekly.sh --dry-run                    # 需要 ANTHROPIC_API_KEY；没有 OPENAI_API_KEY 时自动跳过配图
./weekly.sh --dry-run --out-dir /tmp/x --no-images
./weekly.sh                              # 正式写入 content/
```

- 密钥可以 export，也可以写在仓库根目录的 `.env`（已忽略）；脚本会读取，但不会覆盖已 export 的变量，日志只记变量名。
- `ANTHROPIC_MODEL` 指定的模型不存在时，脚本自动改用账号里可用的 Sonnet 模型并在日志里写明。
- `content/weekly/*/images/` 不再被 git 忽略：`content/latest.json` 里的文章引用这些配图，必须随内容一起提交。

### 上市公司公告（filings.py）

`sources.yaml` 的 `filings:` 段控制。每周抓过去 7 天：

- SEC EDGAR 全文检索（efts.sec.gov）：8-K / 6-K（部分查询含 10-Q / 20-F），关键词 "exclusive license" "Greater China"、"exclusive license agreement"、"NewCo"、"upfront payment"；只留医药/生物技术 SIC；同一份 filing 只留一条（优先 EX-99 新闻稿附件）。User-Agent 带 contact@inlightbio.com，限速 5 次/秒。
- 港交所披露易：按日期检索（中文标题）+ 最新披露 feed；关注名单 02162、01276、03692、09995、02142、06990、01093、01177、01801、09926 的自愿公告/内幕消息/须予披露交易全收，其它医药公司只收许可、合作、新公司、合资类标题；排除月报表、一般授权、回购等。
- 巨潮资讯（沪深）：关键词 授权许可、独家许可、合作协议、合资、设立公司、首付款、里程碑；排除 生产许可证、上市许可申请、注册证；只留医药类公司或标题。

抓到的公告以 `kind: filing` 进入允许引用的 URL 列表，并下载正文前几页做摘录（PDF 用 pdftotext）。写稿提示词要求公告优先于媒体，同一交易用公告链接。任何一个平台出错只记日志，其它来源照跑。回归：`python -m unittest tests.test_filings`。

### 发布路径

脚本只写文件，不推送。正式运行后在仓库里提交 `content/weekly/日期/`（含 images）和 `content/latest.json`，以补丁或 PR 合入 `main`，GitHub Pages 自动重建。
