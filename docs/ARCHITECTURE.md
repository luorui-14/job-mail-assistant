# 架构与设计约束

## 处理模型

Job Mail Assistant 是单进程、顺序执行的处理流程：

```text
QQ IMAP → 候选邮件过滤 → AI 结构化提取 → 确定性时间解析
        → 飞书 Base 持久化 → iCloud CalDAV → QQ SMTP 报告
```

除非明确进行架构变更，否则不得引入数据库、Web 服务、Agent framework 或并行写入者。
飞书 Base 同时保存业务记录和唯一的持久化运行游标。

## 邮件扫描与身份识别

默认 `SCAN_DAYS=7` 是计划任务漏跑后的恢复窗口，不是状态游标。除非设计了其他漏跑
恢复机制，否则应保留该默认值。对完整、明确、可执行的重复邮件，应在 AI 解析前跳过：
优先使用规范化的 `Message-ID`，缺失时使用确定性的 fallback fingerprint。

只有当已持久化记录的确认说明明确指出可确定修复的 parser 缺陷时，完全相同的邮件才可
重新处理。目前允许修复的缺陷仅包括：缺失明确日期组成部分，或唯一 action URL 被拒绝。
修复完成后，该邮件重新进入正常的重复跳过路径。

线程更新只能使用 `References` 或 `In-Reply-To` 的精确匹配。非线程合并要求公司、岗位、
事项类型和解析后的时间全部精确相同，不得引入模糊语义合并。

IMAP 必须保持只读，并以服务器 `INTERNALDATE` 作为权威接收时间。不得依赖已读/未读状态
或邮件头中的 `Date`。

## AI 与时间解析

AI 输出是不可信的结构化提取结果，必须使用 Pydantic 验证。不得把 AI 生成的最终 datetime
作为权威值。小时、自然日、中国工作日、明确日期、星期表达、跨年保护和仅用于展示的默认
时长均由 Python 确定性解析。所有时间统一为 `Asia/Shanghai`。

模糊表达、“尽快”、不受支持年份的节假日以及只有日期的面试，都必须进入人工确认。
工作日从邮件接收后的次日开始计算，并保留邮件接收时刻。只有日期的 deadline 解析为
23:59，同时标记为推算时间。

## 飞书与 Calendar 不变量

运行时代码只验证飞书 schema，不得创建、删除或修改表和字段。生产权限不包含删除或
schema 修改能力。

Calendar discovery 要求 `ICLOUD_CALENDAR_NAME` 恰好匹配一个日历；不得自动创建日历，
也不得在多个同名日历之间猜测。每个 Calendar UID 和资源名称由飞书 `record_id` 派生；
事项改期时必须更新同一个 UID。仅 Calendar 写入失败时，应持久化补偿状态，并且可以允许
邮件游标继续推进。淘汰或流程结束邮件不得自动删除事件或把任务标记为完成。

Calendar 事件统一使用 `Asia/Shanghai`、提前 24 小时的 DISPLAY alarm，以及确定性的展示
时长：没有结束时间的固定事项为 60 分钟，deadline 为 15 分钟。备注必须明确说明仅用于
展示的结束时间并非来自邮件原文。

## 失败与安全边界

只有在 IMAP、AI、飞书持久化和 SMTP 报告全部成功后，才推进完整成功游标。身份验证、
schema、歧义和数据验证错误都按保守失败处理；rate limit 和临时 5xx 响应使用有界指数退避
重试。

不得记录或提交邮件正文、凭据、Token、私人 Wiki/Base URL 或真实邮件 fixture。GitHub
Actions 权限保持 `contents: read`，checkout 禁止持久化凭据，Action 必须固定到不可变
commit SHA，运行配置只保存在本地 `.env` 或 Actions Secrets。
