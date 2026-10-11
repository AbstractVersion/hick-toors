from dataclasses import dataclass

from app.models import Counts
from app.vote_ranking import rank, score, winner


@dataclass
class Item:
    name: str
    position: int
    counts: Counts


def item(name, position, love=0, ok=0, nogo=0):
    return Item(name, position, Counts(love=love, ok=ok, nogo=nogo))


def ranked(items):
    return [i.name for i in rank(items, lambda i: i.counts, lambda i: i.position)]


def test_scores():
    assert score(Counts(love=1)) == 2
    assert score(Counts(ok=1)) == 1
    assert score(Counts(nogo=1)) == -3
    assert score(Counts(love=2, ok=1, nogo=1)) == 2
    assert score(Counts()) == 0


def test_score_desc():
    assert ranked([item("a", 0, ok=1), item("b", 1, love=1), item("c", 2, nogo=1)]) == ["b", "a", "c"]


def test_tie_fewer_nogos_first():
    # both score 2: a = 5 ok - 1 nogo, b = 2 ok
    assert ranked([item("a", 0, ok=5, nogo=1), item("b", 1, ok=2)]) == ["b", "a"]


def test_tie_then_more_loves():
    # both score 2, no nogos: a = 2 ok, b = 1 love
    assert ranked([item("a", 0, ok=2), item("b", 1, love=1)]) == ["b", "a"]


def test_tie_then_lower_position():
    assert ranked([item("a", 2, love=1), item("b", 0, love=1), item("c", 1, love=1)]) == ["b", "c", "a"]


def test_order_of_tie_breaks():
    # equal score 4: nogos beat loves (x has more loves but a nogo -> after y)
    x = item("x", 0, love=3, ok=1, nogo=1)  # 6 + 1 - 3 = 4
    y = item("y", 1, ok=4)                  # 4
    assert ranked([x, y]) == ["y", "x"]


def test_unvoted_rank_by_position_with_score_zero():
    items = [item("a", 0), item("b", 1, nogo=1), item("c", 2), item("d", 3, ok=1)]
    assert ranked(items) == ["d", "a", "c", "b"]
    assert score(items[0].counts) == 0


def test_rank_does_not_mutate_input():
    items = [item("a", 1), item("b", 0)]
    rank(items, lambda i: i.counts, lambda i: i.position)
    assert [i.name for i in items] == ["a", "b"]


def test_winner_first_with_a_vote():
    items = rank([item("a", 0), item("b", 1, nogo=1), item("c", 2, ok=1)],
                 lambda i: i.counts, lambda i: i.position)
    assert winner(items, lambda i: i.counts).name == "c"


def test_winner_can_have_negative_score():
    items = rank([item("a", 0), item("b", 1, nogo=1)], lambda i: i.counts, lambda i: i.position)
    # a has no votes -> skipped even though it ranks first
    assert winner(items, lambda i: i.counts).name == "b"


def test_no_winner_without_votes():
    assert winner([item("a", 0), item("b", 1)], lambda i: i.counts) is None
    assert winner([], lambda i: i.counts) is None
