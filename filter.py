#!/usr/bin/env python3
"""
Filter collected tweets using Claude Haiku.
Reads from collected.json, outputs filtered.json with only quality tweets.
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from anthropic import Anthropic, RateLimitError

DATA_DIR = Path(__file__).parent / "data"
INPUT_PATH = DATA_DIR / "collected.json"
OUTPUT_PATH = DATA_DIR / "filtered.json"

CLASSIFICATION_PROMPT = """Classify this tweet for a technical reader. Return JSON only.

SKIP (skip: true) if: engagement bait, SaaS spam, generic AI hype, pile-on takes, crypto/web3, viral RT with no context, personal life updates, jokes without substance
KEEP (skip: false) if: articles, papers, GitHub links, researcher insights, technical content, novel analysis, interesting news, linked interviews, meditation or personal insights

Author: @{handle} ({name})
Content: {text}
Links: {links}

Return ONLY valid JSON: {{"skip": bool, "skip_reason": "reason if skipping", "quality": "high"/"medium"/"low", "topic": "short_slug", "summary": "one sentence"}}"""


def classify_tweet(client: Anthropic, tweet: dict, max_retries: int = 3) -> dict:
    """Classify a single tweet with retry logic for rate limits."""
    prompt = CLASSIFICATION_PROMPT.format(
        handle=tweet.get('handle', ''),
        name=tweet.get('name', ''),
        text=(tweet.get('text', '') or '')[:800],
        links=', '.join(tweet.get('links', [])[:3]) if tweet.get('links') else 'none',
    )

    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}]
            )

            result_text = response.content[0].text.strip()

            # Extract JSON from response
            json_match = re.search(r'\{[\s\S]*\}', result_text)
            if json_match:
                result_text = json_match.group(0)

            return json.loads(result_text)

        except RateLimitError as e:
            wait_time = 2 ** attempt  # 1s, 2s, 4s
            print(f"  Rate limited, waiting {wait_time}s...")
            time.sleep(wait_time)
            if attempt == max_retries - 1:
                print(f"  Error after retries: {e}")
                return {"skip": False, "quality": "medium", "topic": "unknown", "summary": ""}

        except Exception as e:
            print(f"  Error: {e}")
            return {"skip": False, "quality": "medium", "topic": "unknown", "summary": ""}


def process_tweet(args):
    """Process a single tweet - used for parallel execution."""
    client, tweet, index, total = args
    handle = tweet.get('handle', '')[:15]

    classification = classify_tweet(client, tweet)

    skip = classification.get('skip', False)
    quality = classification.get('quality', 'medium')
    topic = classification.get('topic', '')
    summary = classification.get('summary', '')

    # Add classification info to tweet
    tweet['_skip'] = skip
    tweet['_skip_reason'] = classification.get('skip_reason', '') if skip else None
    tweet['_quality'] = quality
    tweet['_topic'] = topic
    tweet['_summary'] = summary

    return index, tweet, skip, quality, handle


def main():
    if not os.environ.get('ANTHROPIC_API_KEY'):
        print("Error: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    if not INPUT_PATH.exists():
        print(f"Error: {INPUT_PATH} not found")
        sys.exit(1)

    client = Anthropic()

    with open(INPUT_PATH) as f:
        tweets = json.load(f)

    # Deduplicate by handle + first 50 chars
    seen = set()
    unique_tweets = []
    for t in tweets:
        key = (t.get('handle', '') or '') + (t.get('text', '') or '')[:50]
        if key not in seen:
            seen.add(key)
            unique_tweets.append(t)

    if len(unique_tweets) < len(tweets):
        print(f"Deduplicated: {len(tweets)} -> {len(unique_tweets)} tweets")
    tweets = unique_tweets

    print(f"Filtering {len(tweets)} tweets (5 parallel)...")

    # Prepare args for parallel processing
    args_list = [(client, tweet, i, len(tweets)) for i, tweet in enumerate(tweets)]

    # Process in parallel with 10 workers
    results = [None] * len(tweets)
    kept_count = 0
    completed = 0

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(process_tweet, args): args[2] for args in args_list}

        for future in as_completed(futures):
            index, tweet, skip, quality, handle = future.result()
            results[index] = tweet
            completed += 1

            status = 'SKIP' if skip else 'KEEP'
            print(f"[{completed}/{len(tweets)}] {status} | {quality:6} | @{handle}")

            if not skip:
                kept_count += 1

    # Save all tweets with classification data (reader will separate kept/skipped)
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\nDone! Kept {kept_count}/{len(tweets)} tweets")
    print(f"Saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
