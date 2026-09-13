---
name: linear-ticket-writing
description: Write or rewrite Linear tickets in plain language with concrete changes and observable completion checks. Use when drafting, creating, editing, or splitting Linear issues and their acceptance criteria; not for code work merely linked to an issue.
---

# Write Linear tickets people can act on

The reader should understand what to do without reading our conversation or knowing our internal jargon. Explain unfamiliar concepts simply, without talking down to them. A ticket is a work instruction, not a strategy presentation.

## Write the actual work

- Give the ticket a short action title that names the change. Prefer “Block merging when required tests fail” to “Establish delivery assurance.”
- Start with the concrete problem: what happens today, when it happens, and why that causes trouble. Distinguish observed facts from suspected causes and proposed improvements.
- Say exactly what to change. Name the file, command, service, setting, or screen when known. Never invent a path or implementation detail to sound specific. If investigation is needed, name what to inspect and what decision the findings should settle.
- Use a before/after example when it makes the request easier to understand.
- Explain how someone can tell the work is complete. Describe an observable result, including a failure case where relevant. “Verify it works” and “ensure quality” do not tell anyone what to check.
- Include links and evidence that help the person start. Keep detailed background below the work instructions.

For substantial tickets, these headings usually work:

### What is wrong?
A short explanation of the current problem and its effect.

### What we need to do
Numbered, concrete actions. Each action should tell the reader what to change or investigate.

### How we know it works
Observable checks or examples that demonstrate the intended result.

### Where to start
Relevant files, existing issues, PRs, commands, or documentation. Omit this section when it adds nothing.

Scale the structure to the task. A simple fix may need only a few sentences. Do not turn ticket writing into another documentation requirement.

## Language and planning

- Use familiar words and direct verbs. Avoid abstract phrases such as “evidence contract,” “delivery lane,” “fenced remediation,” “policy alignment,” or “deterministic merge-ready result.” Explain the actual action instead.
- Technical names are useful when they identify something the reader must work on. Explain unfamiliar terms on first use. For example, “mutation tests deliberately introduce small mistakes to check whether our tests catch them.”
- Do not replace jargon with equally vague plain language. “Make checks better” still needs an explanation of which checks and what should change.
- Do not assume work takes weeks or create calendar phases by default. Describe the next useful change and its real prerequisites. Estimate duration only when requested or supported by evidence; AI tools alone do not prove an estimate.
- Explain issue dependencies concretely: “This issue starts the simulator; ROW-123 adds the flight scenarios that run inside it.” Do not mark loosely related work as blocked.
- Preserve necessary safety rules and human review requirements. Write out what they require for this task rather than hiding them behind shorthand.

## Example

Unclear:
“Implement one deterministic merge-ready result that aggregates applicable evidence.”

Clear:
“Add a final GitHub check that reads the results of the tests and builds required for this PR. It must fail if any required check failed or never ran. Make GitHub require this final check before merging. Try a PR with a deliberately failing test and confirm GitHub blocks the merge.”

Use the example to guide the writing style, not to prescribe that implementation for every repository.

## Editing existing tickets

Rewrite confusing text rather than appending a clearer version beneath it. Preserve useful facts, links, and unresolved questions. Keep historical notes only when they still help, and label them clearly so they cannot be mistaken for current instructions.

Keep assignees, labels, status, and relationships unchanged unless the user requests changes. Do not carry a one-time assignment or project preference into unrelated tickets. Avoid personal assessments of teammates or unnecessary conversation history.

This skill governs writing. Follow the user's request for whether to draft or publish; it does not independently authorize external changes.

Before finishing, read the ticket as someone new to the project: could they explain what needs changing and how to check the result? Replace any sentence that sounds impressive but does not help them do the work.
