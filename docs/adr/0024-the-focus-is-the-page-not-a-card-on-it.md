# The Focus is the page, not a card on it

The **Focus** was drawn as a bordered card — `1px solid var(--line)`, a `--panel`
background, a 10px radius — sitting inside the page gutter, with the
**Scrollback** padded again inside it. On a 390px phone that stacked to 30px of
horizontal inset per side (`--gut` 14 + the border + `.sb` 15), leaving a 330px
column and roughly 36 characters on a line. The readability band starts at 45.

The card is now gone from the Focus, and `.sb`'s inner padding with it: the page
gutter drops to 6px and the read runs to it.

## Why the card had to be the thing that went

A card is what distinguishes *one of many*. The Focus is at most one, by
definition — the queue is what holds the rest — so the box was drawing a
boundary against nothing and charging the read for it. That is the whole
argument, and it is why this is not a padding tweak: no amount of trimming a
number would have found it, because the number was not the mistake.

The rule that follows, and the reason this is written down: **a card marks one of
many; a singular surface is the page.** Every box that sits in a list keeps its
border — the queue rows, the Foreign rows, the **Recover** picker's list. Every
box that is the only one of its kind loses it. `.startcard` went with the Focus
for the same reason. A future reader looking at a borderless reading surface next
to a list of bordered rows should read that as the rule holding, not as an
inconsistency to fix.

## The type scale was the other candidate, and it stays at 1.25

Most of the missing width is not padding. At `--fs:1.25` a rem is 20px and the
live prose renders at ~18px, so *deleting every horizontal pixel on the page* —
full bleed to the bezel — reaches only ~43 characters on a 390px phone. Dropping
`--fs` to 1.15 would have bought more than the entire gutter did.

Rejected. ADR 0018 set 1.25 because that is what reading real Runs on a real
phone settled on, and the same test settled this one: on the device in hand the
bleed measured 45 characters, inside the band, with the type untouched. Smaller
type read worse. A decision made on a phone is only reversible on that phone,
and it did not reverse.

Consequence worth stating plainly: 45 is the *430px* device. A 390px phone lands
near 41 on the same combination, still short of the band. That is accepted — the
alternative is shrinking type that was measured, on hardware, to be the size it
should be.

## The 50px label gutter survives, unexamined

The **Record**'s `you` / `work` / `claude` column is `2.5rem` — 50px at `--fs:1.25`,
about 15% of the column, and now the largest single width cost left on the page.
A variant that dissolved it into inline hanging labels was built and not judged.
It stays because ADR 0017 bought the fixed shape deliberately: the column is
meant to be skimmable without reading a value, and inline labels are exactly the
change most likely to cost that. Spending a measured win on an unmeasured one is
how a good result gets talked back out. If the gutter is ever reopened, that
prototype is the shape of the test.

## Consequences

- `.sb` loses its `border-bottom` along with the box, so a single hairline above
  the composer is the only rule left inside the read. The header needs none: the
  **Workspace** already owns its own row (ADR 0023).
- `--gut` becomes `max(6px, …)`. The floor only ever applies below ~768px, so the
  740px column's desktop centring is untouched.
- `.zones` hardcoded `padding:8px 14px` instead of `var(--gut)` — the one
  hand-duplicated gutter on the page. It moves to the token, or the queue sheet
  sits 8px inboard of everything else the moment the gutter changes again.
- Past 900px the Focus has no edge against the 290px rail and is not given one.
  The centred `--col` is edge enough; the rail carries its own background.
