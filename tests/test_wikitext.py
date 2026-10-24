"""Finding the sentence that carries a link, from raw wikitext."""

from __future__ import annotations

from app.wikitext import MAX_SENTENCE, find_link_context

ARTICLE = """{{Short description|Edible fruit}}
{{Infobox plant
| name = Banana
| region = [[Southeast Asia]]
}}
A '''banana''' is an elongated, edible [[fruit]].<ref>{{cite web|title=x}}</ref> \
It is grown in [[India|the Indian subcontinent]] and elsewhere. Bananas vary in size.

== Taxonomy ==
Linnaeus named [[Musa (genus)|''Musa'']] and placed [[plantain]]s in it.<ref name="a" />

{| class="wikitable"
| [[Kerala]] || yields
|}
"""


def sentence(context) -> str:
    return context.before + context.link + context.after


def test_finds_the_sentence_and_marks_the_linked_words():
    context = find_link_context(ARTICLE, "Fruit")

    assert context.where == "prose"
    assert context.link == "fruit"
    assert sentence(context) == "A banana is an elongated, edible fruit."
    assert context.section is None


def test_a_piped_link_marks_the_words_shown_not_the_title():
    context = find_link_context(ARTICLE, "India")

    assert context.link == "the Indian subcontinent"
    assert sentence(context) == (
        "It is grown in the Indian subcontinent and elsewhere."
    )


def test_letters_trailing_a_link_belong_to_it():
    assert find_link_context(ARTICLE, "Plantain").link == "plantains"


def test_reports_the_section_the_sentence_sits_in():
    context = find_link_context(ARTICLE, "Musa (genus)")

    assert context.section == "Taxonomy"
    assert context.link == "Musa"
    assert "<ref" not in sentence(context)


def test_matches_titles_the_way_mediawiki_does():
    """First letter case-insensitive; spaces and underscores interchangeable."""
    text = "See [[southeast_Asia]] today."
    assert find_link_context(text, "Southeast Asia").link == "southeast_Asia"


def test_a_link_only_in_an_infobox_or_table_is_reported_as_such():
    assert find_link_context(ARTICLE, "Southeast Asia").where == "template"
    assert find_link_context(ARTICLE, "Kerala").where == "template"


def test_a_link_absent_from_the_wikitext_is_elsewhere():
    """Navigation boxes are transcluded, so their links never appear."""
    assert find_link_context(ARTICLE, "Mathematics").where == "elsewhere"
    assert find_link_context("", "Fruit").where == "elsewhere"


def test_a_long_sentence_is_cut_to_a_window_around_the_link():
    filler = " ".join(["word"] * 200)
    text = f"Start {filler} the [[Target]] and then {filler} end."

    context = find_link_context(text, "Target")

    assert context.link == "Target"
    assert len(sentence(context)) <= MAX_SENTENCE + 2
    assert context.before.startswith("…")
    assert context.after.endswith("…")


def test_a_link_label_longer_than_the_window_is_cut_alone():
    label = "x" * (MAX_SENTENCE + 50)
    context = find_link_context(f"Before [[Target|{label}]] after.", "Target")

    assert context.link == label[:MAX_SENTENCE]
    assert (context.before, context.after) == ("…", "…")
