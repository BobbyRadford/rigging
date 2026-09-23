# Skill mechanics

This covers what's specific to skills, which is how they get invoked, when to split one, and router skills. Everything else about writing a skill is in [`SKILL.md`](SKILL.md).

## Invocation

A skill is either model-invoked or user-invoked, and the choice trades context cost against memory cost.

A model-invoked skill has a description the agent always sees, so the agent can load the skill on its own and other skills can load it too. The user can still invoke it by name. The description is the skill's context pointer, so it costs context on every turn in exchange for the agent being able to find the skill. Write it for the agent, listing the situations that should trigger it, and follow the pointer rules in `SKILL.md`. A model-invoked skill that's all reference is also a good home for material several other skills need, since each of them can load it.

A user-invoked skill is hidden from the agent. Only the user can load it, by typing its name, and no other skill can reach it. It costs no context, but the user has to remember it exists. To make a skill user-invoked, set `disable-model-invocation: true` in the frontmatter for Claude, and set `allow_implicit_invocation: false` under `policy` in `agents/openai.yaml` for Codex. The description then only needs to be a one-line summary for the user.

Make a skill model-invoked only when the agent or another skill needs to reach it on its own. If it only ever runs when the user asks for it, make it user-invoked.

Never rely on a user-invoked skill for behavior that should always happen. The agent can't see it, so a line in its description saying it must always apply does nothing. Put rules like that in `AGENTS.md`, or make the skill model-invoked.

Two user-invoked skills can't share reference through each other, because neither can load the other. Put shared material in a plain file outside the skills that both can point to.

## Splitting a skill by invocation

Split part of a skill into its own model-invoked skill when it has a distinct trigger word you actually use in your prompts, or when another skill needs to load it. The new description costs context on every turn, so the separate trigger has to be worth that.

Splitting a sequence of steps is covered in `SKILL.md`.

## Router skills

When there are more user-invoked skills than the user can remember, add a router skill. It's a user-invoked skill that lists the others and says when to use each one, so the user only has to remember one name. A router can only suggest the other skills, because user-invoked skills can't be loaded by anything but the user.
