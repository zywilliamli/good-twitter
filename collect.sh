#!/bin/bash
# Twitter collector - opens browser, scrolls, collects tweets, opens reader
# Usage: ./collect.sh [count] [--filter]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$SCRIPT_DIR/data"
READER_PATH="$DATA_DIR/reader.html"
OUTPUT_PATH="$DATA_DIR/collected.json"

# Load config if exists
if [ -f "$SCRIPT_DIR/config.sh" ]; then
    source "$SCRIPT_DIR/config.sh"
fi

# Parse args
TARGET=200
FILTER=false
for arg in "$@"; do
    if [[ "$arg" == "--filter" ]] || [[ "$arg" == "-f" ]]; then
        FILTER=true
    elif [[ "$arg" =~ ^[0-9]+$ ]]; then
        TARGET=$arg
    fi
done

# Random delay (0-15 minutes) to avoid detection patterns
if [ -n "$RANDOM_DELAY" ] || [ "$1" = "--scheduled" ]; then
    DELAY=$((RANDOM % 900))
    echo "Waiting ${DELAY}s before collection..."
    sleep $DELAY
fi

# Check if Chrome is running and has a window
CHROME_CHECK=$(osascript 2>/dev/null << 'CHECKEOF'
tell application "System Events"
    if not (exists process "Google Chrome") then
        return "not_running"
    end if
end tell
tell application "Google Chrome"
    if (count of windows) is 0 then
        return "no_windows"
    end if
    return "ok"
end tell
CHECKEOF
)

if [ "$CHROME_CHECK" = "not_running" ]; then
    echo "Chrome not running, skipping collection"
    exit 0
fi

if [ "$CHROME_CHECK" = "no_windows" ]; then
    echo "Chrome has no windows, skipping collection"
    exit 0
fi

echo "Twitter Collector"
echo "================="
echo "Target: $TARGET tweets"
if [ "$FILTER" = true ]; then
    echo "Filter: ON (will run Claude classifier)"
fi
echo ""

# Create temp JS file that Chrome will load
TEMP_JS="$DATA_DIR/_collector.js"
cat > "$TEMP_JS" << JSEOF
(function() {
  // Reset any stale state from previous runs
  window._collectorRunning = false;
  window._collectedData = null;
  window._collectorRunning = true;
  window.T = new Map();
  var TARGET = ${TARGET};

  // Parse Twitter's relative time (5m, 2h, 3d, Jan 15) into absolute timestamp
  function parseTime(timeStr, now) {
    if (!timeStr) return now;
    timeStr = timeStr.trim();
    // Match patterns like "5m", "2h", "3d" - using [0-9] instead of backslash-d for escaping safety
    var match = timeStr.match(/^([0-9]+)([smhd])$/);
    if (match) {
      var num = parseInt(match[1], 10);
      var unit = match[2];
      var ms = 0;
      if (unit === 's') ms = num * 1000;
      else if (unit === 'm') ms = num * 60000;
      else if (unit === 'h') ms = num * 3600000;
      else if (unit === 'd') ms = num * 86400000;
      return now - ms;
    }
    // Try to parse as date (e.g., "Jan 15" or "Jan 15, 2024")
    // If no year present, add current year
    var currentYear = new Date(now).getFullYear();
    var withYear = timeStr;
    // Check if string has a year (4 digits)
    if (!/[0-9]{4}/.test(timeStr)) {
      withYear = timeStr + ', ' + currentYear;
    }
    var parsed = Date.parse(withYear);
    if (!isNaN(parsed)) {
      // If parsed date is in the future, it's probably from last year
      if (parsed > now) {
        parsed = Date.parse(timeStr + ', ' + (currentYear - 1));
      }
      return parsed;
    }
    return now;
  }

  var collector = setInterval(function() {
    var now = Date.now();
    document.querySelectorAll('[data-testid="tweet"]').forEach(function(el) {
      try {
        var text = el.querySelector('[data-testid="tweetText"]');
        text = text ? text.innerText : '';
        var user = el.querySelector('[data-testid="User-Name"]');
        user = user ? user.innerText : '';
        var parts = user.split(String.fromCharCode(10));
        var name = parts[0] || '';
        var handle = parts[1] || '';
        var time = parts[3] || '';
        var imgs = [];
        el.querySelectorAll('img[src*="pbs.twimg.com/media"]').forEach(function(i) { imgs.push(i.src); });
        var links = [];
        el.querySelectorAll('a[href^="http"]').forEach(function(a) {
          var h = a.href;
          if (h.indexOf('x.com') === -1 && h.indexOf('twitter.com') === -1) links.push(h);
        });
        var timeLink = el.querySelector('time');
        timeLink = timeLink ? timeLink.closest('a') : null;
        var tweetUrl = timeLink ? timeLink.href : '';
        var key = handle + text.slice(0, 50);
        var tweetTs = parseTime(time, now);
        if (time && time.trim() !== '' && !T.has(key)) T.set(key, { name: name, handle: handle, time: time, text: text, imgs: imgs, links: links, tweetUrl: tweetUrl, ts: tweetTs });
      } catch(e) {}
    });
  }, 400);

  var scroller = setInterval(function() {
    if (T.size >= TARGET) {
      clearInterval(scroller);
      clearInterval(collector);
      window._collectorRunning = false;
      window._collectedData = JSON.stringify(Array.from(T.values()));
      console.log('COLLECTION_COMPLETE:' + T.size);
      return;
    }
    window.scrollBy(0, 1500); document.documentElement.scrollTop += 1500;
    console.log('COLLECTING:' + T.size + '/' + TARGET);
  }, 500);
})();
JSEOF

