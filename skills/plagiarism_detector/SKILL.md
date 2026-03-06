# Plagiarism Detector — Skill Instructions

## Purpose
Detects potential plagiarism between student submissions using dual similarity analysis. Flags any pair of submissions that exceed the similarity threshold.

## When to Invoke
- After all submissions have been graded, before generating the Excel report.

## Inputs
| Input | Type | Source |
|-------|------|--------|
| `submissions` | `list[dict]` | From file_extractor (`filename`, `content`) |

## Outputs
| Output | Type | Description |
|--------|------|-------------|
| `flags` | `dict[str, list[str]]` | Mapping of filename → list of flag descriptions |

## Detection Methods

### Method A: TF-IDF Cosine Similarity (semantic)
- Converts each submission to a TF-IDF vector (word frequencies weighted by rarity).
- Computes pairwise cosine similarity matrix.
- Catches submissions using the same ideas/vocabulary even if restructured.

### Method B: Character 4-gram Jaccard Similarity (structural)
- Breaks text into overlapping 4-character windows.
- Computes Jaccard index: `|A ∩ B| / |A ∪ B|`.
- Catches verbatim copy-paste, even partial sentences.

### Combined Score
```
combined = 0.6 × cosine_score + 0.4 × ngram_score
```
Pairs with `combined ≥ SIMILARITY_THRESHOLD` (default 0.65) are flagged.

## Key Functions
- `check_plagiarism(submissions)` — returns flag dict
- `apply_flags(results, flags)` — merges flags into grading results

## Dependencies
- `scikit-learn` (TF-IDF, cosine similarity)
- `config.SIMILARITY_THRESHOLD`
