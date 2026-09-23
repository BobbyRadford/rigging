---
name: writing-for-agents
description: Writing documents for agents. Use when creating or editing skills, or modifying AGENTS.md or CLAUDE.md.
---

Use this when you write anything an agent will read, whether that's a skill, an `AGENTS.md` or `CLAUDE.md`, or a doc that one of those points to. The goal is a document that leads the agent through the same process every time it runs. The output can differ from run to run, but the way the agent gets there shouldn't.

If the document is a skill, also read [`SKILL-MECHANICS.md`](SKILL-MECHANICS.md). It covers frontmatter, how a skill gets invoked, and router skills.

## Voice

Write the way you'd explain the task to a capable coworker, in whole sentences with plain words. Agents copy the style of what they read. A clipped document full of symbols and bold labels gets clipped, symbol-heavy work back. When this skill talks about cutting, it means cutting whole sentences and whole ideas. Keep the articles, verbs, and connecting words that make each sentence easy to read. Refer to the person you work for as "the user", and load the unslop skill for the full set of prose rules.

## Context pointers

A context pointer is a line the agent always sees that names some material it doesn't see yet and says when to go read it. A skill's description is a context pointer, and so is a line in `AGENTS.md` that mentions a doc. The wording of the pointer decides whether the agent actually reads the material. If something important sits behind a vaguely worded pointer, the agent will read it on some runs and skip it on others. Fix the wording first, and only move the material inline if better wording doesn't work.

A good pointer says what the material is and lists each situation that should send the agent to it. Because the agent sees the pointer on every turn, each word costs something every time, so trim it harder than you'd trim the body.

- Put the most important word first, since that's the word that triggers the read.
- Give each distinct situation one trigger. Three synonyms for the same situation are one trigger written three times.
- Leave out anything the body already explains.

## Two kinds of cost

Every document you add costs the agent or the user something.

Context cost is what always-loaded text costs the agent. An `AGENTS.md` line or a skill description takes up tokens and attention on every turn, whether or not it's relevant.

Memory cost is what it costs the user to know which documents exist and when to use them. This cost is worth paying where the user's judgement matters, because it keeps them in control. Where their judgement doesn't matter, remove it.

Material behind a pointer only costs the agent the pointer's line. Material with no pointer at all costs the agent nothing, but the user has to remember it exists.

## What goes where

A document contains steps, which are the actions the agent takes in order, and reference, which is rules and facts the agent looks up when it needs them. A recipe is all steps, a review checklist is all reference, and many documents mix the two.

Decide where each piece goes by how soon the agent needs it.

1. Steps go in the main file, in order.
2. Reference the agent needs on most runs goes in the main file, after or beside the steps.
3. Reference only some runs need goes in a separate file, with a pointer to it from the main file.

The cleanest test is to ask which cases need a piece. If every case needs it, keep it in the main file. If only some cases need it, move it to a separate file. Moving out too little buries the steps under reference, and the agent starts skipping steps. Moving out too much hides material the agent needed.

Keep related material together. A concept's definition, its rules, and its caveats belong under one heading, so reading one part brings the rest along. The finished document should read like documentation written for the agent. This is different from duplication. Duplication says the same thing in two places, while scattering splits one idea across several places.

A document can also be too long even when every line is accurate and useful. The agent's attention spreads thin across a long file, and every extra line is one more to keep up to date. Fix this by moving reference out into separate files and by splitting the document into smaller ones.

## Steps and how they end

Every step needs a completion criterion, a condition that tells the agent the step is done. How clear it is matters, and so does how much it asks for.

Clarity decides whether the agent can tell done from not done. A vague criterion like "until you understand the problem" lets the agent stop early, because the steps still ahead pull it forward. Sharpen the criterion first. If it can't be made sharp and you actually see the agent rushing, hide the later steps by splitting the work across a real handoff, such as a subagent. Calling another skill in the same session doesn't hide anything, because the later steps stay in context.

How much a criterion asks for decides how much work the agent does. "Every modified model is accounted for" makes the agent dig much harder than "produce a list of changes". This works for reference too. "Every rule applied" sets the same bar for a checklist that "every step done" sets for a sequence.

The best criteria are ones the agent can check and that cover everything.

## When to split a document

Splitting adds cost, so only split when it pays off.

Split a sequence of steps when the later steps tempt the agent to rush the current one. Keeping them out of view makes the agent work harder on what's in front of it. The reverse also holds. Merging two sequences shows the agent more steps ahead, which invites it to rush.

Splitting a skill so it can be invoked separately is covered in [`SKILL-MECHANICS.md`](SKILL-MECHANICS.md).

## Leading words

A leading word is an ordinary word or phrase the model already understands well, which you repeat to anchor a behavior. For example, a "tight" loop means one that's fast, deterministic, and cheap to run. A loop that goes "red" means one that fails reliably on the bug, which turns a fuzzy goal into something the agent can observe. Each time the agent sees the word, it reaches for the same behavior, and one word replaces a sentence you would otherwise repeat.

Choose words the model already knows. A made-up term brings no meaning with it, so you have to spend sentences defining it. Write a leading word as plain text, like any other word. Bolding it or defining it with a colon turns it into jargon. The same word works in a pointer, where using the same language as your prompts and codebase helps the agent find the material.

Look for places where one word could replace a repeated phrase. If the same three qualities are spelled out in three places, or a pointer spends a whole sentence gesturing at one idea, a leading word can usually replace it.

## Say what to do

Telling the agent what not to do tends to backfire. Naming the unwanted behavior puts it in the agent's context, and the "don't" is weaker than the behavior it's attached to. Describe the behavior you want instead, such as "write one-line comments". Keep a prohibition only as a hard rule you can't phrase positively, and pair it with the behavior you want.

## Pruning

Say each thing in one place, so changing a behavior means editing one line. Duplication costs tokens and upkeep, and it makes a point look more important than it is.

Leave out anything the agent can find by looking at the repo, such as scripts in `package.json`, config files, the directory layout, or `--help` output. A copy of those goes stale. Write down what the agent can't find by looking, like unwritten conventions, the reasons behind a choice, and gotchas that no config file mentions.

Check every line against what the document does today. Lines go stale when the behavior they describe changes, and stale lines pile up because adding feels safe and deleting feels risky. Shorter documents are easier to keep current.

Delete any instruction the model already follows without being told. The test is whether the line changes what the agent does compared with having no line at all, and you settle that by running the document. When a sentence fails the test, delete the whole sentence rather than trimming words from it. A leading word too weak to change behavior, like "be thorough", fails the same test. Replace it with a stronger word, like "relentless".
