"""
mural_to_4em.py

Converts a Mural JSON export into a 4EM-compatible ADOxx XML import file,
supporting MULTIPLE submodels and BOTH relation mechanisms 4EM uses:

  - Same-submodel relations  -> <CONNECTOR class="4EM_Relation"> with FROM/TO
  - Cross-submodel relations -> a <RECORD name="Intermodel-Relations"> block
                                  embedded INSIDE the source <INSTANCE>,
                                  using <INTERREF>/<IREF> to point at the
                                  target instance by name (not by ID).

Built directly against THREE real reference exports:
  - mural_export_new.json   (Mural REST API "list widgets" response)
  - new_export.xml          (ADOxx export: hand-built Goal Model)
  - example_2_4em.xml       (ADOxx export: hand-built ARM model, containing
                             one same-model connector AND one cross-model
                             intermodel relation to a Goal Model instance)

Key facts learned from those files (do not re-guess these):
  1. Sticky note text is NOT in the "text" field (often empty). Real content
     is in "htmlText" as an HTML fragment.
  2. Mural connectors use "startRefId"/"endRefId".
  3. ADOxx CONNECTOR <FROM>/<TO> reference elements by NAME + CLASS, not ID.
  4. Empirically, Mural's start/end came out reversed vs 4EM's FROM/TO on
     2/2 tested same-model arrows -> we swap FROM/TO for same-model relations.
     NOT YET VERIFIED for cross-model (intermodel) relations - treat with
     caution and check your first cross-model test carefully.
  5. "index" in Position/Positions is a counter that resets PER MODEL
     (not global across the whole file), incrementing in creation order.
  6. Coordinates are in cm; Mural gives pixels. Scale factor derived from
     the reference Goal export: 168px sticky <-> 4cm node -> 0.0238.
  7. Cross-model relations use a DIFFERENT "Type" enumeration vocabulary
     than same-model connectors (e.g. "play" vs "plays" seen in real data).
     This is UNVERIFIED beyond that one example - confirm the valid values
     via the ADOxx dropdown before trusting RELATION_RULES_CROSS_MODEL below.
  8. tobjname/tmodelname in <IREF> must EXACTLY match the target instance's
     "name" and the target MODEL's "name" attribute - this script keeps
     MODEL name == modeltype string to guarantee that match automatically.
"""

import json
import re
import sys
from html import unescape
from pathlib import Path

# ---------------------------------------------------------------------------
# 1. CONFIG: the Mural <-> 4EM convention. Extend this as your board grows.
# ---------------------------------------------------------------------------

# Classification key is now (backgroundColor, shape) instead of color alone.
# Rationale (confirmed by real test data): with 24 4EM classes, relying on
# color alone requires ~24 distinct, easily-confusable hex values. Adding
# shape (Mural sticky notes support "rectangle" and "circle") doubles the
# encoding space per color, so each color only needs to be reused for at
# most 2 classes - much easier to build a board against by hand.
# Two colors below are adopted from real board usage rather than invented:
# #86E6D9FF (Process) and #9EDCFAFF (Information Set) were both confirmed
# from actual Mural exports, so kept as-is to avoid relearning them.
COLOR_SHAPE_TO_CLASS = {
    # --- Goal Model --- (colors below match the real board, not invented)
    ("#AAED92FF", "rectangle"): "Goal",
    ("#AAED92FF", "circle"): "KPI",
    ("#F6A324FF", "rectangle"): "Problem",
    ("#9EDCFAFF", "rectangle"): "Cause",
    ("#FFFFFFFF", "rectangle"): "Constraint",
    ("#459C5BFF", "rectangle"): "Opportunity",
    # --- Business Process Model ---
    ("#FFFFFFFF", "circle"): "Process",                # UPDATED - was #86E6D9FF/rectangle in an earlier, simpler test board
    ("#EDEDEDFF", "rectangle"): "External Process",     # UPDATED - was #86E6D9FF/circle
    ("#FBF9E5FF", "rectangle"): "Information Set",     # actual real color that landed after 3 attempts - Mural's picker kept landing near-neighbors of already-taken colors
    ("#EDEDEDFF", "circle"): "Split (AND)",
    ("#F7F7F7FF", "circle"): "Join (AND)",
    # --- Actors and Resources Model ---
    ("#FEBBBEFF", "rectangle"): "Individual",
    ("#9E7EE6FF", "rectangle"): "Role",
    ("#D4D4D4FF", "rectangle"): "Resource",
    ("#F7F7F7FF", "rectangle"): "Organizational Unit",
    # --- Concepts Model ---
    ("#E6C003FF", "circle"): "Concept",
    ("#D4D4D4FF", "circle"): "Attribute",
    ("#459C5BFF", "circle"): "KPI (Concepts)",   # KPI can live in EITHER Goal Model or Concepts Model in real 4EM - see REAL_CLASS_NAME below
    # --- Technical Components and Requirements Model ---
    ("#D8C7FFFF", "circle"): "IS Technical Component",
    ("#9EDCFAFF", "circle"): "IS Requirement",
    # --- Product-Service-Model ---
    ("#A1887FFF", "rectangle"): "Component",
    ("#A1887FFF", "circle"): "Unspecific/Product/Service",
    ("#FFE082FF", "rectangle"): "Feature",
    ("#FFE082FF", "circle"): "PartOF (AND)",
    # --- Business Rule Model ---
    ("#B3B3B3FF", "rectangle"): "Rule",
}

