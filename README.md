# Job Mail Assistant

一个仅供个人使用的轻量求职邮件自动处理工具。每天北京时间 04:37 从 QQ 邮箱读取最近 7 天邮件，将新增测评、笔试和面试事项写入飞书多维表格、同步到 iCloud Calendar，并在 08:00 前发送客观早报。

## 工作方式

```text
QQ IMAP → 招聘候选过滤 → AI 结构化提取 → Python 时间计算
        → 飞书去重/持久化 → iCloud CalDAV → QQ SMTP 早报
```

AI 只理解自然语言和提取时间组成部分。小时、自然日、中国工作日、明确日期及星期表达均由 Python 计算；无法可靠确定的时间留空并进入“需要人工确认”。邮件接收基准使用 IMAP `INTERNALDATE`，不使用邮件头 `Date` 或任务执行时间。

## 安装与本地运行

要求 Python 3.12（代码最低兼容 Python 3.11）。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install --no-deps -e .
Copy-Item .env.example .env
```

将 `.env` 中的值加载到当前终端后运行：

```powershell
python -m job_mail_assistant run --dry-run
python -m job_mail_assistant run
python -m job_mail_assistant retry-calendar
```

`--dry-run` 会读取 QQ 邮件、调用 AI 并读取飞书用于去重，但不会写飞书、创建 Calendar 事件或发送早报。
`retry-calendar` 只补偿飞书中待处理或失败的 iCloud 事件，不读取邮件、不调用 AI，也不发送早报。

## 环境变量

| 名称 | 必需 | 说明 |
|---|---:|---|
| `QQ_EMAIL` | 是 | QQ 邮箱地址 |
| `QQ_AUTH_CODE` | 是 | QQ 邮箱授权码，不是 QQ 登录密码 |
| `FEISHU_APP_ID` | 是 | 专用飞书企业自建应用 App ID |
| `FEISHU_APP_SECRET` | 是 | 专用飞书应用 Secret |
| `FEISHU_WIKI_URL` | 是 | 目标飞书 Wiki/Base 的完整 URL，作为私人配置保存 |
| `ICLOUD_USERNAME` | 是 | iCloud 账户的完整电子邮件地址，不使用电话号码 |
| `ICLOUD_APP_PASSWORD` | 是 | Apple App 专用密码 |
| `AI_API_KEY` | 是 | OpenAI-compatible API Key |
| `AI_BASE_URL` | 是 | 兼容 API 的 base URL，通常以 `/v1` 结尾 |
| `AI_MODEL` | 是 | 实际使用的模型名 |
| `ICLOUD_CALENDAR_NAME` | 否 | 默认 `秋招` |
| `FEISHU_TABLE_NAME` | 否 | 默认 `测评&面试` |
| `SCAN_DAYS` | 否 | 默认 7，允许 1–30 |

代码、日志、README 和测试 fixture 均不包含真实凭据或真实邮件。

## QQ 邮箱设置

1. 在 QQ 邮箱设置中开启 IMAP/SMTP 服务。
2. 生成邮箱授权码并保存为 `QQ_AUTH_CODE`。
3. 不要使用 QQ 密码。

连接地址固定为：

- IMAP SSL：`imap.qq.com:993`
- SMTP SSL：`smtp.qq.com:465`

程序以只读方式选择 INBOX，使用 `BODY.PEEK[]`，不会改变邮件已读状态。

## 飞书设置

使用单独的企业自建应用并发布版本。运行时预计需要以下最小权限，最终以飞书开发者后台的接口提示为准：

- `wiki:node:retrieve`
- `base:app:read`
- `base:table:read`
- `base:field:read`
- `base:record:read`
- `base:record:create`
- `base:record:update`

还需将该应用加入目标 Wiki/Base 的可访问成员。生产应用不需要删除记录、删除表或修改 schema 的权限。

目标主表为 `测评&面试`。除已有日常字段外，一次性配置以下技术字段：

- 文本：`Message-ID`、`邮件指纹`、`原邮件主题`、`原始时间描述`、`确认说明`、`Calendar Event ID`、`Calendar 状态`、`Calendar 错误`、`时间类型`
- 复选框：`时间为推算`、`需要人工确认`
- 日期时间：`最后处理时间`、`结束时间`

同一 Base 还需表 `JMA_运行状态（请勿手动编辑）`，字段为：

- `状态键`：文本主字段
- `上次完整成功时间`：日期时间
- `Schema 版本`：文本

程序启动时会验证 schema，但不会自动新增或删除字段。这样 GitHub Actions 可以维持最小权限。

## iCloud Calendar

1. 在 Apple 账户中生成 App 专用密码。
2. `ICLOUD_USERNAME` 使用该账户可登录 iCloud 的完整电子邮件地址；手机号 Apple 账户建议先创建 iCloud 邮件地址。
3. 保证 iCloud 中恰好存在一个名称与 `ICLOUD_CALENDAR_NAME` 完全一致的日历。
4. 默认目标为 `秋招`。程序不会自动创建日历，也不会在同名日历之间猜选。

所有事件采用北京时间并带提前 24 小时的 DISPLAY 提醒。仅有开始时间时，固定事项日历展示为 60 分钟；deadline 展示为 15 分钟，备注会明确默认结束时间并非邮件原文。
iCloud 不支持可靠的 UID REPORT 查询，因此程序使用由飞书 `record_id` 派生的固定 UID/资源地址直接幂等覆盖；重复运行不会创建重复事件。

## GitHub Actions

工作流 `.github/workflows/daily.yml`：

- 每天北京时间 `04:37` 运行，并显式使用 `Asia/Shanghai` 时区；避开 GitHub Actions 整点拥堵并为调度队列预留数小时，确保早报尽量在 08:00 前送达。
- 支持 `Run workflow`，可选择 dry-run 或仅重试 Calendar。
- 使用 concurrency 防止并发写入。
- 仅授予 `contents: read`。

在仓库的 **Settings → Secrets and variables → Actions** 中配置：

```text
QQ_EMAIL
QQ_AUTH_CODE
FEISHU_APP_ID
FEISHU_APP_SECRET
FEISHU_WIKI_URL
ICLOUD_USERNAME
ICLOUD_APP_PASSWORD
AI_API_KEY
AI_BASE_URL
AI_MODEL
```

`ICLOUD_CALENDAR_NAME` 有默认值；如需覆盖，可额外建立同名 Secret。

### 公开仓库安全

- 真实凭据和飞书 Wiki/Base 链接只放在 GitHub Actions Secrets 或本地 `.env`，不要写入代码、示例配置、测试 fixture 或 Issue。
- `.env.example` 仅保留空值和说明；提交前运行测试，并检查 `git diff --cached`。
- 如怀疑凭据曾进入提交历史，应先轮换凭据并清理历史，再公开仓库。安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。

## 去重和失败恢复

- 首选 `Message-ID`；缺失时使用发件人、主题、服务器接收时间和正文哈希组成的指纹。
- `References`/`In-Reply-To` 命中已有记录时更新原事项。
- Calendar UID 由飞书 `record_id` 确定生成；日历写入成功但飞书回写失败时，下次会查找相同 UID，不会创建新 UID。
- Calendar 失败会写回状态并显示在早报，下次自动重试。
- AI 或飞书业务写入失败时不推进成功游标；Calendar 单项失败已持久化，可以推进游标。
- 淘汰/未通过只进入早报，不自动删除日历或修改“已完成”。

## 测试

```powershell
python -m pytest
python -m ruff check .
```

测试覆盖时间计算、中国节假日和调休、邮件过滤、链接约束、Message-ID/线程去重、24 小时提醒，以及连续运行两次不重复写表或建日历。

## 常见错误

- `Missing required environment variables`：环境变量未加载，名称以 `.env.example` 为准。
- QQ IMAP/SMTP 认证失败：确认使用邮箱授权码且已开启 IMAP/SMTP。
- 飞书 `99991672` 或 scope 错误：给专用应用增加错误中列出的最小 scope，重新发布版本。
- 飞书 schema 未配置：按“飞书设置”补齐字段和技术表，字段名必须完全一致。
- Wiki/Base 无权限：将专用应用加入目标资源可访问成员。
- iCloud 日历数量不是 1：确认名称完全一致且没有同名日历。
- 中国工作日历不覆盖目标年份：升级 `chinesecalendar` 到包含国务院最新安排的版本；在此之前相关时间会要求人工确认。
- Structured Output 不受模型支持：程序会自动退回 JSON Object 模式并继续做 Pydantic 严格校验。
