# 仓库协作指南

## 会话交接

长期设计约束以 `docs/ARCHITECTURE.md` 为准。开始新的工作会话时，还应读取存在的
`.codex/HANDOFF.md`；该交接文件仅保存在本地，不纳入版本控制。

## 项目结构与模块划分

应用代码位于 `src/job_mail_assistant/`。不同服务的集成应保持分离：`mailbox.py`
负责 QQ IMAP/SMTP，`ai_parser.py` 负责结构化提取，`deadlines.py` 负责日期解析，
`feishu.py` 负责持久化，`apple_calendar.py` 负责 CalDAV upsert。`app.py` 编排顺序
处理流程，`__main__.py` 提供 CLI 入口。

测试位于 `tests/`，应按外部行为而非内部实现组织。GitHub workflow 位于
`.github/workflows/`；配置说明写入 `README.md`，`.env.example` 只列出支持的变量，
不得包含真实值。本仓库不设置静态资源目录或数据库。

## 构建、测试与开发命令

开发环境使用 Python 3.12，最低支持 Python 3.11。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
python -m ruff check .
python -m pytest --cov=job_mail_assistant --cov-report=term-missing
python -m job_mail_assistant run --dry-run
```

`dry-run` 可能读取邮件、调用 AI 服务并读取飞书进行去重，但不得写入记录、创建日历
事件或发送报告。只有在重试已持久化的 Calendar 失败时才使用
`python -m job_mail_assistant retry-calendar`。

## 编码风格与命名约定

使用四空格缩进、类型提示和 100 字符行宽。Ruff 启用 `E`、`F`、`I`、`B` 和 `UP`
规则。模块、函数和变量使用 `snake_case`，类使用 `PascalCase`，常量使用
`UPPER_SNAKE_CASE`。AI 输出始终视为不可信输入，必须先通过 Pydantic 验证，再进入
确定性的业务逻辑。

## 测试规范

Pytest 发现 `tests/test_*.py`，测试命名为 `test_<expected_behavior>`。解析和日期规则应
添加聚焦的单元测试；IMAP、SMTP、AI、飞书和 CalDAV 使用 fake 编写集成式测试。
fixture 中不得使用真实邮件正文或凭据。CI 会报告覆盖率但不设置硬性阈值；新增行为应
维持或提高相关覆盖率。

## Commit 与 Pull Request 规范

沿用现有简洁、祈使语气的 commit 风格，例如 `Harden public workflow dependencies`。
每个 commit 只包含一个明确变更。Pull Request 应说明目的、运行影响、测试证据，以及
环境变量或 schema 变化，并关联相关 issue。只有文档或外部可见 UI 发生变化时才需要
截图。

## 安全与 Agent 注意事项

Secret 只能保存在本地 `.env` 或 GitHub Actions Secrets。不得提交个人地址、Wiki/Base
URL、Token、生产日志或真实邮件 fixture。安全披露遵循 `SECURITY.md`。受版本控制的
项目文档是长期上下文；命令、配置、权限或恢复行为变化时，应同步更新 `README.md`。
