from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

from .app import retry_calendars, run
from .config import Config, ConfigError
from .deadlines import SHANGHAI
from .feishu import FeishuError
from .mailbox import QQMailbox


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m job_mail_assistant")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="process recent recruiting emails")
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="read and parse without writing Base/Calendar or sending a report",
    )
    subparsers.add_parser(
        "retry-calendar",
        help="retry pending/failed iCloud events without reading mail or sending a report",
    )
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = build_parser().parse_args()
    dry_run = getattr(args, "dry_run", False)
    calendar_only = args.command == "retry-calendar"
    try:
        config = Config.from_env()
        if calendar_only:
            return retry_calendars(config)
        return run(config, dry_run=dry_run)
    except ConfigError as exc:
        logging.error("Configuration error: %s", exc)
        return 2
    except FeishuError as exc:
        logging.error("Run failed: %s", exc)
        if dry_run or calendar_only:
            return 1
        try:
            date_text = datetime.now(SHANGHAI).strftime("%Y-%m-%d")
            QQMailbox(config.qq_email, config.qq_auth_code).send_report(
                f"【秋招早报】{date_text} 运行失败",
                "Job Mail Assistant 本次运行失败。\n"
                "失败阶段异常类型：FeishuError\n"
                "未确认完成的邮件会在下次运行时从上次成功运行起回扫，"
                "请检查 GitHub Actions 日志。\n",
            )
        except Exception as report_exc:
            logging.error("Failure report could not be sent: %s", type(report_exc).__name__)
        return 1
    except Exception as exc:
        logging.error("Run failed: %s", type(exc).__name__)
        if dry_run or calendar_only:
            return 1
        try:
            date_text = datetime.now(SHANGHAI).strftime("%Y-%m-%d")
            QQMailbox(config.qq_email, config.qq_auth_code).send_report(
                f"【秋招早报】{date_text} 运行失败",
                "Job Mail Assistant 本次运行失败。\n"
                f"失败阶段异常类型：{type(exc).__name__}\n"
                "未确认完成的邮件会在下次运行时从上次成功运行起回扫，"
                "请检查 GitHub Actions 日志。\n",
            )
        except Exception as report_exc:
            logging.error("Failure report could not be sent: %s", type(report_exc).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
