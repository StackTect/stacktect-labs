# StackTect Labs

The code, setup guides and real results from the **StackTect** YouTube videos.
Every lab runs on one laptop, and most need nothing but Python.

Each video gets its own folder:

```
videos/<date>-<topic>/
├── README.md   setup guide: what you build, how to run it, what to look for
├── code/       everything you need to run the lab yourself
└── results/    what we measured when we ran it for the video
```

## Labs

| Date | Video | Lab | Needs |
|---|---|---|---|
| 2026-11-04 | Load Balancer: What Happens When a Server Dies? *(link added after upload)* | [load-balancer-server-dies](videos/2026-11-04-load-balancer-server-dies/) | Python 3.8+ |

## About the numbers
- Numbers in `results/` come from real runs on our machine. They are listed with the date, the Python version and the settings that were used.
- Your numbers will be a bit different on your machine. What should stay the same is the pattern (which version fails and which doesn't).
- If a video uses an example number that we didn't measure, the video description says it is illustrative, and it does not go in `results/`.
- The code is written to teach, so keep it short and readable. It is **not production code**.

## License
- Code (`code/` folders): [MIT](LICENSE)
- Guides and results (README files, `results/`): [CC BY 4.0](LICENSE-docs). You can reuse them if you credit StackTect.

Found a bug or got different numbers? Open an issue and tell us your OS, your Python version and what you saw.
