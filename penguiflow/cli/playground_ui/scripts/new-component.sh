#!/usr/bin/env bash
set -euo pipefail

NAME="${1:-}"
CATEGORY="${2:-}"

if [[ -z "$NAME" || -z "$CATEGORY" ]]; then
  echo "Usage: ./scripts/new-component.sh <Name> <category/path>"
  echo "Example: ./scripts/new-component.sh Button primitives"
  exit 1
fi

COMPONENT_DIR="src/lib/components/${CATEGORY}"
TEST_DIR="tests/unit/components/${CATEGORY}"

mkdir -p "$COMPONENT_DIR" "$TEST_DIR"

COMPONENT_PATH="${COMPONENT_DIR}/${NAME}.svelte"
TEST_PATH="${TEST_DIR}/${NAME}.test.ts"

if [[ -f "$COMPONENT_PATH" ]]; then
  echo "Component already exists: ${COMPONENT_PATH}"
  exit 1
fi

cat > "$COMPONENT_PATH" << EOF
<script lang="ts">
  interface Props {
    // Add props here
  }

  let { }: Props = \$props();
</script>

<div class="${NAME,,}">
  <!-- Component content -->
</div>

<style>
  .${NAME,,} {
    /* Styles */
  }
</style>
EOF

cat > "$TEST_PATH" << EOF
import { render } from '@testing-library/svelte';
import { describe, it, expect } from 'vitest';
import ${NAME} from '\$lib/components/${CATEGORY}/${NAME}.svelte';

describe('${NAME}', () => {
  it('renders', () => {
    const { container } = render(${NAME});
    expect(container).toBeTruthy();
  });
});
EOF

echo "Created ${COMPONENT_PATH}"
echo "Created ${TEST_PATH}"