# Which 4EM submodel each class belongs to: (modeltype string, libtype)
CLASS_TO_MODEL = {
    "Goal": ("Goal Model", "bp"),
    "Problem": ("Goal Model", "bp"),
    "Constraint": ("Goal Model", "bp"),
    "Cause": ("Goal Model", "bp"),
    "Opportunity": ("Goal Model", "bp"),
    # KPI genuinely lives in EITHER Goal Model or Concepts Model in real 4EM
    # (confirmed: instances found declared in both sections of the reference
    # file). Since one internal class name can only map to one submodel here,
    # two Mural colors are used to disambiguate which one a given KPI sticky
    # targets: plain "KPI" (paired with Goal's green, circle shape) means
    # Goal Model; "KPI (Concepts)" is a separate internal key for the
    # Concepts Model version, mapped back to the real class name "KPI" when
    # writing XML - see REAL_CLASS_NAME below.
    "KPI": ("Goal Model", "bp"),
    "KPI (Concepts)": ("Concepts Model", "bp"),
    "Rule": ("Business Rule Model", "bp"),  # NOTE: singular "Rule", confirmed from real export (was wrongly "Business Rules Model" before)
    "Concept": ("Concepts Model", "bp"),
    "Attribute": ("Concepts Model", "bp"),
    "Process": ("Business Process Model", "bp"),
    "External Process": ("Business Process Model", "bp"),
    "Information Set": ("Business Process Model", "bp"),
    "Split (AND)": ("Business Process Model", "bp"),
    "Join (AND)": ("Business Process Model", "bp"),
    "Individual": ("Actors and Resources Model", "bp"),
    "Role": ("Actors and Resources Model", "bp"),
    "Resource": ("Actors and Resources Model", "bp"),
    "Organizational Unit": ("Actors and Resources Model", "bp"),
    "IS Technical Component": ("Technical Components and Requirements Model", "bp"),
    "IS Requirement": ("Technical Components and Requirements Model", "bp"),
    "Component": ("Product-Service-Model", "bp"),
    "Unspecific/Product/Service": ("Product-Service-Model", "bp"),
    "Feature": ("Product-Service-Model", "bp"),
    "PartOF (AND)": ("Product-Service-Model", "bp"),
}

# Relation type for SAME-SUBMODEL connectors (verified vocabulary: "plays",
# "Supports", "Hinders" seen in real exports; others are best-guess).
# Relation type for SAME-SUBMODEL connectors. Confirmed vocabulary extracted
# directly from 4EM_Example_Models.adl (a comprehensive real reference model).
# NOTE: several class pairs support MULTIPLE possible relation types in real
# 4EM (e.g. Goal-Goal can be Supports/Hinders/Contradicts; Concept-Concept
# can be 1:1/1:n/n:m). This simple dict can only encode ONE default per pair -
# that's a genuine limitation of pure color-based classification worth
# discussing in your evaluation. Use CONNECTOR_COLOR_OVERRIDE (arrow stroke
# color) as a second cue to disambiguate when you need more than one.
RELATION_RULES = {
    ("Goal", "Goal"): "Supports",              # also seen: Hinders, Contradicts
    ("Problem", "Goal"): "Hinders",
    ("Constraint", "Goal"): "Hinders",
    ("Cause", "Problem"): "Causes",
    ("Cause", "Opportunity"): "Causes",
    ("Opportunity", "Goal"): "Supports",
    ("Goal", "KPI"): "measured by",
    ("Goal", "Rule"): "Motivates",              # unverified guess, kept from earlier version
    ("Individual", "Role"): "plays",
    ("Resource", "Organizational Unit"): "belongs to",
    ("Resource", "Resource"): "interacts with",
    ("Role", "Organizational Unit"): "works in",
    ("Role", "Resource"): "maintains",
    ("Organizational Unit", "Organizational Unit"): "",
    ("Process", "Process"): "Requires",
    ("Process", "Information Set"): "Output",
    ("Information Set", "Process"): "Input",
    ("External Process", "Information Set"): "Output",
    ("Split (AND)", "Process"): "",
    ("Split (AND)", "External Process"): "",
    ("Information Set", "Split (AND)"): "",
    ("Information Set", "Join (AND)"): "",
    ("Join (AND)", "Process"): "",
    ("Concept", "Concept"): "1:n",              # also seen: 1:1, n:m
    ("Concept", "Attribute"): "",
    ("KPI", "Concept"): "refers to",
    ("Component", "PartOF (AND)"): "",
    ("PartOF (AND)", "Unspecific/Product/Service"): "",
    ("Feature", "Component"): "requires",
    ("IS Technical Component", "IS Technical Component"): "supports",  # also seen: hinders
    ("IS Technical Component", "IS Requirement"): "has requirement",
}

# Relation type for CROSS-SUBMODEL (intermodel) relations. Confirmed from
# real exports: Individual->Goal ("play") and Rule->Process ("supports").
# Everything else here is UNVERIFIED - check the ADOxx dropdown for the real
# allowed value before trusting entries you add.
RELATION_RULES_CROSS_MODEL = {
    ("Individual", "Goal"): "play",
    ("Role", "Goal"): "is responsible for",  # CORRECTED - was wrongly "play", confirmed real value from ARM export
    ("Rule", "Process"): "supports",
    ("Organizational Unit", "IS Technical Component"): "defines",   # confirmed
    ("Role", "Process"): "performs",                                # confirmed
    ("Process", "IS Technical Component"): "requires",              # confirmed
    ("Process", "IS Requirement"): "motivates",                     # confirmed
    ("Process", "Concept"): "uses",                                 # confirmed
    ("External Process", "Concept"): "creates",                     # confirmed
}

CONNECTOR_COLOR_OVERRIDE = {
    "#E53E3EFF": "Contradicts",
}

# Extra confirmed-valid relation types per class pair, alongside the single
# default each pair already has in RELATION_RULES / RELATION_RULES_CROSS_MODEL
# (e.g. Goal-Goal can be Supports OR Contradicts OR Hinders; IS Technical
# Component <-> IS Technical Component can be "supports" OR "hinders" -
# NOTE the different casing convention from Goal Model's "Hinders"!). Keyed
# by (cross_model, src_cls, tgt_cls) so casing stays scoped to the pair that
# actually uses it - a global set previously caused "hinders" (TCRM,
# lowercase) to incorrectly resolve to "Hinders" (Goal Model, capitalized)
# since both are valid SOMEWHERE but not interchangeable.
EXTRA_RELATION_TYPES_BY_PAIR = {
    (False, "Goal", "Goal"): ("Contradicts",),
    (False, "Concept", "Concept"): ("1:1", "n:m"),
    (False, "IS Technical Component", "IS Technical Component"): ("hinders",),
}


def known_types_for_pair(cross_model, src_cls, tgt_cls):
    """Set of valid relation-type strings (real casing) for this exact pair,
    including the table default plus any registered extras."""
    table = RELATION_RULES_CROSS_MODEL if cross_model else RELATION_RULES
    types = set()
    default = table.get((src_cls, tgt_cls))
    if default:
        types.add(default)
    types.update(EXTRA_RELATION_TYPES_BY_PAIR.get((cross_model, src_cls, tgt_cls), ()))
    return types