# Base64 encode the JS to safely pass through shell/AppleScript
JS_B64=$(base64 < "$TEMP_JS" | tr -d '\n')

# AppleScript to control Chrome
osascript << ASEOF
tell application "Google Chrome"
    activate
    delay 0.5

    -- Make sure we have a window
    if (count of windows) is 0 then
        make new window
        delay 1
    end if

    -- Check if there's a tab with Twitter already open
    set foundTab to false
    set tabIndex to 1

    repeat with t in tabs of window 1
        if URL of t contains "x.com" or URL of t contains "twitter.com" then
            set foundTab to true
            exit repeat
        end if
        set tabIndex to tabIndex + 1
    end repeat

    if foundTab then
        set active tab index of window 1 to tabIndex
        set URL of active tab of window 1 to "https://x.com/home"
    else
        tell window 1 to make new tab with properties {URL:"https://x.com/home"}
    end if

    delay 3

    -- Inject collector script via base64 decode
    tell active tab of window 1
        execute javascript "eval(atob('${JS_B64}'))"
    end tell

end tell
ASEOF

if [ $? -ne 0 ]; then
    echo "Error: Failed to inject script. Make sure Chrome is open and has a window."
    exit 1
fi

echo "Collector injected. Scrolling..."
echo ""

# Poll for completion
sleep 3
ATTEMPTS=0
MAX_ATTEMPTS=120

