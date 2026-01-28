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
CONFIG_PATH = Path(__file__).parent / "config.sh"

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

    # Load existing classifications from multiple sources
    existing_classifications = {}

    def add_classifications(tweets_list, source_name):
        count = 0
        for t in tweets_list:
            if '_skip' in t:
                key = (t.get('handle') or '') + (t.get('text') or '')[:50]
                if key not in existing_classifications:
                    existing_classifications[key] = {
                        '_skip': t.get('_skip'),
                        '_skip_reason': t.get('_skip_reason'),
                        '_quality': t.get('_quality'),
                        '_topic': t.get('_topic'),
                        '_summary': t.get('_summary'),
                    }
                    count += 1
        if count > 0:
            print(f"Loaded {count} classifications from {source_name}")

    # 1. Load from local filtered.json
    if OUTPUT_PATH.exists():
        try:
            with open(OUTPUT_PATH) as f:
                add_classifications(json.load(f), "filtered.json")
        except Exception as e:
            print(f"Could not load filtered.json: {e}")

    # 2. Also fetch from gist (the accumulated source of truth)
    gist_id = None
    github_username = None
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                for line in f:
                    if line.startswith('GIST_ID='):
                        gist_id = line.split('=', 1)[1].strip().strip('"\'')
                    elif line.startswith('GITHUB_USERNAME='):
                        github_username = line.split('=', 1)[1].strip().strip('"\'')
        except:
            pass

    if gist_id and github_username:
        try:
            import urllib.request
            gist_url = f"https://gist.githubusercontent.com/{github_username}/{gist_id}/raw/collected.json"
            with urllib.request.urlopen(gist_url, timeout=10) as resp:
                gist_data = json.loads(resp.read().decode())
                add_classifications(gist_data, "gist")
        except Exception as e:
            print(f"Could not fetch gist: {e}")

    print(f"Total existing classifications: {len(existing_classifications)}")

    # Apply existing classifications to tweets
    for t in tweets:
        key = (t.get('handle') or '') + (t.get('text') or '')[:50]
        if key in existing_classifications and '_skip' not in t:
            t.update(existing_classifications[key])

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

    # Separate already-classified tweets from new ones
    already_classified = [t for t in tweets if '_skip' in t]
    needs_classification = [t for t in tweets if '_skip' not in t]

    print(f"Found {len(already_classified)} already classified, {len(needs_classification)} new tweets")

    if not needs_classification:
        print("No new tweets to classify!")
        # Still output the file with existing classifications
        with open(OUTPUT_PATH, 'w') as f:
            json.dump(tweets, f, indent=2)
        kept_count = len([t for t in tweets if not t.get('_skip', False)])
        print(f"Total: {kept_count}/{len(tweets)} kept")
        return

    print(f"Filtering {len(needs_classification)} new tweets (5 parallel)...")

    # Prepare args for parallel processing (only new tweets)
    args_list = [(client, tweet, i, len(needs_classification)) for i, tweet in enumerate(needs_classification)]

    # Process in parallel with 5 workers
    new_results = [None] * len(needs_classification)
    kept_count = 0
    completed = 0

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(process_tweet, args): args[2] for args in args_list}

        for future in as_completed(futures):
            index, tweet, skip, quality, handle = future.result()
            new_results[index] = tweet
            completed += 1

            status = 'SKIP' if skip else 'KEEP'
            print(f"[{completed}/{len(needs_classification)}] {status} | {quality:6} | @{handle}")

            if not skip:
                kept_count += 1

    # Combine: newly classified + already classified
    all_results = new_results + already_classified

    # Sort by timestamp (newest first)
    all_results.sort(key=lambda t: t.get('ts', 0), reverse=True)

    # Save all tweets with classification data
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(all_results, f, indent=2)

    total_kept = len([t for t in all_results if not t.get('_skip', False)])

    print(f"\nDone! Classified {kept_count}/{len(needs_classification)} new tweets as kept")
    print(f"Total: {total_kept}/{len(all_results)} kept")
    print(f"Saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
