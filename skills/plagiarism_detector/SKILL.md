# Plagiarism Detector — Skill Instructions

## Purpose
Detects potential plagiarism between student submissions using dual similarity analysis. Flags any pair of submissions that exceed the similarity threshold.

## When to Invoke
- After all submissions have been graded, before generating the Excel report.
- **Skipped** if the Plagiarism Analysis toggle is disabled in the UI.

## Inputs
| Input | Type | Source |
|-------|------|--------|
| `submissions` | `list[dict]` | From file_extractor (`filename`, `content`) |
| `threshold` | `float/int/str` | Optional similarity threshold as ratio (`0.65`) or percent (`65`) |
| `penalty_marks` | `float/int/str` | Optional mark deduction applied once per flagged student |

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
Pairs with `combined >= threshold` are flagged. If no threshold is supplied, `SIMILARITY_THRESHOLD` is used (default 0.65).

Teachers can tune this in the JavaScript UI:
- Higher threshold = stricter matching and fewer flags.
- Lower threshold = more sensitive matching and more possible false positives.
- Mark deduction is optional and applied once per flagged student, not once per matched pair.

## Key Functions
- `check_plagiarism(submissions, results=None, threshold=None)` — returns flag dict
- `apply_flags(results, flags)` — merges flags into grading results
- `apply_flags_and_penalty(results, flags, penalty_marks=0)` — merges flags and optionally deducts marks once per flagged student

## Dependencies
- `scikit-learn` (TF-IDF, cosine similarity)
- `config.SIMILARITY_THRESHOLD`