while [ $ATTEMPTS -lt $MAX_ATTEMPTS ]; do
    RESULT=$(osascript 2>/dev/null << 'POLLEOF'
tell application "Google Chrome"
    if (count of windows) > 0 then
        tell active tab of window 1
            return execute javascript "window._collectedData || ''"
        end tell
    end if
    return ""
end tell
POLLEOF
    )

    if [ -n "$RESULT" ] && [ "$RESULT" != "" ] && [ ${#RESULT} -gt 10 ]; then
        echo ""
        echo "Collection complete!"
        echo "$RESULT" > "$OUTPUT_PATH"

        COUNT=$(echo "$RESULT" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "?")
        echo "Saved $COUNT tweets to $OUTPUT_PATH"

        # Create loader HTML that merges with existing
        cat > "$DATA_DIR/loader.html" << HTMLEOF
<!DOCTYPE html>
<html>
<head><title>Loading...</title></head>
<body>
<script>
const newData = $RESULT;
const existing = JSON.parse(localStorage.getItem('tweets') || '[]');
// Dedupe by handle + first 50 chars of text
const existingKeys = new Set(existing.map(t => (t.handle || '') + (t.text || '').slice(0, 50)));
const uniqueNew = newData.filter(t => !existingKeys.has((t.handle || '') + (t.text || '').slice(0, 50)));
const merged = [...uniqueNew, ...existing];
console.log('Merged:', uniqueNew.length, 'new +', existing.length, 'existing =', merged.length);
localStorage.setItem('tweets', JSON.stringify(merged));
window.location.href = 'reader.html';
</script>
</body>
</html>
HTMLEOF

        # Run filter if requested
        if [ "$FILTER" = true ]; then
            echo ""
            echo "Running Claude filter..."
            cd "$SCRIPT_DIR"
            [ -f .env ] && export $(grep -v '^#' .env | xargs)
            source .venv/bin/activate && python3 filter.py

            if [ -f "$DATA_DIR/filtered.json" ]; then
                # Use filtered data instead
                RESULT=$(cat "$DATA_DIR/filtered.json")
                FCOUNT=$(echo "$RESULT" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "?")
                echo "Filtered to $FCOUNT tweets"

                # Update loader to use filtered data (merges with existing)
                cat > "$DATA_DIR/loader.html" << FILTEREDEOF
<!DOCTYPE html>
<html>
<head><title>Loading...</title></head>
<body>
<script>
const newData = $RESULT;
const existing = JSON.parse(localStorage.getItem('tweets') || '[]');
const existingKeys = new Set(existing.map(t => (t.handle || '') + (t.text || '').slice(0, 50)));
const uniqueNew = newData.filter(t => !existingKeys.has((t.handle || '') + (t.text || '').slice(0, 50)));
const merged = [...uniqueNew, ...existing];
console.log('Merged:', uniqueNew.length, 'new +', existing.length, 'existing =', merged.length);
localStorage.setItem('tweets', JSON.stringify(merged));
window.location.href = 'reader.html';
</script>
</body>
</html>
FILTEREDEOF
            fi
        fi

        # Sync to gist for mobile access (merge with existing gist data)
        if [ -n "$GIST_ID" ] && [ -n "$GITHUB_USERNAME" ]; then
            echo "Syncing to gist..."

            # Determine which local file to use
            if [ -f "$DATA_DIR/filtered.json" ] && [ "$FILTER" = true ]; then
                LOCAL_DATA="$DATA_DIR/filtered.json"
            else
                LOCAL_DATA="$OUTPUT_PATH"
            fi

            # Fetch existing gist data and merge
            GIST_URL="https://gist.githubusercontent.com/${GITHUB_USERNAME}/${GIST_ID}/raw/collected.json"
            EXISTING_DATA=$(curl -s "$GIST_URL" 2>/dev/null || echo "[]")

            # Merge using Python (dedup by handle + text[:50])
            MERGED_FILE="$DATA_DIR/_merged_gist.json"
            python3 << PYEOF
import json
import sys

try:
    with open('$LOCAL_DATA') as f:
        new_tweets = json.load(f)
except:
    new_tweets = []

try:
    existing = json.loads('''$EXISTING_DATA''')
    if not isinstance(existing, list):
        existing = []
except:
    existing = []

# Merge: new tweets take precedence, dedup by handle + text[:50]
seen = set()
merged = []

for t in new_tweets:
    key = (t.get('handle') or '') + (t.get('text') or '')[:50]
    if key not in seen:
        seen.add(key)
        merged.append(t)

for t in existing:
    key = (t.get('handle') or '') + (t.get('text') or '')[:50]
    if key not in seen:
        seen.add(key)
        merged.append(t)

# Sort by timestamp (newest first)
merged.sort(key=lambda t: t.get('ts', 0), reverse=True)

with open('$MERGED_FILE', 'w') as f:
    json.dump(merged, f)

print(f"Merged: {len(new_tweets)} new + {len(existing)} existing = {len(merged)} total")
PYEOF

            # Upload merged data
            gh gist edit "$GIST_ID" -f collected.json "$MERGED_FILE" 2>/dev/null || echo "Gist sync failed (optional)"
            rm -f "$MERGED_FILE"
        fi

        # Only open reader if not running scheduled (avoid interrupting user)
        if [ "$1" != "--scheduled" ]; then
            echo "Opening reader..."
            open "$DATA_DIR/loader.html"
            sleep 2
            rm -f "$DATA_DIR/loader.html"
        else
            echo "Scheduled run complete (skipping reader open)"
        fi

        exit 0
    fi

    sleep 1
    ATTEMPTS=$((ATTEMPTS + 1))

    # Progress indicator
    if [ $((ATTEMPTS % 5)) -eq 0 ]; then
        printf "."
    fi
done

echo ""
echo "Timeout. Check Chrome console - you may need to manually copy the data."
echo "In console run: copy(JSON.stringify([...T.values()]))"
exit 1
