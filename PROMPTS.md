# Prompts

Everything the LLM sees lives in `prompts.py`. This file quotes it exactly. If you change one, change the other.

## How the prompts fit together

The context the LLM gets on every turn is:

1. the **system prompt** (persona + voice rules), always first
2. the conversation so far: what the user said, and what the bot actually spoke
3. **one session note** at the end, written by Python for this turn only (the previous turn's note is removed)

Python fills each note's `{next_step}` with either `ASK_QUESTION` (the next question, word for word) or `GIVE_FEEDBACK` (after the last question).

## System prompt (exactly as used)

```text
You are a calm, warm and encouraging interview coach running a short spoken mock interview.

Before each of your replies you will get a session note telling you what to do next. Follow it exactly.
Never ask a question the session note did not give you, and never skip ahead.

You are speaking out loud, so:
- Use short, plain sentences. Ask only one question per reply.
- Never use lists, markdown, emojis or symbols.
- Say numbers and abbreviations the way a person would say them, for example "about twenty percent" instead of "~20%".
- Keep acknowledgements short and genuine, like "That sounds like a solid project." Do not praise everything.
```

## Session notes (exactly as used)

`OPEN`: the first turn, sent with no user message:
```text
Session note: greet the candidate in one short friendly sentence, then {next_step}
```

`DECIDE`: a normal turn, where the LLM chooses:
```text
Session note: look at the candidate's last answer.
If it is vague, very short, or they seem stuck, start your reply with [FOLLOWUP] and then ask one short follow-up about something they actually said, or give one gentle hint.
Otherwise start your reply with [NEXT], acknowledge their answer in a few words, then {next_step}
```

`ADVANCE`: after the follow-up has been used, so moving on is forced:
```text
Session note: acknowledge their answer in a few words, then {next_step}
```

`ASK_QUESTION`: fills `{next_step}` with the next question:
```text
ask exactly: "{question}"
```

`GIVE_FEEDBACK`: fills `{next_step}` after the last question:
```text
give two or three sentences of spoken feedback on the whole interview:
one specific strength and one specific thing to improve, both based on what the candidate actually said.
Then thank them and say goodbye. Do not ask any more questions.
```

The questions, in order:
1. Tell me about a project you're proud of.
2. What was the hardest technical problem in it?
3. How did you know it was working well?
4. What would you do differently now?

## How the agent decides: follow-up, nudge, or next question

**Python** owns the order. The LLM only chooses whether an answer needs its one follow-up. It never chooses which question comes next.

```
first turn                        -> OPEN     (greeting + Q1)
follow-up already used for this Q -> ADVANCE  (acknowledge + next question / feedback)
otherwise                         -> DECIDE   (LLM picks [FOLLOWUP] or [NEXT])
```

- With `DECIDE`, the LLM starts its reply with a tag. `TagStripper` reads it, removes it before text-to-speech, and records the decision. A missing tag counts as `[FOLLOWUP]`, the safe choice, because the next turn is then forced to move on.
- "Follow-up" and "gentle nudge" are the same budget: one per question, whichever the LLM judges fits ("ask one short follow-up … or give one gentle hint").
- **A decision only counts once the reply has played to the end.** If the user cuts in, or the reply is cancelled before any audio plays, the decision is thrown away and the same question is offered again on the next turn. So the 4 questions can't be skipped or reordered. The worst case is one asked twice.
- The question text is given word for word ("ask exactly"), so the LLM can't paraphrase it into a different question.
- Using a tag instead of a tool call like `next_question()` avoids a second LLM round trip (roughly 300–500ms) on every turn that moves on.

## How the prompt is shaped for voice

- **Short sentences, one question per reply.** Long sentences delay the first audio (Cartesia starts on the first sentence) and are hard to follow by ear.
- **No lists, markdown, emojis or symbols.** TTS would read them out or garble them.
- **Numbers and abbreviations as spoken** ("about twenty percent", not "~20%"). Cartesia also normalises digits, but spelling them out avoids misreadings like "~".
- **Names:** there is no special rule. The coach repeats the user's own words from the transcript, so a misheard name is repeated as heard (see failure cases).
- **Short, genuine acknowledgements**, so the coach doesn't spend its first second of audio on praise.
- Persona is set once in the system prompt (calm, warm, encouraging, not praising everything) and isn't restated per turn, which keeps the notes short.

## How the closing feedback is generated

After question 4 (once its follow-up is used, or the LLM says `[NEXT]`), `{next_step}` becomes `GIVE_FEEDBACK`. The LLM writes the feedback from the **whole conversation in its context**: every answer as transcribed, plus its own follow-ups. It's told to give one specific strength and one specific improvement, "both based on what the candidate actually said", in 2–3 spoken sentences, then say goodbye. When that reply has played to the end, `TurnDirector` ends the session.

## Iterations

### v1: a plain assistant, kicked off with a fake user message
The first working pipeline used `"You are a friendly assistant in a voice conversation…"` and started the conversation by adding a user message, `"Say a short hello."`, to the context.

**What was wrong:** nothing kept it to the 4 questions or to one follow-up. The fake line also stayed in the history as if the candidate had said it.

**Changed to:** the interview system prompt, with the context starting from the system prompt only, and the `OPEN` session note asking for the greeting and Q1.

### v2: per-question time budgets and a "wrap-up" nudge
Each question had a budget (90/75/60/60s) and a fourth note, `WRAP_UP_NUDGE` ("we are nearly out of time… very gently and kindly say you'd like to keep things moving…"), sent when under 15s were left.

**What was wrong:** it wasn't part of the brief and added state to get wrong. In a live session the timer was running for a question the user never heard:
```text
interview: Q3 | 53s left | DECIDE
```
Q3 had been started and immediately cut off, but its timer had started anyway.

**Changed to:** timers and `WRAP_UP_NUDGE` removed. Only the follow-up budget limits each question.

### v3: decisions applied too early (two live bugs)
**Bug 1:** the user paused briefly mid-answer, and smart-turn ended the turn:
```text
user: "Project that I'm really proud of is the voice agent project that I'm working on currently."
interview: Q1 | 83s left | DECIDE
User started speaking  -> broadcasting interruption     (user carried on talking)
interview: Q1 | LLM chose FOLLOWUP                      (tag from the cancelled reply, never played)
interview: Q1 | 79s left | ADVANCE                      (forced on to Q2 mid-answer)
```

**Bug 2:** `[NEXT]` plus Q3 started playing, and the user said "And" a moment later. The counter had already moved, so Q3 was never heard and every later note offered Q4.

**Changed to (current):** decisions wait as `pending` and are applied only when the reply plays to the end without being interrupted.

## Known failure cases

1. **Misheard technical words get repeated back.** Deepgram transcribed "Pipecat" as "pipette", and the coach's follow-up used it: *"What part of the pipette logic is most confusing for you?"* The LLM has no way to know the transcript is wrong. A fix would be Deepgram keyterm boosting for expected jargon.
2. **Fragmented answers can trigger the wrong decision.** When a pause ends the turn early, the answer arrives as several user messages, and the most recent may be a fragment like `"So"`. `DECIDE` asks the LLM to look at "the candidate's last answer", so a fragment can look vague and use up the follow-up even though the full answer was fine.
3. **A question can be asked twice** if the user cuts in right at the end of it, because the interrupted reply doesn't count.
