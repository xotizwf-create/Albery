# ADR-0002: Codex for reasoning, Groq for media

- Status: accepted
- Date: 2026-08-10
- Owners: Albery engineering

## Context

Groq was used both for media processing and for quality-sensitive reasoning: composing task offers, classifying daily task check-ins, and analyzing the Novinki folder. The owner prioritizes answer quality for the latter workloads and wants Groq retained for screenshots and audio.

## Decision

Use an isolated Codex/Hermes one-shot runner for task offers, task check-in classification, and Novinki analysis. Keep Groq for audio transcription, screenshot understanding, and OCR/media workloads.

Routing clarification accepted during the 2026-08-10 independent acceptance review:

- screenshot/OCR uses Groq as the primary provider and Codex as a resilience fallback;
- audio/STT uses Groq Whisper;
- the Codex vision fallback is media extraction only and is not the generative fallback for task
  offers, task check-in, or Novinki reasoning.

The quality runner must:

- expose zero tools and verify that invariant with a deploy self-check;
- accept untrusted prompts through standard input;
- request machine-validated JSON only;
- share the global run-slot limiter;
- use bounded timeout and retry;
- never fall back to a generative Groq call.

Failure policy:

- task offers: deterministic local fallback;
- task check-in: fail closed and make no task changes;
- Novinki: abort the run and retain all source files if any AI stage fails.

## Consequences

- Higher-quality, more consistent reasoning for business-sensitive decisions.
- More latency and Codex usage than Groq, especially for multi-batch Novinki runs.
- Tool isolation limits prompt-injection impact from task and file content.
- Groq remains valuable for fast, cost-effective media extraction.
- The runner depends on the installed Hermes environment and requires an explicit self-check after deploy.
- Groq is the normal screenshot path, while a Groq outage can consume Codex capacity through the
  explicit vision fallback.
