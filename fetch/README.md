# fetch/

URL → Content materializer.

## Usage

### Input (queue/)

Drop a file in `queue/` to trigger materialization.

**Flexible formats accepted**:

```
# Just a URL
https://example.com/article

# URL with note (blank line separated)
https://example.com/article

This is context about why I'm saving this.

# JSON (for programmatic use)
{
  "url": "https://example.com/article",
  "note": "Optional context",
  "tags": ["optional", "tags"]
}
```

**Filename**: Anything, but prefer descriptive (e.g., `2025-11-27-article-title.txt`).

### Output (output/)

Materialized content lands here as markdown:

```
output/
└── 2025-11-27-article-title.md
```

**Format**:
```markdown
---
url: https://example.com/article
title: Article Title
fetched_at: 2025-11-27T12:00:00Z
source_note: Optional context from input
---

# Article Title

Content here...
```

### Trigger Methods

| Method | How |
|--------|-----|
| **File drop** | Add file to `queue/` → push triggers workflow |
| **Manual** | GitHub Actions workflow dispatch |
| **Webhook** | POST to workflow dispatch endpoint |

## How It Works

1. File appears in `queue/`
2. GitHub Actions workflow triggers on push
3. Workflow reads file, extracts URL
4. Fetches content (Jina Reader → Playwright fallback)
5. Writes markdown to `output/`
6. Deletes processed file from `queue/`

## Limitations

- One file at a time (sequential processing)
- No retry on failure (logs error, skips)
- No deduplication (same URL can be processed multiple times)

These can be improved as needed.
