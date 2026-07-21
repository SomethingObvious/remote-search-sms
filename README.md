# Remote Search over SMS

Search the web from a phone that can text but has no data. A computer at home watches for questions, looks each one up in free web sources and texts the answer back. I built it for trips with a bar of signal or a satellite and no data, so `tide Tofino` from a trailhead comes back as a normal SMS.

## Commands

The first word picks the source and the rest is the question. Anything else is a web search.

| Text | Answer |
| --- | --- |
| `news [topic]` | Google News headlines |
| `define`, `wiki`, `so` | Wiktionary, Wikipedia, Stack Overflow |
| `more`, `help`, `help <word>` | the rest of a long answer, the command list, one command's details |

The news comes from the Google News edition in `NEWS_REGION`, which is a country code like `GB` for the English edition there, or `CA:fr` for the French one in Canada. It's `CA` unless you set it. Replies are folded into GSM-7 and cut into 300-character pages, since one character outside GSM-7 bills a reply at more than twice as many segments.

It says so when it doesn't know. A result only counts when it overlaps what you asked, and the reply names its source, so a near miss is easy to spot.

Web searches go through the ddgs package, which scrapes DuckDuckGo and moves on to the other engines it supports while DuckDuckGo is blocking.

## Setup

It needs Python 3.11 or newer.

```
python -m venv .venv
.venv/bin/pip install -e .        # .venv\Scripts\pip on Windows
```

Copy `config.example.txt` to `config.txt` and fill it in, or set the same names as environment variables, which win over the file.

```
remote-search ask "tide Tofino"   # one answer in the terminal, no accounts needed
```

```
python -m pip install -e ".[dev]"
python -m pytest                  # offline tests
```

## License

MIT, see [LICENSE](LICENSE).
