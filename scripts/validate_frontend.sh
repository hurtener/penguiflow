#!/bin/bash
# Validates that frontend dist files are properly built
# Run this before commits or in CI

set -e

DIST_DIR="penguiflow/cli/playground_ui/dist"
INDEX_HTML="$DIST_DIR/index.html"
MIN_INDEX_SIZE=200  # bytes - valid index.html should be at least this size

echo "Validating frontend build..."

# Check dist directory exists
if [ ! -d "$DIST_DIR" ]; then
    echo "❌ ERROR: Frontend dist directory missing: $DIST_DIR"
    echo "   Run: cd penguiflow/cli/playground_ui && npm run build"
    exit 1
fi

# Check index.html exists
if [ ! -f "$INDEX_HTML" ]; then
    echo "❌ ERROR: index.html missing: $INDEX_HTML"
    echo "   Run: cd penguiflow/cli/playground_ui && npm run build"
    exit 1
fi

# Check index.html is not truncated
INDEX_SIZE=$(wc -c < "$INDEX_HTML" | tr -d ' ')
if [ "$INDEX_SIZE" -lt "$MIN_INDEX_SIZE" ]; then
    echo "❌ ERROR: index.html appears truncated ($INDEX_SIZE bytes, expected >= $MIN_INDEX_SIZE)"
    echo "   Current content:"
    cat "$INDEX_HTML"
    echo ""
    echo "   Run: cd penguiflow/cli/playground_ui && npm run build"
    exit 1
fi

# Check for assets directory
if [ ! -d "$DIST_DIR/assets" ]; then
    echo "❌ ERROR: assets directory missing"
    exit 1
fi

# Check for at least one JS and CSS file
JS_COUNT=$(find "$DIST_DIR/assets" -name "*.js" | wc -l | tr -d ' ')
CSS_COUNT=$(find "$DIST_DIR/assets" -name "*.css" | wc -l | tr -d ' ')

if [ "$JS_COUNT" -eq 0 ]; then
    echo "❌ ERROR: No JS files in assets directory"
    exit 1
fi

if [ "$CSS_COUNT" -eq 0 ]; then
    echo "❌ ERROR: No CSS files in assets directory"
    exit 1
fi

echo "✅ Frontend build valid:"
echo "   - index.html: $INDEX_SIZE bytes"
echo "   - JS files: $JS_COUNT"
echo "   - CSS files: $CSS_COUNT"
