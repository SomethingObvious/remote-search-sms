"""The remote-search command: ask, run and check."""

import argparse
import logging
import sys

from .config import load_config, setting

logger = logging.getLogger("remotesearch")


def positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"has to be 1 or more, not {value}")
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="remote-search",
        description="Answer questions texted from a phone that has signal but no data.",
    )
    parser.add_argument("--config", default="config.txt", help="the config file, config.txt here")
    parser.add_argument("--verbose", action="store_true", help="log debug detail")
    # The same two after the action. SUPPRESS keeps a subparser's defaults from
    # overwriting what was given before it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS, help="the config file")
    common.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="action", metavar="{ask,check,run}", required=True)

    ask = sub.add_parser(
        "ask", parents=[common], help="answer one question here, no accounts needed"
    )
    ask.add_argument("question", nargs="+")
    ask.add_argument("--chars", type=positive, help="characters per text, MAX_SMS_CHARS if not set")

    check = sub.add_parser(
        "check", parents=[common], help="try every account and key in the config"
    )
    check.add_argument("--text", action="store_true", help="also send a test text to PHONE_TO")

    run = sub.add_parser("run", parents=[common], help="answer texts and mail until Ctrl-C")
    run.add_argument("--once", action="store_true", help="answer what's waiting and exit, for cron")
    run.add_argument("--dry-run", action="store_true", help="log replies instead of sending them")
    run.add_argument(
        "--catch-up", action="store_true", help="also answer what was waiting at start"
    )
    return parser.parse_args(argv)


def ask(config: dict[str, str], question: str, chars: int | None) -> None:
    from .reply import DEFAULT_SMS_CHARS, Answerer

    limit = chars or setting(config, "MAX_SMS_CHARS", DEFAULT_SMS_CHARS)
    print("\n\n".join(Answerer(config, limit).pages(question)))


def check(config: dict[str, str], text: bool) -> int:
    from .checks import run_all, send_test_text

    results = run_all(config)
    failed = any(r.ok is False for r in results)
    for result in results:
        mark = {True: "ok  ", False: "FAIL", None: "--  "}[result.ok]
        print(f"{mark} {result.name}: {result.detail}")
    if text:
        try:
            print(f"ok   Test text: sent to {send_test_text(config)}")
        except ValueError as exc:
            print(f"FAIL Test text: {exc}")
            failed = True
    return 1 if failed else 0


def run(config: dict[str, str], args: argparse.Namespace) -> None:
    from .mail import LoginError, MailboxError
    from .poller import Poller

    poller: Poller | None = None
    try:
        # A --once run from cron would otherwise set aside everything waiting, every time.
        catch_up = args.catch_up or args.once
        poller = Poller(config, source=args.config, dry_run=args.dry_run, catch_up=catch_up)
        if args.once:
            count = poller.poll()
            logger.info("Sent %d %s", count, "reply" if count == 1 else "replies")
        else:
            poller.run()
    except LoginError as exc:
        raise SystemExit(f"{exc}. Check MAIL_PASSWORD, which has to be an app password.") from None
    except MailboxError as exc:
        raise SystemExit(f"{exc}.") from None
    except KeyboardInterrupt:
        pass  # Ctrl-C is how it's meant to be stopped
    finally:
        if poller:
            poller.close()


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    # Only this package logs at INFO. Twilio's client logs every request at INFO,
    # account SID included.
    logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s")
    logger.setLevel(logging.DEBUG if args.verbose else logging.INFO)
    config = load_config(args.config)

    if args.action == "ask":
        ask(config, " ".join(args.question), args.chars)
    elif args.action == "check":
        sys.exit(check(config, args.text))
    elif args.action == "run":
        run(config, args)
