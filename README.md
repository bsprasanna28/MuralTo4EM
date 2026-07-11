# mural-to-4em

Converts a [Mural](https://www.mural.co/) board export (JSON) into an AdoXML file that can be imported directly into the [4EM Modeling Toolkit](https://www.omilab.org/4em/) running on ADOxx.

This is part of a university project comparing collaborative modeling tools and their export capabilities. The idea is simple: Mural is great for collaborative brainstorming but has no concept of enterprise modeling classes, while 4EM has a rich metamodel but no real-time collaboration. This script bridges the two by using sticky note **color + shape** as a convention to encode 4EM classes, then generates a properly structured AdoXML import file.

## How it works

1. Build a model in Mural using color/shape-coded sticky notes (see convention table below)
2. Export the board as JSON via the Mural API
3. Run the script to convert it to AdoXML
4. Import the generated `.xml` file into ADOxx

```bash
python3 mural_to_4em.py my_board_export.json output.xml
```

Any sticky note the script can't classify gets skipped and logged to stderr instead of silently guessed wrong, so check the console output after running it.

## Sticky note convention

Each 4EM class is identified by a `(color, shape)` pair. Full table is in `COLOR_SHAPE_TO_CLASS` near the top of the script — that's the single source of truth, easier to keep one table updated than duplicate it here and have it go stale.

Roughly: 3 colors cover the Goal Model (Goal/KPI, Problem/Cause, Constraint/Opportunity as rectangle/circle pairs), similar pairing for the other submodels. Shape doubles how many classes you can fit per color, since 24 4EM classes is a lot to cover with visually distinct colors alone.

## Naming a component + giving it a description

Type the name on the first line of the sticky, then a new line (Shift+Enter) for the description:

Increase Sales

Grow quarterly revenue by 15% through new marketing channels

First line becomes the instance name (used for relation references), everything after becomes the Description attribute. Single-line stickies still work fine — description just falls back to the name.

## Relations

Draw an arrow between two stickies in Mural and it becomes either:
- a same-submodel `CONNECTOR` (e.g. Goal supports Goal), or
- a cross-submodel intermodel relation (e.g. Individual plays a role linked to a Goal), automatically detected based on which submodels the two endpoints belong to

Relation type (Supports / Hinders / Output / Input / etc.) is inferred from the class pair via `RELATION_RULES` and `RELATION_RULES_CROSS_MODEL`. If a pair isn't in the table it falls back to a generic `"Relation"` label.

## Known quirks / things to watch out for

- Mural's `startRefId`/`endRefId` on arrows do **not** map directly onto ADOxx's `FROM`/`TO` — there's a consistent reversal that's already compensated for in the code, confirmed empirically across multiple test imports. If you ever see arrow directions come out backwards after modifying this logic, this is probably why.
- Not every class template below is fully verified against a real ADOxx export — see the comments in `INSTANCE_ATTR_TEMPLATES` for which ones are confirmed vs best-guess. When in doubt, build one instance manually in ADOxx, export it, and check the real attribute structure before trusting the generated XML for that class.
- Some 4EM class pairs support more than one relation type (e.g. Goal→Goal can be Supports, Hinders, or Contradicts). This script can only encode one default per pair — arrow stroke color is available as a second disambiguation cue via `CONNECTOR_COLOR_OVERRIDE` if you need more than one relation type between the same two classes.
- Coordinates are converted from Mural pixels to 4EM centimeters using a fixed scale factor derived from comparing a default-size sticky against a default-size 4EM node. Adjust `PX_TO_CM` if your sticky notes are a different size than the default.

## Requirements

Python 3, no external dependencies — just the standard library.

## Status

Working end to end for Goal Model, Business Process Model, and Actors and Resources Model, including cross-model relations. Coverage of the remaining submodels (Concepts, Technical Components, Product-Service, Business Rule) is implemented but less thoroughly tested — treat those as a good starting point rather than fully proven.