# Fallback global set (used only when a label doesn't match anything scoped
# to the specific pair - keeps some flexibility for genuinely cross-cutting
# labels without letting them override a pair's own confirmed casing).
KNOWN_RELATION_TYPES_EXACT = set()
KNOWN_RELATION_TYPES_CI = {}
for _table in (RELATION_RULES, RELATION_RULES_CROSS_MODEL):
    for _v in _table.values():
        if _v:
            KNOWN_RELATION_TYPES_EXACT.add(_v)
            KNOWN_RELATION_TYPES_CI.setdefault(_v.lower(), _v)
for _pair_extras in EXTRA_RELATION_TYPES_BY_PAIR.values():
    for _v in _pair_extras:
        KNOWN_RELATION_TYPES_EXACT.add(_v)
        KNOWN_RELATION_TYPES_CI.setdefault(_v.lower(), _v)

PX_TO_CM = 4.0 / 168.0

INSTANCE_ATTR_TEMPLATES = {
    "Goal": """<ATTRIBUTE name="Position" type="STRING">{position}</ATTRIBUTE>
<ATTRIBUTE name="External tool coupling" type="STRING"></ATTRIBUTE>
<ATTRIBUTE name="Description" type="LONGSTRING">{desc}</ATTRIBUTE>
<ATTRIBUTE name="Criticality" type="ENUMERATION">Low</ATTRIBUTE>
<ATTRIBUTE name="Priority" type="ENUMERATION">Low</ATTRIBUTE>
{intermodel_relations}
<INTERREF name="Decomposition"></INTERREF>
<ATTRIBUTE name="Defined by" type="LONGSTRING"></ATTRIBUTE>
<RECORD name="Attributes"></RECORD>""",
    "Problem": """<ATTRIBUTE name="Position" type="STRING">{position}</ATTRIBUTE>
<ATTRIBUTE name="External tool coupling" type="STRING"></ATTRIBUTE>
{intermodel_relations}
<INTERREF name="Decomposition"></INTERREF>
<RECORD name="Attributes"></RECORD>
<ATTRIBUTE name="Defined by" type="LONGSTRING"></ATTRIBUTE>
<ATTRIBUTE name="Priority" type="ENUMERATION">Low</ATTRIBUTE>
<ATTRIBUTE name="Criticality" type="ENUMERATION">Low</ATTRIBUTE>
<ATTRIBUTE name="Description" type="LONGSTRING">{desc}</ATTRIBUTE>
<ATTRIBUTE name="type" type="ENUMERATION">Problem</ATTRIBUTE>""",
    "Individual": """<ATTRIBUTE name="Position" type="STRING">{position}</ATTRIBUTE>
<ATTRIBUTE name="External tool coupling" type="STRING"></ATTRIBUTE>
<ATTRIBUTE name="Description" type="LONGSTRING">{desc}</ATTRIBUTE>
{intermodel_relations}
<INTERREF name="Decomposition"></INTERREF>
<RECORD name="Attributes"></RECORD>""",
    "Role": """<ATTRIBUTE name="Position" type="STRING">{position}</ATTRIBUTE>
<ATTRIBUTE name="External tool coupling" type="STRING"></ATTRIBUTE>
<ATTRIBUTE name="Description" type="LONGSTRING">{desc}</ATTRIBUTE>
{intermodel_relations}
<INTERREF name="Decomposition"></INTERREF>
<ATTRIBUTE name="Qualification" type="LONGSTRING"></ATTRIBUTE>
<ATTRIBUTE name="Number of Employees with this Role" type="INTEGER">0</ATTRIBUTE>
<RECORD name="Attributes"></RECORD>""",
    "Process": """<ATTRIBUTE name="Position" type="STRING">{position}</ATTRIBUTE>
<ATTRIBUTE name="External tool coupling" type="STRING"></ATTRIBUTE>
<ATTRIBUTE name="Description" type="LONGSTRING">{desc}</ATTRIBUTE>
<ATTRIBUTE name="Decomposed Process" type="STRING"></ATTRIBUTE>
{intermodel_relations}
<INTERREF name="Decomposition"></INTERREF>
<ATTRIBUTE name="Execution Time" type="DOUBLE">0</ATTRIBUTE>
<ATTRIBUTE name="Complexity" type="DOUBLE">0</ATTRIBUTE>
<ATTRIBUTE name="Type" type="LONGSTRING"></ATTRIBUTE>
<RECORD name="Attributes"></RECORD>""",
    "Information Set": """<ATTRIBUTE name="Position" type="STRING">{position}</ATTRIBUTE>
<ATTRIBUTE name="External tool coupling" type="STRING"></ATTRIBUTE>
<ATTRIBUTE name="Description" type="LONGSTRING">{desc}</ATTRIBUTE>
<ATTRIBUTE name="Type" type="ENUMERATION">Information Set</ATTRIBUTE>
{intermodel_relations}
<INTERREF name="Decomposition"></INTERREF>
<RECORD name="Attributes"></RECORD>""",
    # --- New classes below, extracted from 4EM_Example_Models.adl ---
    "Constraint": """<ATTRIBUTE name="Position" type="STRING">{position}</ATTRIBUTE>
<ATTRIBUTE name="External tool coupling" type="STRING"></ATTRIBUTE>
<ATTRIBUTE name="Description" type="LONGSTRING">{desc}</ATTRIBUTE>
{intermodel_relations}
<INTERREF name="Decomposition"></INTERREF>
<RECORD name="Attributes"></RECORD>
<ATTRIBUTE name="Defined by" type="LONGSTRING"></ATTRIBUTE>""",
    "Cause": """<ATTRIBUTE name="Position" type="STRING">{position}</ATTRIBUTE>
<ATTRIBUTE name="External tool coupling" type="STRING"></ATTRIBUTE>
<ATTRIBUTE name="Description" type="LONGSTRING">{desc}</ATTRIBUTE>
{intermodel_relations}
<INTERREF name="Decomposition"></INTERREF>
<RECORD name="Attributes"></RECORD>
<ATTRIBUTE name="Defined by" type="LONGSTRING"></ATTRIBUTE>""",
    "Opportunity": """<ATTRIBUTE name="Position" type="STRING">{position}</ATTRIBUTE>
<ATTRIBUTE name="External tool coupling" type="STRING"></ATTRIBUTE>
<ATTRIBUTE name="Description" type="LONGSTRING">{desc}</ATTRIBUTE>
{intermodel_relations}
<INTERREF name="Decomposition"></INTERREF>
<RECORD name="Attributes"></RECORD>
<ATTRIBUTE name="Defined by" type="LONGSTRING"></ATTRIBUTE>""",
    # KPI: attribute types now CONFIRMED from a real ADOxx export
    # (Goal_Model_4em_Export.xml) - "KPI Log" is a plain LONGSTRING attribute,
    # NOT a RECORD as originally guessed (that guess caused a real
    # WRONG_ATTRIBUTE_TYPE import error - ADOxx tried to parse it as a
    # table/record when it should be simple text). "Designation" is
    # LONGSTRING, not STRING as originally guessed.
    "KPI": """<ATTRIBUTE name="Position" type="STRING">{position}</ATTRIBUTE>
<ATTRIBUTE name="External tool coupling" type="STRING"></ATTRIBUTE>
<ATTRIBUTE name="Description" type="LONGSTRING">{desc}</ATTRIBUTE>
{intermodel_relations}
<INTERREF name="Decomposition"></INTERREF>
<ATTRIBUTE name="Defined by" type="LONGSTRING"></ATTRIBUTE>
<RECORD name="Attributes"></RECORD>
<ATTRIBUTE name="Target Value" type="STRING"></ATTRIBUTE>
<ATTRIBUTE name="KPI Log" type="LONGSTRING"></ATTRIBUTE>
<ATTRIBUTE name="Designation" type="LONGSTRING"></ATTRIBUTE>""",
    # Rule: "Type" is ENUMERATION in real data (e.g. "Derivation Rule") -
    # exact full list of valid enum values NOT verified, "Derivation Rule"
    # confirmed as one legal value. "Formal description in advanced language"
    # inferred LONGSTRING (freeform text).
    "Rule": """<ATTRIBUTE name="Position" type="STRING">{position}</ATTRIBUTE>
<ATTRIBUTE name="External tool coupling" type="STRING"></ATTRIBUTE>
<ATTRIBUTE name="Type" type="ENUMERATION">Derivation Rule</ATTRIBUTE>
<ATTRIBUTE name="Description" type="LONGSTRING">{desc}</ATTRIBUTE>
{intermodel_relations}
<INTERREF name="Decomposition"></INTERREF>
<RECORD name="Attributes"></RECORD>
<ATTRIBUTE name="Formal description in advanced language" type="LONGSTRING"></ATTRIBUTE>""",
    "Concept": """<ATTRIBUTE name="Position" type="STRING">{position}</ATTRIBUTE>
<ATTRIBUTE name="External tool coupling" type="STRING"></ATTRIBUTE>
<ATTRIBUTE name="Description" type="LONGSTRING">{desc}</ATTRIBUTE>
<INTERREF name="Decomposition"></INTERREF>
<ATTRIBUTE name="Complexity" type="DOUBLE">0</ATTRIBUTE>
<ATTRIBUTE name="Execution Time" type="DOUBLE">0</ATTRIBUTE>
<RECORD name="Attributes"></RECORD>
{intermodel_relations}""",
    # Attribute (the CLASS, e.g. "Email Address") - not to be confused with
    # the generic <ATTRIBUTE> XML tag used everywhere else in this file.
    # "Data Type" CONFIRMED as ENUMERATION (was wrongly STRING) - "String" is
    # a confirmed valid value, used as default rather than leaving blank
    # since blank ENUMERATION values are a known source of import errors.
    "Attribute": """<ATTRIBUTE name="Position" type="STRING">{position}</ATTRIBUTE>
<ATTRIBUTE name="External tool coupling" type="STRING"></ATTRIBUTE>
<ATTRIBUTE name="Description" type="LONGSTRING">{desc}</ATTRIBUTE>
{intermodel_relations}
<INTERREF name="Decomposition"></INTERREF>
<RECORD name="Attributes"></RECORD>
<ATTRIBUTE name="Data Type" type="ENUMERATION">String</ATTRIBUTE>
<ATTRIBUTE name="Value Range" type="STRING"></ATTRIBUTE>""",
    # External Process: confirmed identical attribute set to Process.
    "External Process": """<ATTRIBUTE name="Position" type="STRING">{position}</ATTRIBUTE>
<ATTRIBUTE name="External tool coupling" type="STRING"></ATTRIBUTE>
<ATTRIBUTE name="Description" type="LONGSTRING">{desc}</ATTRIBUTE>
{intermodel_relations}
<INTERREF name="Decomposition"></INTERREF>
<ATTRIBUTE name="Execution Time" type="DOUBLE">0</ATTRIBUTE>
<ATTRIBUTE name="Complexity" type="DOUBLE">0</ATTRIBUTE>
<ATTRIBUTE name="Type" type="LONGSTRING"></ATTRIBUTE>
<RECORD name="Attributes"></RECORD>""",
    # Split (AND) / Join (AND): structural routing nodes, no Description or
    # Intermodel-Relations in real data - just position. Do not add
    # {intermodel_relations} here; leave as-is.
    "Split (AND)": """<ATTRIBUTE name="Position" type="STRING">{position}</ATTRIBUTE>
<ATTRIBUTE name="External tool coupling" type="STRING"></ATTRIBUTE>""",
    "Join (AND)": """<ATTRIBUTE name="Position" type="STRING">{position}</ATTRIBUTE>
<ATTRIBUTE name="External tool coupling" type="STRING"></ATTRIBUTE>""",
    # Resource: Location/Quantity types CONFIRMED from real ADOxx export
    # (ActorsResources_Model_4em_Export.xml) - was wrongly STRING/DOUBLE.
    "Resource": """<ATTRIBUTE name="Position" type="STRING">{position}</ATTRIBUTE>
<ATTRIBUTE name="External tool coupling" type="STRING"></ATTRIBUTE>
<ATTRIBUTE name="Description" type="LONGSTRING">{desc}</ATTRIBUTE>
{intermodel_relations}
<INTERREF name="Decomposition"></INTERREF>
<ATTRIBUTE name="Location" type="LONGSTRING"></ATTRIBUTE>
<ATTRIBUTE name="Quantity" type="INTEGER">0</ATTRIBUTE>
<RECORD name="Attributes"></RECORD>""",
    # Organizational Unit: Location type CONFIRMED LONGSTRING (was STRING).
    "Organizational Unit": """<ATTRIBUTE name="Position" type="STRING">{position}</ATTRIBUTE>
<ATTRIBUTE name="External tool coupling" type="STRING"></ATTRIBUTE>
<ATTRIBUTE name="Description" type="LONGSTRING">{desc}</ATTRIBUTE>
{intermodel_relations}
<INTERREF name="Decomposition"></INTERREF>
<ATTRIBUTE name="Location" type="LONGSTRING"></ATTRIBUTE>
<RECORD name="Attributes"></RECORD>""",
    # IS Technical Component: Location/Quantity fixed BY ANALOGY to Resource
    # (same attribute names, same likely pattern) - not yet independently
    # verified against a real Technical Components Model export.
    "IS Technical Component": """<ATTRIBUTE name="Position" type="STRING">{position}</ATTRIBUTE>
<ATTRIBUTE name="External tool coupling" type="STRING"></ATTRIBUTE>
<ATTRIBUTE name="Description" type="LONGSTRING">{desc}</ATTRIBUTE>
{intermodel_relations}
<INTERREF name="Decomposition"></INTERREF>
<ATTRIBUTE name="Location" type="LONGSTRING"></ATTRIBUTE>
<ATTRIBUTE name="Quantity" type="INTEGER">0</ATTRIBUTE>
<RECORD name="Attributes"></RECORD>""",
    "IS Requirement": """<ATTRIBUTE name="Position" type="STRING">{position}</ATTRIBUTE>
<ATTRIBUTE name="External tool coupling" type="STRING"></ATTRIBUTE>
<ATTRIBUTE name="Description" type="LONGSTRING">{desc}</ATTRIBUTE>
<ATTRIBUTE name="Type" type="ENUMERATION">Functional</ATTRIBUTE>
{intermodel_relations}
<INTERREF name="Decomposition"></INTERREF>
<RECORD name="Attributes"></RECORD>""",
    # Component: Location/Quantity fixed BY ANALOGY to Resource - not yet
    # independently verified against a real Product-Service-Model export.
    "Component": """<ATTRIBUTE name="Position" type="STRING">{position}</ATTRIBUTE>
<ATTRIBUTE name="External tool coupling" type="STRING"></ATTRIBUTE>
<ATTRIBUTE name="Description" type="LONGSTRING">{desc}</ATTRIBUTE>
<INTERREF name="Decomposition"></INTERREF>
<ATTRIBUTE name="Quantity" type="INTEGER">0</ATTRIBUTE>
<ATTRIBUTE name="Location" type="LONGSTRING"></ATTRIBUTE>
{intermodel_relations}""",
    # Unspecific/Product/Service: "Attribute" field here is a RECORD (list of
    # linked Attribute-class instances), distinct from the "Attribute" class
    # itself - kept as an empty RECORD placeholder, unverified beyond that.
    "Unspecific/Product/Service": """<ATTRIBUTE name="Position" type="STRING">{position}</ATTRIBUTE>
<ATTRIBUTE name="External tool coupling" type="STRING"></ATTRIBUTE>
<ATTRIBUTE name="Specification" type="LONGSTRING"></ATTRIBUTE>
<RECORD name="Attribute"></RECORD>
<ATTRIBUTE name="Description" type="LONGSTRING">{desc}</ATTRIBUTE>
{intermodel_relations}
<INTERREF name="Decomposition"></INTERREF>""",
    "Feature": """<ATTRIBUTE name="Position" type="STRING">{position}</ATTRIBUTE>
<ATTRIBUTE name="External tool coupling" type="STRING"></ATTRIBUTE>
<ATTRIBUTE name="Description" type="LONGSTRING">{desc}</ATTRIBUTE>
<RECORD name="Attribute"></RECORD>
{intermodel_relations}
<INTERREF name="Decomposition"></INTERREF>""",
    # PartOF (AND): structural decomposition node, like Split/Join. The
    # "__Conversion__" internal system attribute seen in real data is
    # intentionally omitted - it looks ADOxx-internal, not user data.
    "PartOF (AND)": """<ATTRIBUTE name="Position" type="STRING">{position}</ATTRIBUTE>
<ATTRIBUTE name="External tool coupling" type="STRING"></ATTRIBUTE>""",
}
# KPI (Concepts) uses the identical real 4EM attribute structure as KPI -
# same class, just routed to a different submodel file. See CLASS_TO_MODEL.
INSTANCE_ATTR_TEMPLATES["KPI (Concepts)"] = INSTANCE_ATTR_TEMPLATES["KPI"]

