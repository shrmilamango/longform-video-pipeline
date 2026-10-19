"""Topic research for long-form space sleep documentaries.

  py sog.py research       find what's winning right now (needs youtube_api_key in config.yaml)
  py sog.py ideas          show the numbered topic ideas
  py sog.py telegram-id    find your Telegram chat id for phone alerts (optional)
"""
import argparse
import sys

from sog import research

for _stream in (sys.stdout, sys.stderr):  # never crash on unusual characters in Windows logs
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass


def main():
    ap = argparse.ArgumentParser(description="Topic research", usage=__doc__)
    sub = ap.add_subparsers(dest="cmd")
    for name in ("research", "ideas", "telegram-id"):
        sub.add_parser(name)
    a = ap.parse_args()

    if a.cmd == "research":
        try:
            research.run_research()
        except RuntimeError as e:
            sys.exit(f"Research stopped: {e}")
    elif a.cmd == "ideas":
        ideas = research.list_ideas()
        if not ideas:
            print("No ideas yet. Run research, paste data/research/topics_prompt.md into your AI assistant,\n"
                  "and save the numbered answer as data/research/ideas.md")
        for i in ideas:
            print(f"{i['n']:>2}. {i['line']}")
    elif a.cmd == "telegram-id":
        from sog.notify import print_chat_id
        print_chat_id()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
