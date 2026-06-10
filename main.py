import argparse
import asyncio
import sys
from dataclasses import asdict

from dotenv import load_dotenv

load_dotenv()

from src.runner import run_analysis
from src.utils import load_intermediate, save_intermediate, setup_logging


def load_watchlist(path: str) -> list[str]:
    try:
        with open(path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        print(f"Watchlist file not found: {path}", file=sys.stderr)
        sys.exit(1)

    tickers = []
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if line:
            tickers.append(line.upper())
    return tickers


def parse_args():
    parser = argparse.ArgumentParser(
        description="Stock analysis: technical + fundamental + AI report"
    )
    parser.add_argument(
        "--tickers", nargs="+", default=[],
        help="Stock tickers to analyze, in addition to any --watchlist file "
             "(benchmarks SPY/QQQ/DIA/IWM always included)"
    )
    parser.add_argument(
        "--watchlist", nargs="?", const="watchlist.txt", default=None,
        help="Read core tickers from a file (one per line, '#' for comments). "
             "Defaults to watchlist.txt if no path given."
    )
    parser.add_argument("--output-dir", default="reports", help="Report output directory")
    parser.add_argument("--data-dir", default="data", help="Intermediate JSON directory")
    parser.add_argument(
        "--skip-fetch", action="store_true",
        help="Reuse cached data from --data-dir instead of fetching"
    )
    parser.add_argument("--no-report", action="store_true", help="Skip Gemini report generation")
    parser.add_argument("--no-critique", action="store_true", help="Skip Critique agent")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    return parser.parse_args()


async def async_main():
    args = parse_args()
    logger = setup_logging(args.verbose)

    tickers = []
    if args.watchlist:
        tickers.extend(load_watchlist(args.watchlist))
    tickers.extend(t.upper() for t in args.tickers)

    if not tickers:
        logger.error("No tickers provided. Use --tickers and/or --watchlist.")
        sys.exit(1)

    # Dedupe while preserving order
    tickers = list(dict.fromkeys(tickers))

    # --- Phase 1 & 2: fetch + analyze ---
    if args.skip_fetch:
        logger.info("Loading cached analysis from disk...")
        cached = load_intermediate("analysis_results.json", args.data_dir)
        if cached is None:
            logger.error(f"No cached data found in {args.data_dir}/analysis_results.json")
            sys.exit(1)
        # Reconstruct from JSON for the reporter
        analysis_json = cached
    else:
        analysis = await run_analysis(tickers)
        analysis_json = {
            "tickers": analysis.tickers,
            "technical": [asdict(t) for t in analysis.technical],
            "fundamental": [asdict(f) for f in analysis.fundamental],
        }
        path = save_intermediate(analysis_json, "analysis_results.json", args.data_dir)
        logger.info(f"Analysis saved to {path}")

    if args.no_report:
        logger.info("Skipping report generation (--no-report).")
        return

    # --- Phase 3: generate reports ---
    from src.reporter_gemini import CritiqueAgent, ReportingAgent

    logger.info("Generating main report via Gemini...")
    reporter = ReportingAgent()
    report_md = reporter.generate(analysis_json)
    paths = reporter.save(report_md, args.output_dir, analysis_json)
    print(f"\nMain report saved:\n  Markdown : {paths['md']}\n  HTML     : {paths['html']}")

    if not args.no_critique:
        logger.info("Generating critique via Gemini...")
        critic = CritiqueAgent()
        critique_md = critic.generate(report_md, analysis_json)
        cpaths = critic.save(critique_md, args.output_dir)
        print(f"\nCritique report saved:\n  Markdown : {cpaths['md']}\n  HTML     : {cpaths['html']}")


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
