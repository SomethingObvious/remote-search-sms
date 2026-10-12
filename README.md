# Remote Search over SMS

Search the web from a phone that can text but has no data. A computer at home watches for questions, looks each one up in free web sources and texts the answer back. I built it for trips with a bar of signal or a satellite and no data, so `tide Tofino` from a trailhead comes back as a normal SMS.

## Ways to Reach It

Texting your Twilio number is the simplest. The computer polls Twilio's API for new texts, so there's no webhook or public address to set up, and listing your number in `ALLOWED_SENDERS` turns it on. That works from anything that sends ordinary SMS:

- An iPhone 14 or later on iOS 18, through Messages via satellite in Canada and the US. It sends SMS to non-Apple numbers like a Twilio one when your carrier supports SMS by satellite, and replies come back the same way, though you need to be outside with a clear view of the sky.
- Rogers Satellite in Canada or T-Mobile's T-Satellite in the US, which carry plain SMS to any number.
- A Garmin inReach, texting the Twilio number as it would any phone, and the answer comes back to the device as a normal reply. The inReach has no number of its own though, and Garmin's forums say its texts go out from whichever Garmin number the gateway picks, so it can change between messages. Each of those has to be in `ALLOWED_SENDERS`, and a text from one that isn't gets ignored, with its number in the log.

It also reads an email inbox over IMAP, logging in with an app password. Gmail, iCloud, Fastmail and Yahoo work with just the address and password, and any other provider needs `IMAP_HOST` and `SMTP_HOST`. A text to that address through your carrier's SMS-to-email gateway (from something like `6045551234@txt.bell.ca`) gets its answer texted to `PHONE_TO` when Twilio is set up, or emailed back to the gateway when it isn't, and plain email from an allowed address gets an email back in the same thread. It only opens mail from the allowed senders and leaves everything else unread, though a separate address for this is still the simplest. Several carriers have closed their gateways (Rogers in 2023, AT&T in 2025), so that part depends on yours. Don't send inReach messages to the email address, as they come from `no.reply.inreach@garmin.com` and the only way back is a form on Garmin's site, so it skips them. Text the Twilio number from the inReach instead.

## Commands

The first word picks the source and the rest is the question. Anything else is a web search.

| Text | Answer |
| --- | --- |
| `weather <place>`, `forecast <place>` | Open-Meteo, now or over 3 days, plus Environment Canada alerts |
| `sun <place>`, `time <place>` | sunrise and sunset, or the local time |
| `tide <place>` | next highs and lows at the nearest Canadian Hydrographic Service station |
| `avy <place>` | Avalanche Canada danger ratings and problems |
| `road <highway or BC town>` | DriveBC closures and events, like `road coquihalla` |
| `drive <place> to <place>` | distance and time from OSRM, with no traffic |
| `calc`, `convert`, `translate` | arithmetic, units and currency (ECB rates), MyMemory translation |
| `news [topic]`, `score <team>` | Google News headlines, ESPN scores |
| `hours <business> <town>` | opening hours, address and phone from OpenStreetMap |
| `define`, `wiki`, `so` | Wiktionary, Wikipedia, Stack Overflow |
| `reddit`, `quora`, `youtube`, `site <domain>` | a web search of that one site |
| `more`, `help`, `help <word>` | the rest of a long answer, the command list, one command's details |

The news comes from the Google News edition in `NEWS_REGION`, which is a country code like `GB` for the English edition there, or `CA:fr` for the French one in Canada. It's `CA` unless you set it. Reddit's API is closed to personal apps and Quora has none, so those answers are the top search result's title and snippet rather than the thread itself. Replies are folded into GSM-7 and cut into 300-character pages, since one character outside GSM-7 bills a reply at more than twice as many segments.

It says so when it doesn't know. A result only counts when it overlaps what you asked, and the reply names its source, so a near miss is easy to spot.

With `AI_API_KEY` set, a model writes one short answer from the search results and says "I don't know" when they don't cover it. The key's prefix picks Anthropic, OpenAI, OpenRouter, Groq, Google or xAI, and `AI_BASE_URL` with `AI_MODEL` points it at any OpenAI-compatible server, like a local Ollama. The results are handed to it as untrusted text, and whatever it writes only goes back to whoever asked.

Web searches go through the ddgs package, which scrapes DuckDuckGo and moves on to the other engines it supports while DuckDuckGo is blocking. With `BRAVE_API_KEY` set, Brave's search API gets asked before any of them, and it's the dependable option as it's a real API rather than a scraped page.

## Setup

It needs Python 3.11 or newer.

```
python -m venv .venv
.venv/bin/pip install -e .        # .venv\Scripts\pip on Windows
```

Copy `config.example.txt` to `config.txt` and fill it in, or set the same names as environment variables, which win over the file.

```
remote-search ask "tide Tofino"   # one answer in the terminal, no accounts needed
remote-search run                 # answer texts and mail until Ctrl-C
remote-search run --once          # answer what's waiting and exit, for cron
remote-search run --dry-run       # log replies instead of sending them
```

At startup it skips mail that's already waiting, unless you pass `--catch-up`. Texts to the Twilio number that arrive while it's stopped get answered once it's back, and the ones it has handled are kept in `STATE_FILE`. A dry run leaves all of that alone, so mail stays unread and `STATE_FILE` isn't written.

On an inReach, `MAX_SMS_CHARS=160` keeps every reply to one message, which is about what Garmin passes through. A preset message like `weather here` is worth setting up too, since typing on the device is slow.

```
python -m pip install -e ".[dev]"
python -m pytest                  # offline tests
```

## Your Own Commands

Every `.py` file in `plugins` is loaded at start, and `plugins/example.py` shows the whole thing. A command is a function with the `@command("word")` decorator that gets the rest of the text and returns the answer, or `None` to send it on to a web search. Put words in `COMMANDS_OFF` (like `quora,youtube`) to turn built-in ones off, so a text starting with one of them is searched for instead.

## What It Won't Do

It won't start without `ALLOWED_SENDERS`, and it only ever answers the sender or `PHONE_TO`. Every reply is billed, so `MAX_REPLIES_PER_POLL` and `MAX_REPLIES_PER_HOUR` (30 by default, and kept across restarts) cap them, and questions past a cap wait for a later poll. A US Twilio number needs A2P 10DLC registration before it can text US phones, and Canadian numbers bought since March 2025 need registering too. Twilio handles STOP and HELP itself on North American numbers, so a text of just STOP blocks every reply to that phone until it texts START. The scraped engines all block automated searches after a burst now and then, and once every one of them has, the reply says the web search isn't answering. A Brave key is the fix if that keeps happening. The mail check trusts the From header, which can be forged, so the allowed senders are a filter and not a lock.

## License

MIT, see [LICENSE](LICENSE).
