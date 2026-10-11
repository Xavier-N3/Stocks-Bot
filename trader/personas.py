"""Who's who in the Studio: names, roles, colors and personalities."""

AGENTS = {
    "day_researcher": dict(name="Scout", role="Day-trading researcher", desk="day", kind="researcher",
                           color="#3987e5", badge="🔍",
                           bio="Quick, curious and caffeinated. Lives on the news wire and today's top movers."),
    "day_trader": dict(name="Dash", role="Day trader", desk="day", kind="trader", color="#3987e5", badge="⚡",
                       bio="Fast and decisive, but disciplined: every position is closed by 3:50pm New York time."),
    "swing_researcher": dict(name="Sage", role="Swing-trading researcher", desk="swing", kind="researcher",
                             color="#d95926", badge="🔍",
                             bio="Patient chart reader who thinks in weeks and loves a clean trend."),
    "swing_trader": dict(name="Swift", role="Swing trader", desk="swing", kind="trader", color="#d95926",
                         badge="📈", bio="Calm and methodical; holds trades for days and lets the stops do their job."),
    "long_researcher": dict(name="Atlas", role="Long-term researcher", desk="long", kind="researcher",
                            color="#199e70", badge="📚",
                            bio="Reads SEC filings for fun. Cares about revenue growth, margins and debt."),
    "long_trader": dict(name="Oakley", role="Long-term investor", desk="long", kind="trader", color="#199e70",
                        badge="🌳", bio="Unhurried and steady; thinks in months and rebalances once a week."),
    "crypto_researcher": dict(name="Nova", role="Crypto researcher", desk="crypto", kind="researcher",
                              color="#e66767", badge="🪙",
                              bio="Night owl who never sleeps; watches Bitcoin, Ethereum, Solana and XRP around the clock."),
    "crypto_trader": dict(name="Satoshi", role="Crypto trader", desk="crypto", kind="trader", color="#e66767",
                          badge="₿", bio="The most cautious bot in the studio: only trades crypto when every check "
                                        "agrees with high confidence, and happily sits out otherwise."),
    "forex_researcher": dict(name="Sterling", role="Forex researcher", desk="forex", kind="researcher",
                             color="#22a6c7", badge="💱",
                             bio="Globetrotter who follows central banks, inflation reports and the US dollar from "
                                 "Tokyo's open to New York's close."),
    "forex_trader": dict(name="Pip", role="Forex trader", desk="forex", kind="trader", color="#22a6c7", badge="🌍",
                         bio="Precise and patient. Trades currency pairs both ways, only when every check agrees, "
                             "and is always flat before the weekend."),
    "challenger": dict(name="Devi", role="The Challenger (devil's advocate)", desk=None, kind="challenger",
                       color="#d55181", badge="⚖️",
                       bio="Friendly but relentless skeptic. Argues both sides of every trade before it is placed."),
    "manager": dict(name="Morgan", role="Manager", desk=None, kind="manager", color="#9085e9", badge="📋",
                    bio="Warm, organized and honest. Grades every trade, writes the reports, and moves capital "
                        "toward what works."),
    "lab": dict(name="Darwin", role="Training lab (neuroevolution)", desk=None, kind="lab", color="#c98500",
                badge="🧬", bio="Nerdy scientist who breeds neural-network trading brains and only promotes ones "
                              "that win on data they've never seen."),
}
