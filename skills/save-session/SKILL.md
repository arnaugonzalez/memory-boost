---
name: save-session
description: Save what was done in this session to the shared agent memory (log entry + project page update) so any other agent or harness can pick it up later. Use when the user says "save session", "log this session" or "save to memory", or at the end of significant work.
---

# save-session

1. Identify the project slug (the directory name, or ask if unclear).
2. Summarise the session: one-line `action`, one `detail` sentence with the key result,
   and `pending` if something is left to do. Facts only; no narrative.
3. Call `memory_save(project, action, detail, pending, agent="<your harness name>")`.
4. If the session produced **stable** knowledge (how to run the project, an architecture
   fact, a gotcha that will bite again), edit `wiki/projects/<project>.md` directly and add
   it under the right `##` section. Do not copy the log into the page.
5. If a decision was made, create `wiki/decisions/<slug>.md` with frontmatter
   `status: current` and `review_by: <date>`; mark the decision it replaces `status: superseded`.
6. If you used checkpoints, close yours: `memory_checkpoint(..., status="done")`.
7. If the memory home is a git repository, commit only if the user asked you to.
