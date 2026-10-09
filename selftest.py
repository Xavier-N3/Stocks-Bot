"""Checks every connection and tells you exactly what's working.  python selftest.py"""
import time

OK, BAD, MEH = "[ OK ]", "[FAIL]", "[ -- ]"


def main():
    from trader import config, llm, web
    print("\n=== AI trading firm self-test ===\n")
    try:
        config.validate()
        print(OK, "settings loaded -", "PAPER trading" if config.ALPACA_PAPER else "LIVE MONEY")
    except SystemExit as e:
        print(BAD, e)
        return

    from trader.broker import Broker
    from trader.marketdata import MarketData
    try:
        a = Broker().account()
        print(OK, f"Alpaca account: equity ${a['equity']:,.2f}, cash ${a['cash']:,.2f}")
    except Exception as e:
        print(BAD, "Alpaca account:", e)
        return
    try:
        s = MarketData().snapshots(["SPY"])
        print(OK, f"Alpaca market data: SPY ${s['SPY']['price']:.2f}")
    except Exception as e:
        print(BAD, "Alpaca market data:", e)
    n = len(web.google_news("stock market", 3))
    print(OK if n else BAD, f"Web news (Google News RSS): {n} headlines")
    f = web.sec_fundamentals("320193")
    print(OK if f else BAD, "SEC company financials:", "Apple revenue growth " + str(f.get("revenue_growth")) if f
          else "blocked - set SEC_USER_AGENT in .env to include your email")

    print("\nAI models (each one tested with a tiny request):")
    seen, working = set(), 0
    for tier in ("smart", "fast"):
        for prov, model in llm.CHAINS[tier]:
            if (prov, model) in seen:
                continue
            seen.add((prov, model))
            p = llm.PROVIDERS[prov]
            if not p["key"]:
                print(MEH, f"{prov}:{model} - no key in .env (optional)")
                continue
            llm.CHAINS["_t"] = [(prov, model)]
            t = time.time()
            r = llm.chat("Reply with JSON only.", 'Return {"ok": true}', tier="_t", max_tokens=20, cache_min=0)
            if r:
                working += 1
                print(OK, f"{prov}:{model} answered in {time.time() - t:.1f}s")
            else:
                print(BAD, f"{prov}:{model} did not answer (bad key, retired model, or offline) - see logs/system.log")
    print()
    if working == 0:
        print("No AI model works yet. The system still trades on numbers alone, but add a free Groq key "
              "(https://console.groq.com/keys) to .env for the AI analysts.")
    else:
        print(f"{working} AI model(s) working. Start everything with ./start.ps1")


if __name__ == "__main__":
    main()
