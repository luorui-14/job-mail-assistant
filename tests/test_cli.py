from job_mail_assistant.__main__ import build_parser, main
from job_mail_assistant.feishu import FeishuError


def test_cli_dry_run_flag() -> None:
    args = build_parser().parse_args(["run", "--dry-run"])
    assert args.command == "run"
    assert args.dry_run is True


def test_dry_run_failure_does_not_send_email(monkeypatch) -> None:
    sent = []

    class FakeConfig:
        qq_email = "me@example.com"
        qq_auth_code = "auth-code"

    class FakeMailbox:
        def __init__(self, *_: object) -> None:
            pass

        def send_report(self, *_: object) -> None:
            sent.append(True)

    monkeypatch.setattr("sys.argv", ["job_mail_assistant", "run", "--dry-run"])
    monkeypatch.setattr(
        "job_mail_assistant.__main__.Config.from_env", lambda: FakeConfig()
    )
    monkeypatch.setattr(
        "job_mail_assistant.__main__.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FeishuError("safe details")),
    )
    monkeypatch.setattr("job_mail_assistant.__main__.QQMailbox", FakeMailbox)

    assert main() == 1
    assert sent == []
