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
| `SEC_USER_AGENT` | 可选，SEC EDGAR 来源需要。格式：`公司名 contact@example.com`。未设置则跳过 SEC 来源 |
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

只输出学术文章，跳过交易提取：

```bash
python run_weekly.py --no-deals
# 或通过环境变量
INLIGHT_NO_DEALS=1 python run_weekly.py
```

`--no-deals` 跳过 SEC EDGAR 交易提取，只输出学术文章。当没有文章时（退出码 3），不会写任何输出。现有的 `latest.json` 中的交易数据会被保留。

确认内容可以上站之后，在仓库里提交并推送 `main`。GitHub Pages 监听 `main` 的根目录，推送后自动重新构建，站点跟着更新。HTTPS 已启用并强制跳转。

公众号只进草稿箱，不群发：

```bash
python publish_wechat.py
python publish_wechat.py --week 2026-10-07
```

不写 `--week` 时，用 `content/weekly/` 里日期最新的一周。

## 新闻源

名单在 `sources.yaml`。每条有名称、学术或行业、`rss` 或 `pubmed`、首页和 feed。改来源只改这个文件。

## 交易披露来源（官方公告）

交易数据**仅来自 SEC EDGAR**。所有交易通过 `sec_deals.py` 模块从 SEC 8-K/6-K 文件中提取。

| 来源 | 覆盖范围 | 环境变量 |
| --- | --- | --- |
| SEC EDGAR | 美股 8-K/6-K（SIC 2834/2835/2836/8731） | 需要 `SEC_USER_AGENT` |

**交易分类**：
- `lic`（授权合作）：许可/授权/合作协议
- `acq`（并购）：收购/并购
- `inv`（融资/信贷）：股权融资/贷款协议

**严格验证**：
- 所有引用必须是 SEC 文件的精确子串
- 交易对手必须出现在引用中
- 金额必须在包含交易对手的同一段落中
- 服务协议、办公租约等非交易合同会被过滤
- 条件性金额（"may receive up to"）不作为标题金额

**金额来源标注**：每条交易标注 `filing`（来自披露文件）。金额字段只填文件里明确写出的数字。

**日期显示**：交易卡片显示的日期是事件发生日期（8-K/6-K 的 "date of earliest event"），而非文件提交日期。

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

这样避免重复抓取同一篇论文。如果一篇文章从不同来源抓到（例如 Nature RSS 和 PubMed），会保留先到的那条。

## AI 工具使用

- Claude：使用 tool_use 模式返回结构化 JSON，避免解析错误
- 图像生成：使用 gpt-image-1，提示词强调 BioRender 风格、无文字无标签

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
