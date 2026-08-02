# Project Vision

> For an experienced quantitative trader encountering this project
> for the first time.

---

## The Core Idea

This project is building a research platform that learns how one
specific discretionary trader reads the market — and gradually
translates that reading into systematic, testable, and eventually
executable logic.

It is not an algorithmic trading system in the conventional sense.
It is not trying to discover alpha through statistical models or
machine learning on price data. It starts from the opposite end:
one trader has a way of looking at charts that produces repeatable
decisions. The project's job is to capture that way of looking,
decompose it into components, test each component against real data,
and assemble the validated components into a pipeline that can
reproduce those decisions mechanically.

The trader's judgment is the ground truth. The system's job is to
approximate it, not to replace it.

---

## The Architecture

The platform separates what the market did from what to do about it.

The first layer identifies structural price levels — places on the
chart where the market has drawn its own boundaries. These include
the opening range of the session, previous day extremes, pre-market
extremes, and proprietary structures born from momentum patterns.
Every level source produces the same standardized output: a price
or zone, a directional role, and a timestamp. The system downstream
does not know or care which source generated the level.

The second layer watches for a specific candle at one of those
levels — a candle that touches the level and rejects from it with
conviction. This is the entry signal. It is level-agnostic: the
same rejection logic applies regardless of whether the level came
from the opening range, yesterday's high, or a momentum structure
identified three hours ago. The entry layer answers one question:
did the current bar prove that this level held?

The third layer packages the entry into a standardized record — a
Trade Candidate — that captures everything known at the instant of
entry and nothing that requires hindsight. This record is frozen and
immutable. Every module downstream reads the same object.

Above these layers sits a Policy Engine that makes the subjective
decisions: which candidates to accept, which to filter, what
confluence is required, what time-of-day restrictions apply, what
position sizing to use. The policy layer is where the trader's
preferences live — configurable, testable, swappable. Different
policy profiles can be backtested against the same detection
pipeline without touching the detection logic.

---

## The Research Process

The project follows a strict epistemic discipline. No detection rule
is written until the pattern it captures has been observed repeatedly
in labeled real examples.

The lifecycle is deliberate:

A trader observes something on a chart. That observation becomes a
hypothesis. The hypothesis is tested through a discovery workspace
where the trader labels real examples — not to confirm the hypothesis,
but to understand when it holds and when it fails. The labeled data
reveals which parameters matter and what ranges they take. Only then
are the parameters implemented, and even then as configurable Lab
options rather than hardcoded rules. A parameter becomes a fixed
detector rule only after backtesting proves it adds value across
instruments and market conditions.

One interesting chart changes nothing. Evidence must accumulate.
The detector evolves through accumulated evidence, never through
isolated examples.

---

## The Trader's Brain

Before any detection logic can be written, the project captures the
trader's visual vocabulary — the natural language of how a
discretionary trader actually reads a chart. This is not a
shortcut or an ornament. It is foundational.

The vocabulary covers how the trader recognizes momentum (the
candles are covering ground, the move catches the eye), how he
identifies structural levels (the price has been here before and
something happened), how he judges a rejection candle (someone
clearly won, the close inspires confidence), how he recognizes
continuation (the trend picks up where it left off), what makes
him wait (the structure is incomplete, the entry candle is not
convincing), and what makes a level more interesting (several
different structures point to the same area).

This vocabulary exists because the distance between "I know it
when I see it" and "the machine can detect it" is where most
discretionary-to-systematic projects fail. They skip the
translation and go straight to rules. This project does the
translation first.

---

## The End Goal

The final platform allows one detection engine to consume any
number of level sources and produce standardized candidates. A
configurable policy layer filters those candidates according to
the trader's preferences. A backtester evaluates policy
combinations against historical data. A review workspace presents
candidates for human judgment, creating labeled training data that
feeds back into the research cycle.

The system progresses through stages: manual review of detected
candidates, paper trading through an execution gateway, and
eventually live trading — all consuming the same pipeline and the
same Trade Candidate object.

The architecture is designed so that adding a new level source —
a new way of identifying where the market has drawn a boundary —
requires writing one new provider module. The entry logic, the
candidate packaging, the policy layer, the backtester, and the
review workspace all work unchanged. The system does not know
where the level came from. It only knows that a level exists and
a candle reacted to it.

---

## The Philosophy

The project is built on a distinction that most trading systems
blur: trade quality and trade profitability are different things.

A good setup that gets stopped out was still a good setup. A bad
setup that happens to profit was still a bad setup. The system
evaluates the process, not the result. The trader's judgment about
whether he would take the trade — independent of outcome — is the
standard against which everything is measured.

The detector does not optimize for profit. It optimizes for
agreement with the trader's eye. Profit is the responsibility of
the policy layer, the risk management, and the market itself. The
detector's only job is to see what the trader sees.
