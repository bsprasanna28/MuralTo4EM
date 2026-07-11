# mural-to-4em

Converts a [Mural](https://www.mural.co/) board export (JSON) into an AdoXML file that can be imported directly into the [4EM Modeling Toolkit](https://www.omilab.org/4em/) running on ADOxx.

This is part of a university project comparing collaborative modeling tools and their export capabilities. The idea is simple: Mural is great for collaborative brainstorming but has no concept of enterprise modeling classes, while 4EM has a rich metamodel but no real-time collaboration. This script bridges the two by using sticky note **color + shape** as a convention to encode 4EM classes, then generates a properly structured AdoXML import file — including cross-submodel relations, which is the part most conversion approaches skip.

## Status

7 of 4EM's 8 submodels are validated end-to-end against real ADOxx exports: Goal Model, Business Process Model, Actors and Resources Model, Business Rule Model, Concepts Model, Technical Components and Requirements Model, plus the cross-model relation mechanism itself. Product-Service-Model (a toolkit-level addition not part of the original 6-submodel 4EM methodology) is out of scope for now.

## Usage

```bash
# Convert a board
python3 mural_to_4em.py my_board_export.json output.xml

# Check a board for problems BEFORE converting - no file written
python3 mural_to_4em.py my_board_export.json --validate

# Dev-time check: make sure no duplicate keys crept into the tables
python3 mural_to_4em.py --check-integrity
```

Run `--validate` before every real conversion attempt. It's saved me from several bad imports by catching things a failed ADOxx import would otherwise be the first sign of:
- stickies whose color/shape isn't in the palette (grouped and counted, with example text)
- dangling arrows (missing or broken endpoints — a recurring authoring mistake in Mural, easy to miss visually)
- arrow labels that don't match any known relation type

## Sticky note convention

Each 4EM class is identified by a `(color, shape)` pair. Full table is in `COLOR_SHAPE_TO_CLASS` near the top of the script — that's the single source of truth, easier to keep one table updated than duplicate it here and have it go stale. Colors were reassigned several times over the course of building this out as real board usage kept colliding with earlier guesses — if you're extending the palette, run `--check-integrity` after, and check `--validate` against any older boards before trusting them again.

## Naming a component + giving it a description

Type the name on the first line of the sticky, then a new line (Shift+Enter) for the description:

Increase Sales

Grow quarterly revenue by 15% through new marketing channels

First line becomes the instance name (used for relation references), everything after becomes the Description attribute. Single-line stickies still work fine — description just falls back to the name.

## Relations

Draw an arrow between two stickies in Mural and it becomes either:
- a same-submodel `CONNECTOR` (e.g. Goal supports Goal), or
- a cross-submodel intermodel relation (e.g. Individual plays a role linked to a Goal), automatically detected based on which submodels the two endpoints belong to

**Relation type comes from three places, in priority order:**
1. The arrow's Mural label, if it matches a known valid type **for that specific class pair** (case-insensitive fallback, exact case preferred)
2. The arrow's stroke color, if registered in `CONNECTOR_COLOR_OVERRIDE`
3. The class-pair default in `RELATION_RULES` / `RELATION_RULES_CROSS_MODEL`

If the arrow has a label but it doesn't match a known type, it's not discarded — it goes into the connector's Description field instead. This matches real 4EM behavior (confirmed: an untyped Organizational Unit relation with "part of" living in Description, not Type).

Note that label matching is scoped **per class pair**, not global — the same word can mean different things (different casing, even) in different submodels. `Supports` in the Goal Model and `supports` in a TCRM relation are not interchangeable, and treating them as one global vocabulary was a real bug caught during testing.

## Known quirks / things to watch out for

- Mural's `startRefId`/`endRefId` on arrows do **not** map directly onto ADOxx's `FROM`/`TO` — there's a consistent reversal that's already compensated for in the code, confirmed empirically across multiple test imports, for both same-model connectors and cross-model relations.
- Some 4EM classes genuinely belong to more than one submodel (e.g. `KPI` can live in the Goal Model or the Concepts Model). These use an internal disambiguation key (`KPI (Concepts)`) mapped back to the real 4EM class name (`REAL_CLASS_NAME`) so the correct class ends up in the XML regardless of which submodel it was routed to.
- Not every class template is fully verified against a real ADOxx export — see the comments in `INSTANCE_ATTR_TEMPLATES` for which ones are confirmed vs. best-guess-by-analogy. Several "obvious" guesses turned out wrong (a field guessed as a `RECORD` was actually a plain `LONGSTRING` attribute and broke an import outright; several `STRING`/`DOUBLE` guesses were actually `LONGSTRING`/`INTEGER`; an `ENUMERATION` field left empty risked the same class of failure). When in doubt, build one instance manually in ADOxx, export it, and check the real attribute structure before trusting the generated XML for that class.
- Some 4EM class pairs support more than one relation type (e.g. Goal→Goal can be Supports, Hinders, or Contradicts). Only one can be the class-pair default; extras are registered in `EXTRA_RELATION_TYPES_BY_PAIR` so they're still recognized as valid arrow labels.
- Coordinates are converted from Mural pixels to 4EM centimeters using a fixed scale factor derived from comparing a default-size sticky against a default-size 4EM node. Adjust `PX_TO_CM` if your sticky notes are a different size than the default.
- Dangling arrows (an endpoint not properly attached to a sticky in Mural) are silently skipped during conversion rather than causing an error — always run `--validate` first to catch these instead of finding out from a lower relation count than expected.

## Requirements

Python 3, no external dependencies — just the standard library.
