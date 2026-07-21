"""The remote-search command: ask."""

import argparse
import logging

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
    sub = parser.add_subparsers(dest="action", metavar="{ask}", required=True)

    ask = sub.add_parser(
        "ask", parents=[common], help="answer one question here, no accounts needed"
    )
    ask.add_argument("question", nargs="+")
    ask.add_argument("--chars", type=positive, help="characters per text, MAX_SMS_CHARS if not set")

    return parser.parse_args(argv)


def ask(config: dict[str, str], question: str, chars: int | None) -> None:
    from .reply import DEFAULT_SMS_CHARS, Answerer

    limit = chars or setting(config, "MAX_SMS_CHARS", DEFAULT_SMS_CHARS)
    print("\n\n".join(Answerer(config, limit).pages(question)))


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s")
    logger.setLevel(logging.DEBUG if args.verbose else logging.INFO)
    config = load_config(args.config)

    if args.action == "ask":
        ask(config, " ".join(args.question), args.chars)
