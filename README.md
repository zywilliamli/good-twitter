# Twitter Digest

A tool to collect tweets from your Twitter/X feed and filter them using Claude Haiku to remove slop

## Requirements

- **macOS** (uses AppleScript to control Chrome)
- **Google Chrome** browser
- **Python 3.8+**
- **Anthropic API key** (for the filter feature)

## Setup

1. Clone the repository and navigate to the directory:
   ```bash
   cd twitter-digest
   ```

2. Create a virtual environment and install dependencies:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Create a `.env` file with your Anthropic API key:
   ```bash
   echo "ANTHROPIC_API_KEY=sk-ant-your-key-here" > .env
   ```

4. Make the collector script executable:
   ```bash
   chmod +x collect.sh
   ```

## Usage

### Basic Collection

```bash
./collect.sh
```

This will:
1. Open Chrome (or use an existing Twitter tab)
2. Navigate to `x.com/home`
3. Auto-scroll and collect ~200 tweets
4. Open the reader interface in your browser

### Collection with Filtering

```bash
./collect.sh --filter
```

or

```bash
./collect.sh -f
```

This runs the Claude Haiku classifier after collection to filter out low-quality content (engagement bait, spam, etc.).

### Custom Tweet Count

```bash
./collect.sh 500           # Collect 500 tweets
./collect.sh 300 --filter  # Collect 300 tweets, then filter
```

## Important: Feed Selection

**The collector scrolls whichever Twitter tab/feed you're currently on.**

Before running, navigate to your desired feed in Chrome:

| Feed | URL | What You Get |
|------|-----|--------------|
| **For You (FYP)** | `x.com/home` (default tab) | Algorithmic recommendations |
| **Following (Popular)** | `x.com/home` → click "Following" | Tweets from accounts you follow, sorted by engagement |
| **Following (Recent)** | `x.com/following` or toggle in Following | Chronological tweets from accounts you follow |

The script defaults to `x.com/home`, but if you have a Twitter tab already open, it will use that tab's current feed position. So if you last visited "Following", it will continue scrolling there.

## Customizing the Filter Prompt

The classification prompt determines what gets kept vs. skipped. You can customize it in two places:

### 1. In the Reader UI (Recommended)

1. Open the reader (`data/reader.html`)
2. Click the **"Prompt"** button in the header
3. Edit the classification prompt in the text area
4. Enter your API key
5. Click **"Re-run Filter"** to reprocess all tweets

Changes are saved to localStorage and persist between sessions.

### 2. In the Python Script

Edit `filter.py` and modify the `CLASSIFICATION_PROMPT` variable (around line 20):

```python
CLASSIFICATION_PROMPT = """Classify this tweet for a technical reader. Return JSON only.

SKIP (skip: true) if: engagement bait, SaaS spam, generic AI hype...
KEEP (skip: false) if: articles, papers, GitHub links...

Author: @{handle} ({name})
Content: {text}
Links: {links}

Return ONLY valid JSON: {"skip": bool, "quality": "high"/"medium"/"low", "topic": "short_slug", "summary": "one sentence"}"""
```

The placeholders `{handle}`, `{name}`, `{text}`, and `{links}` are automatically replaced with tweet data.

## Output Files

| File | Description |
|------|-------------|
| `data/collected.json` | Raw collected tweets |
| `data/filtered.json` | Tweets with classification metadata (when using `--filter`) |
| `data/reader.html` | The reader interface |

## Reader Features

- **Quality badges**: High/Medium/Low quality indicators
- **Topic tags**: Auto-generated topic slugs
- **Summaries**: One-line summaries for each tweet
- **Skipped panel**: View filtered-out tweets (click "Skipped" button)
- **Re-filter**: Modify the prompt and re-run classification from the UI
- **Paste support**: Manually paste tweet JSON with Cmd+V

## Troubleshooting

**"Failed to inject script"**: Make sure Chrome is open with at least one window.

**Rate limiting**: The filter uses 5 parallel requests. If you hit rate limits, it will automatically retry with exponential backoff.

**No tweets collected**: Ensure you're logged into Twitter in Chrome and the feed is loading.