# Maps an internal disambiguation key back to the REAL 4EM class name that
# must be written into the XML (class="...", relation FROM/TO, IREF
# tclassname). Only needed for classes like KPI that have more than one
# internal key because they can live in more than one submodel - everything
# else maps to itself.
REAL_CLASS_NAME = {
    "KPI (Concepts)": "KPI",
}


def real_class(cls):
    return REAL_CLASS_NAME.get(cls, cls)


# NOTE: Goal, Problem, Individual, Role, Process, Information Set are all
# verified against real exports. For every other class, repeat the
# manual-export trick before trusting it.
DEFAULT_TEMPLATE = """<ATTRIBUTE name="Position" type="STRING">{position}</ATTRIBUTE>
<ATTRIBUTE name="Description" type="LONGSTRING">{desc}</ATTRIBUTE>
{intermodel_relations}"""


def strip_html(html_text: str) -> str:
    if not html_text:
        return ""
    text = re.sub(r"<[^>]+>", " ", html_text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_name_and_description(html_text: str):
    """
    Convention: first line of a sticky's text = Name, any further line(s) =
    Description. Mural stores each line as its own <div> in htmlText, e.g.
    '<html><div><span>Goal 1</span></div><div><span>Increase Q4 revenue via
    new channels</span></div></html>'. A sticky with only one line gets an
    empty description (caller should fall back to using the name).
    """
    if not html_text:
        return "", ""
    lines = re.findall(r"<div[^>]*>(.*?)</div>", html_text, re.S)
    cleaned = []
    for line in lines:
        t = re.sub(r"<[^>]+>", " ", line)
        t = unescape(t)
        t = re.sub(r"\s+", " ", t).strip()
        if t:
            cleaned.append(t)
    if not cleaned:
        return "", ""
    name = cleaned[0]
    description = " ".join(cleaned[1:])
    return name, description


def px_to_cm(px: float) -> float:
    return round(px * PX_TO_CM, 2)


def load_widgets(mural_json_path: str):
    data = json.loads(Path(mural_json_path).read_text(encoding="utf-8"))
    return data["value"] if "value" in data else data


def classify_sticky(widget):
    color = widget.get("style", {}).get("backgroundColor")
    shape = widget.get("shape")
    return COLOR_SHAPE_TO_CLASS.get((color, shape))


def build_registry(widgets):
    """mural widget id -> {name, description, cls, submodel, x, y, w, h}"""
    registry = {}
    for w in widgets:
        if w.get("type") != "sticky note":
            continue
        cls = classify_sticky(w)
        if cls is None:
            print(f"  [skip] unclassified sticky id={w['id']} "
                  f"color={w.get('style', {}).get('backgroundColor')} "
                  f"shape={w.get('shape')} "
                  f"text={strip_html(w.get('htmlText', ''))!r}", file=sys.stderr)
            continue
        modeltype, libtype = CLASS_TO_MODEL.get(cls, ("Goal Model", "bp"))
        name, description = parse_name_and_description(w.get("htmlText", ""))
        if not name:
            name = f"{cls} (untitled)"
        if not description:
            description = name  # fallback: single-line stickies keep old behavior
        registry[w["id"]] = {
            "name": name,
            "description": description,
            "cls": cls,
            "real_cls": real_class(cls),
            "modeltype": modeltype,
            "libtype": libtype,
            "x": px_to_cm(w["x"]),
            "y": px_to_cm(w["y"]),
            "w": px_to_cm(w["width"]),
            "h": px_to_cm(w["height"]),
        }
    return registry


def infer_relation_type(src_cls, tgt_cls, stroke_color, cross_model, label_text=None):
    # Priority: explicit Mural arrow label, checked in order:
    #   1. exact-case match against types known for THIS specific class pair
    #   2. case-insensitive match against types known for THIS pair
    #   3. exact-case match against the global set (cross-pair fallback)
    #   4. case-insensitive match against the global set
    # then stroke-color override > class-pair default.
    # Returns (rel_type, label_used_as_type). When a label exists but isn't
    # recognized, label_used_as_type is False - confirmed by real data
    # (Organizational Unit "part of" relation) that such labels are meant to
    # go in the connector's Description field instead of being discarded.
    if label_text:
        stripped = label_text.strip()
        pair_types = known_types_for_pair(cross_model, src_cls, tgt_cls)
        if stripped in pair_types:
            return stripped, True
        pair_ci = {t.lower(): t for t in pair_types}
        if stripped.lower() in pair_ci:
            return pair_ci[stripped.lower()], True
        if stripped in KNOWN_RELATION_TYPES_EXACT:
            return stripped, True  # exact-case match - trust as typed
        known_ci = KNOWN_RELATION_TYPES_CI.get(stripped.lower())
        if known_ci:
            return known_ci, True  # best-effort casing from a different context
        print(f"  [note] arrow label {label_text!r} is not a recognized "
              f"relation type - using class-pair default for Type, label "
              f"goes into Description instead for "
              f"({src_cls} -> {tgt_cls})", file=sys.stderr)
    if stroke_color in CONNECTOR_COLOR_OVERRIDE:
        return CONNECTOR_COLOR_OVERRIDE[stroke_color], False
    table = RELATION_RULES_CROSS_MODEL if cross_model else RELATION_RULES
    return table.get((src_cls, tgt_cls), "Relation"), False


def extract_arrow_label(widget):
    label = widget.get("label")
    if not label:
        return None
    labels = label.get("labels") or []
    if not labels:
        return None
    text = (labels[0].get("text") or "").strip()
    return text or None


def collect_arrows(widgets, registry):
    """Return (same_model_arrows, cross_model_arrows), each a list of dicts."""
    same, cross = [], []
    for w in widgets:
        if w.get("type") != "arrow":
            continue
        src_id, tgt_id = w.get("startRefId"), w.get("endRefId")
        if src_id not in registry or tgt_id not in registry:
            print(f"  [skip] connector references unclassified widget: "
                  f"{src_id} -> {tgt_id}", file=sys.stderr)
            continue
        src, tgt = registry[src_id], registry[tgt_id]
        stroke = w.get("style", {}).get("strokeColor")
        label_text = extract_arrow_label(w)
        entry = {"src": src, "tgt": tgt, "stroke": stroke, "label_text": label_text}
        if src["modeltype"] == tgt["modeltype"]:
            same.append(entry)
        else:
            cross.append(entry)
    return same, cross


def build_intermodel_relations_by_source(cross_arrows):
    """
    mural source-widget name -> list of RECORD ROW xml strings, one per
    cross-model relation originating at that instance.

    CONFIRMED (via test): drawing an arrow FROM Individual TO Goal in Mural
    produced, with the naive mapping, a record on Goal pointing at
    Individual - i.e. reversed, same as the same-model connector case.
    Mural's "startRefId" widget is therefore the one that should be the
    IREF TARGET, and the "endRefId" widget is the one that should HOLD
    the record. This mirrors the FROM/TO swap already applied in
    build_connectors_xml.
    """
    by_source = {}
    row_counter = 40000
    for entry in cross_arrows:
        mural_start = entry["src"]   # Mural startRefId widget -> IREF target
        mural_end = entry["tgt"]     # Mural endRefId widget   -> record holder
        holder, pointed_at = mural_end, mural_start
        rel_type, _ = infer_relation_type(holder["real_cls"], pointed_at["real_cls"], entry["stroke"], cross_model=True, label_text=entry.get("label_text"))
        row_xml = (
            f'<ROW id="row.{row_counter}" number="1">\n'
            f'<ATTRIBUTE name="Type" type="ENUMERATION">{rel_type}</ATTRIBUTE>\n'
            f'<INTERREF name="interref">\n'
            f'<IREF type="objectreference" tmodeltype="{pointed_at["modeltype"]}" '
            f'tmodelname="{pointed_at["modeltype"]}" tmodelver="" '
            f'tclassname="{pointed_at["real_cls"]}" tobjname="{pointed_at["name"]}"></IREF>\n'
            f'</INTERREF>\n'
            f'</ROW>'
        )
        by_source.setdefault(holder["name"], []).append(row_xml)
        row_counter += 1
    return by_source


def build_instances_for_submodel(items, intermodel_by_name, obj_counter, start_index=1):
    """items: list of (mural_id, info) belonging to ONE submodel.
    obj_counter is the NEXT free global object id (shared across all
    submodels in this file) - caller must thread this through so ids
    never collide between different <MODEL> blocks.
    """
    instances_xml = []
    index = start_index
    for mural_id, info in items:
        cls = info["cls"]
        template = INSTANCE_ATTR_TEMPLATES.get(cls, DEFAULT_TEMPLATE)
        position = f"NODE x:{info['x']}cm y:{info['y']}cm w:{info['w']}cm h:{info['h']}cm index:{index}"
        rows = intermodel_by_name.get(info["name"])
        if rows:
            intermodel_xml = f'<RECORD name="Intermodel-Relations">\n{"".join(rows)}\n</RECORD>'
        else:
            intermodel_xml = '<RECORD name="Intermodel-Relations"></RECORD>'
        body = template.format(position=position, desc=info["description"], intermodel_relations=intermodel_xml)
        obj_id = f"obj.{obj_counter}"
        instances_xml.append(
            f'<INSTANCE id="{obj_id}" class="{info["real_cls"]}" name="{info["name"]}">\n{body}\n</INSTANCE>'
        )
        obj_counter += 1
        index += 1
    return instances_xml, index, obj_counter


def build_connectors_xml(same_model_arrows, start_index, con_counter):
    connectors_xml = []
    index = start_index
    for entry in same_model_arrows:
        src, tgt = entry["src"], entry["tgt"]
        # IMPORTANT: Type must be looked up using the SAME pair that ends up
        # as FROM/TO below (FROM=tgt, TO=src), not the raw Mural src/tgt -
        # otherwise the relation label (e.g. "Output" vs "Input") ends up
        # describing the opposite direction from the one actually stored.
        rel_type, label_used_as_type = infer_relation_type(
            tgt["real_cls"], src["real_cls"], entry["stroke"], cross_model=False, label_text=entry.get("label_text")
        )
        # If the arrow had a label but it wasn't a recognized Type value,
        # put it in Description instead of discarding it - confirmed by a
        # real export where an Organizational Unit "part of" relation has
        # an empty Type and "part of" living in Description.
        label_text = entry.get("label_text")
        description = ""
        if label_text and not label_used_as_type:
            description = label_text.strip()
        con_id = f"con.{con_counter}"
        # Empirically-confirmed swap: Mural start/end come out reversed vs 4EM FROM/TO.
        connectors_xml.append(
            f'<CONNECTOR id="{con_id}" class="4EM_Relation">\n'
            f'<FROM instance="{tgt["name"]}" class="{tgt["real_cls"]}"></FROM>\n'
            f'<TO instance="{src["name"]}" class="{src["real_cls"]}"></TO>\n'
            f'<ATTRIBUTE name="Positions" type="STRING">EDGE 0 index:{index}</ATTRIBUTE>\n'
            f'<ATTRIBUTE name="Type" type="ENUMERATION">{rel_type}</ATTRIBUTE>\n'
            f'<ATTRIBUTE name="Description" type="LONGSTRING">{description}</ATTRIBUTE>\n'
            f'<ATTRIBUTE name="IR" type="ENUMERATION">False</ATTRIBUTE>\n'
            f'</CONNECTOR>'
        )
        con_counter += 1
        index += 1
    return connectors_xml, con_counter


MODEL_BLOCK = """<MODEL id="{model_id}" name="{model_name}" version="" modeltype="{modeltype}" libtype="{libtype}" applib="4EM current">
<MODELATTRIBUTES>
<ATTRIBUTE name="Author" type="STRING">Admin</ATTRIBUTE>
<ATTRIBUTE name="Number of objects and relations" type="INTEGER">{count}</ATTRIBUTE>
</MODELATTRIBUTES>
{body}
</MODEL>"""

FILE_HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE ADOXML SYSTEM "adoxml31.dtd">
<ADOXML version="3.1" date="06.07.2026" time="00:00" database="fouremdb" username="Admin" adoversion="Version 1.8">
<MODELS>
{model_blocks}
</MODELS>
</ADOXML>
"""


def convert(mural_json_path, output_xml_path):
    widgets = load_widgets(mural_json_path)
    registry = build_registry(widgets)
    if not registry:
        raise SystemExit("No classifiable sticky notes found — check COLOR_SHAPE_TO_CLASS mapping.")

    same_model_arrows, cross_model_arrows = collect_arrows(widgets, registry)
    intermodel_by_name = build_intermodel_relations_by_source(cross_model_arrows)

    # Partition registry entries by submodel (modeltype)
    by_submodel = {}
    for mural_id, info in registry.items():
        by_submodel.setdefault(info["modeltype"], []).append((mural_id, info))

    model_blocks = []
    mod_counter = 90001
    obj_counter = 20000
    con_counter = 30000
    for modeltype, items in by_submodel.items():
        libtype = items[0][1]["libtype"]
        instances_xml, next_index, obj_counter = build_instances_for_submodel(
            items, intermodel_by_name, obj_counter
        )

        # same-model connectors only apply within THIS submodel's instances
        names_here = {info["name"] for _, info in items}
        arrows_here = [a for a in same_model_arrows
                       if a["src"]["name"] in names_here and a["tgt"]["name"] in names_here]
        connectors_xml, con_counter = build_connectors_xml(arrows_here, next_index, con_counter)

        body = "\n".join(instances_xml + connectors_xml)
        count = len(instances_xml) + len(connectors_xml)
        model_blocks.append(MODEL_BLOCK.format(
            model_id=f"mod.{mod_counter}",
            model_name=modeltype,     # kept identical to modeltype so IREF matching is guaranteed
            modeltype=modeltype,
            libtype=libtype,
            count=count,
            body=body,
        ))
        mod_counter += 1

    xml_out = FILE_HEADER.format(model_blocks="\n".join(model_blocks))
    Path(output_xml_path).write_text(xml_out, encoding="utf-8")
    print(f"Wrote {output_xml_path}: {len(by_submodel)} submodel(s), "
          f"{len(registry)} instances, {len(same_model_arrows)} same-model relation(s), "
          f"{len(cross_model_arrows)} cross-model relation(s)")


def validate_board(mural_json_path):
    """
    Pre-flight check: scans a Mural export and reports classification and
    structural issues WITHOUT generating any XML. Meant to be run before a
    real conversion attempt, so problems are caught here instead of as a
    failed/wrong ADOxx import discovered later.

    Reports:
      - (color, shape) combos present on the board that aren't in
        COLOR_SHAPE_TO_CLASS, grouped and counted (not one line per sticky)
      - dangling arrows (missing startRefId/endRefId, or pointing at an
        unclassified/nonexistent widget)
      - arrow labels that don't match ANY known relation type anywhere
        (checked globally here, since submodel-pair context isn't resolved
        until classification succeeds - a soft warning, not necessarily
        wrong, since unmatched labels are still valid as Description text)
    """
    widgets = load_widgets(mural_json_path)
    all_ids = {w["id"] for w in widgets if w.get("type") == "sticky note"}

    print(f"=== Pre-flight validation: {mural_json_path} ===\n")

    # 1. Unclassified (color, shape) combinations, grouped
    unclassified = {}
    classified_count = 0
    for w in widgets:
        if w.get("type") != "sticky note":
            continue
        color = w.get("style", {}).get("backgroundColor")
        shape = w.get("shape")
        if (color, shape) in COLOR_SHAPE_TO_CLASS:
            classified_count += 1
            continue
        key = (color, shape)
        example = strip_html(w.get("htmlText", "")) or "(no text)"
        unclassified.setdefault(key, []).append(example)

    print(f"Stickies classified OK: {classified_count}")
    if unclassified:
        print(f"Stickies with UNRECOGNIZED (color, shape): {sum(len(v) for v in unclassified.values())}")
        for (color, shape), examples in sorted(unclassified.items(), key=lambda x: -len(x[1])):
            print(f"  {color!r:12} + {shape!r:10} x{len(examples):<3} e.g. {examples[0]!r}")
        print("  -> Fix: either recolor these stickies to a color/shape already in")
        print("     COLOR_SHAPE_TO_CLASS, or add a new entry for the class they represent.")
    else:
        print("All stickies have a recognized (color, shape).")

    # 2. Dangling / broken arrows
    print()
    dangling = []
    for w in widgets:
        if w.get("type") != "arrow":
            continue
        src_id, tgt_id = w.get("startRefId"), w.get("endRefId")
        label = extract_arrow_label(w)
        if not src_id or not tgt_id:
            dangling.append((w["id"], src_id, tgt_id, label, "missing endpoint"))
        elif src_id not in all_ids or tgt_id not in all_ids:
            dangling.append((w["id"], src_id, tgt_id, label, "endpoint not a sticky note"))
    if dangling:
        print(f"DANGLING arrows: {len(dangling)}")
        for wid, s, e, label, reason in dangling:
            print(f"  arrow {wid} ({reason}) label={label!r} start={s} end={e}")
        print("  -> Fix: reconnect these arrows to real stickies in Mural before exporting.")
    else:
        print("No dangling arrows.")

    # 3. Arrow labels that don't match any known relation type at all
    print()
    unrecognized_labels = []
    for w in widgets:
        if w.get("type") != "arrow":
            continue
        label = extract_arrow_label(w)
        if label and label.strip() not in KNOWN_RELATION_TYPES_EXACT \
                and label.strip().lower() not in KNOWN_RELATION_TYPES_CI:
            unrecognized_labels.append((w["id"], label))
    if unrecognized_labels:
        print(f"Arrow labels not matching any known relation type: {len(unrecognized_labels)}")
        for wid, label in unrecognized_labels:
            print(f"  arrow {wid}: {label!r}")
        print("  -> Not necessarily wrong - unmatched labels become the connector's")
        print("     Description instead of Type. Just confirm that's what you intended.")
    else:
        print("All arrow labels either match a known relation type or have no label.")

    print(f"\n=== End validation ===")


def check_palette_integrity():
    """
    Developer-time self-check: re-parses THIS FILE's own source to detect
    duplicate (color, shape) keys in COLOR_SHAPE_TO_CLASS or duplicate class
    keys in INSTANCE_ATTR_TEMPLATES. Python dict literals silently keep only
    the LAST duplicate entry with no error - this caught real bugs during
    development (a duplicate "Process" template silently discarded an
    earlier one). Run this after editing either table.
    """
    import re as _re
    source = Path(__file__).read_text(encoding="utf-8")

    def _find_duplicates(block_start_marker, block_end_marker, key_pattern):
        start = source.index(block_start_marker)
        end = source.index(block_end_marker, start)
        block = source[start:end]
        keys = _re.findall(key_pattern, block, _re.M)
        seen, dupes = set(), []
        for k in keys:
            if k in seen:
                dupes.append(k)
            seen.add(k)
        return dupes

    color_shape_dupes = _find_duplicates(
        "COLOR_SHAPE_TO_CLASS = {", "\n}\n",
        r'\(\"#[0-9A-Fa-f]{8}\", \"\w+\"\)'
    )
    template_dupes = _find_duplicates(
        "INSTANCE_ATTR_TEMPLATES = {", "\n}\n",
        r'^    \"([^\"]+)\":'
    )
    if color_shape_dupes:
        print(f"[INTEGRITY] Duplicate (color, shape) keys in COLOR_SHAPE_TO_CLASS: {color_shape_dupes}")
    if template_dupes:
        print(f"[INTEGRITY] Duplicate class keys in INSTANCE_ATTR_TEMPLATES: {template_dupes}")
    if not color_shape_dupes and not template_dupes:
        print("[INTEGRITY] No duplicate keys found in COLOR_SHAPE_TO_CLASS or INSTANCE_ATTR_TEMPLATES.")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--check-integrity":
        check_palette_integrity()
    elif len(sys.argv) == 3 and sys.argv[2] == "--validate":
        validate_board(sys.argv[1])
    elif len(sys.argv) == 3:
        convert(sys.argv[1], sys.argv[2])
    else:
        print("Usage:")
        print("  python mural_to_4em.py <mural_export.json> <output.xml>       # convert")
        print("  python mural_to_4em.py <mural_export.json> --validate         # pre-flight check only")
        print("  python mural_to_4em.py --check-integrity                      # dev-time table self-check")
        sys.exit(1)