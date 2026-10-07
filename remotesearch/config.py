"""Settings from config.txt and the environment, the sender allowlist, and saving changes."""

import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Setting:
    key: str
    section: str
    label: str
    hint: str
    secret: bool = False


# Every setting there is, in the order the setup page and config.example.txt show them.
SETTINGS = (
    Setting(
        "ALLOWED_SENDERS",
        "Who Can Ask",
        "Allowed senders",
        "Phone numbers like +16045551234, email addresses, or a carrier's gateway domain "
        "like txt.bell.ca, separated by commas. Nobody else gets an answer.",
    ),
    Setting(
        "TWILIO_ACCOUNT_SID", "Twilio", "Account SID", "Starts with AC, on the Twilio console."
    ),
    Setting("TWILIO_AUTH_TOKEN", "Twilio", "Auth token", "Next to the SID.", secret=True),
    Setting("TWILIO_PHONE_FROM", "Twilio", "Twilio number", "The number people text, as +1..."),
    Setting(
        "PHONE_TO",
        "Twilio",
        "Your phone",
        "Where answers to gateway mail are texted, and where the start-up text goes.",
    ),
    Setting("MAIL_ADDRESS", "Email", "Address", "The mailbox it reads, like you@gmail.com."),
    Setting(
        "MAIL_PASSWORD",
        "Email",
        "App password",
        "An app password, not your normal one. Gmail and iCloud make them in account security.",
        secret=True,
    ),
    Setting("MAIL_FOLDER", "Email", "Folder", "The folder or Gmail label to read. INBOX if empty."),
    Setting(
        "IMAP_HOST", "Email", "IMAP server", "Only needed outside Gmail, iCloud, Fastmail or Yahoo."
    ),
    Setting("SMTP_HOST", "Email", "SMTP server", "As host:port, like smtp.example.com:587."),
    Setting(
        "REPLY_BY_SMS",
        "Email",
        "Answer by text",
        "Addresses or domains whose mail gets its answer as a text. Gateway numbers always do.",
    ),
    Setting("AI_API_KEY", "AI Answers", "API key", "Its prefix picks the provider.", secret=True),
    Setting(
        "AI_PROVIDER",
        "AI Answers",
        "Provider",
        "anthropic, openai, openrouter, groq, google or xai.",
    ),
    Setting("AI_MODEL", "AI Answers", "Model", "Leave empty for a small, cheap default."),
    Setting(
        "AI_BASE_URL",
        "AI Answers",
        "Base URL",
        "For a local server, like http://localhost:11434/v1.",
    ),
    Setting(
        "BRAVE_API_KEY",
        "Search",
        "Brave Search key",
        "Asked before the free engines, which block automated searches now and then.",
        secret=True,
    ),
    Setting(
        "NEWS_REGION", "Search", "News edition", "A country code like CA, or CA:fr for French."
    ),
    Setting(
        "MAX_SMS_CHARS", "Limits", "Characters per text", "300 by default. 160 for an inReach."
    ),
    Setting("MAX_REPLIES_PER_POLL", "Limits", "Replies per poll", "10 by default."),
    Setting(
        "MAX_REPLIES_PER_HOUR", "Limits", "Replies per hour", "30 by default, kept across restarts."
    ),
    Setting("POLL_INTERVAL", "Limits", "Seconds between checks", "5 by default."),
    Setting("STATE_FILE", "Limits", "State file", "remote-search-state.json by default."),
)
KEYS = tuple(s.key for s in SETTINGS)
TWILIO_KEYS = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_FROM")
MAIL_KEYS = ("MAIL_ADDRESS", "MAIL_PASSWORD")

# Where config.txt is, so relative paths in it don't depend on the folder it's run from.
CONFIG_DIR = "_CONFIG_DIR"

# E.164, which is how Twilio reports every number, as in +15551234567.
PHONE = re.compile(r"\+[1-9]\d{6,14}")


def load_config(path: str | Path) -> dict[str, str]:
    """Read ``KEY=value`` lines from ``path`` if it exists, with environment variables winning."""
    config: dict[str, str] = {}
    file = Path(path)
    if file.exists():
        for raw in file.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                config[key.strip()] = value.strip()
    for key in KEYS:
        if os.environ.get(key):
            config[key] = os.environ[key]
    config[CONFIG_DIR] = str(file.resolve().parent)
    return config


def beside_config(config: dict[str, str], key: str, default: str) -> Path:
    """A path setting, read from the folder config.txt is in when it's relative."""
    path = Path(config.get(key) or default)
    return path if path.is_absolute() else Path(config.get(CONFIG_DIR, ".")) / path


def save_config(path: str | Path, changes: dict[str, str]) -> None:
    """Write ``changes`` into the file, keeping its comments and the order of its lines."""
    file = Path(path)
    lines = file.read_text(encoding="utf-8-sig").splitlines() if file.exists() else []
    left = dict(changes)
    # The last copy of a key is the one load_config ends up with, so that's the one edited.
    for n, raw in reversed(list(enumerate(lines))):
        key = raw.split("=", 1)[0].strip()
        if "=" in raw and not raw.lstrip().startswith("#") and key in left:
            lines[n] = f"{key}={left.pop(key)}"
    lines += [f"{key}={value}" for key, value in left.items()]
    # Written beside the real file and swapped in, as it holds the only copy of the secrets.
    temp = file.with_name(file.name + ".tmp")
    temp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temp.replace(file)


def require(config: dict[str, str], keys: tuple[str, ...], path: str | Path) -> None:
    missing = [k for k in keys if not config.get(k)]
    if missing:
        raise SystemExit(
            f"Neither {path} nor the environment sets {', '.join(missing)}. "
            "config.example.txt shows what each one is."
        )


def setting(config: dict[str, str], key: str, default: int) -> int:
    """Read a config value that has to be a whole number of 1 or more."""
    raw = config.get(key) or str(default)
    if not raw.isdecimal() or int(raw) < 1:
        raise SystemExit(f"{key} has to be a whole number of 1 or more, not {raw!r}.")
    return int(raw)


def word_list(config: dict[str, str], key: str) -> frozenset[str]:
    """A comma-separated setting as lowercase words."""
    return frozenset(w.strip().lower() for w in config.get(key, "").split(",") if w.strip())


def phone_number(text: str) -> str | None:
    """Return ``text`` as a bare E.164 number, or None when it isn't one."""
    number = re.sub(r"[\s().-]", "", text)
    return number if PHONE.fullmatch(number) else None


def sender_list(config: dict[str, str], key: str = "ALLOWED_SENDERS") -> set[str]:
    """Parse a comma-separated list of addresses, bare domains and +phone numbers."""
    entries = set()
    for part in config.get(key, "").split(","):
        entry = part.strip().lower().lstrip("@")
        if not entry:
            continue
        if entry.startswith("+"):
            number = phone_number(entry)
            if not number:
                raise SystemExit(
                    f"{key} has {part.strip()!r}, which isn't a phone number in the "
                    "+15551234567 form."
                )
            entry = number
        elif "." not in entry.rpartition("@")[2]:
            raise SystemExit(
                f"{key} has {part.strip()!r}, which isn't an email address, a domain "
                "or a +15551234567 phone number."
            )
        entries.add(entry)
    return entries


def sender_permitted(sender: str, allowed: set[str]) -> bool:
    """Match an email address by itself or its domain, or an E.164 number, against the list."""
    if sender.startswith("+"):
        return sender in allowed
    domain = sender.rpartition("@")[2]
    return sender in allowed or (bool(domain) and domain in allowed)
