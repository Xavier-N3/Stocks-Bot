# Optional features (parked, not active)

Finished features that are deliberately **not** part of the running bot yet. Nothing in this folder is loaded,
imported or run by the firm. Each one has its own folder with instructions for switching it on later.

| feature | folder | why it's parked | what you need to turn it on |
|---|---|---|---|
| **Crypto desk** (Nova + Satoshi, BTC/ETH/SOL/XRP, 24/7) | [`crypto/`](crypto/README.md) | no brain has passed its tests yet, and Alpaca's crypto fees are high | nothing extra: run `crypto\install.ps1` (or ask Claude) |
| **Forex desk** (Sterling + Pip, 6 major currency pairs, long & short, 24/5) | [`forex/`](forex/README.md) | forex moves are small, so it only makes a real difference with a bigger account | install **crypto first** (forex reuses its testing system), then a free OANDA practice key and `forex\install.ps1` (or ask Claude) |
