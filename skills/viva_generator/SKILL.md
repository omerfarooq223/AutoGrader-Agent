# Viva Question Generator

Generates teacher-facing viva questions from a student project proposal, project report, or similar project document.

## Inputs

| Name | Type | Description |
| --- | --- | --- |
| `document_text` | `str` | Extracted project proposal/report text |
| `project_name` | `str` | Optional project name shown in the output |
| `difficulty` | `str` | One of `mixed`, `basic`, `intermediate`, `advanced` |
| `question_count` | `int` | Number of questions to generate |

## Output

Returns a dictionary with:

- `project_name`
- `questions`
- `notes`

Each question includes:

- `category`
- `difficulty`
- `question`
- `what_to_listen_for`

## Rules

- Questions should be specific to the uploaded document.
- Do not invent technologies or claims not present in the project text.
- Prefer teacher-friendly wording.
- Include conceptual, design, implementation, limitation, and testing/deployment angles when relevant.
