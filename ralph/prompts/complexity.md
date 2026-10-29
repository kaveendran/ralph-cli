You are reviewing an implementation plan before an automated build loop runs it. Each unit will be built by an AI agent in ONE focused session with a limited context, so a unit that is too complex will stall, run out of room, or be done badly.

Score each unit's complexity from 1 to 5:
1. trivial: one small change in one place;
2. small: a few closely related changes;
3. moderate: several files, one clear concern;
4. large: several concerns, or tricky integration, or a lot to verify — risky for one session;
5. too big: several units' worth of work.

For every unit scoring 4 or 5, propose how to split it: 2–4 smaller unit titles, each one a single concern that can be tested on its own.

## The units

{{UNITS}}
