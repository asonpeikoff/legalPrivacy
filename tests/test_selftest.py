"""Self-test: AUC maths, separability on known cases, simulation from settings."""
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from legalprivacy.config import Profile, apply_overrides  # noqa: E402
from legalprivacy.domains import DomainPool  # noqa: E402
from legalprivacy.selftest import auc, evaluate, simulate_noise  # noqa: E402

SITES = ["news.example.com", "mail.example.org", "shop.example.net", "forum.example.io", "video.example.com"]


def _person(rng, days=14, start=1_800_000_000.0):
    """Evening browser who keeps returning to a handful of sites."""
    ev = []
    for d in range(days):
        day = start + d * 86400 - (start % 86400)
        for _ in range(rng.randint(15, 30)):
            t = day + rng.uniform(18, 23) * 3600
            ev.append((t, rng.choice(SITES)))
    return sorted(ev)


def test_auc_basics():
    assert auc([(1, 0), (2, 0), (3, 1), (4, 1)]) == 1.0
    assert auc([(1, 1), (2, 1), (3, 0), (4, 0)]) == 0.0
    assert auc([(1, 0), (1, 1)]) == 0.5


def test_noise_that_looks_like_you_scores_near_fifty():
    rng = random.Random(1)
    res = evaluate(_person(rng), _person(random.Random(2)), {})
    assert res["score"] < 62 and res["verdict"] in ("hard to tell apart", "somewhat easy to tell apart")


def test_obvious_noise_is_caught_and_the_giveaway_named():
    rng = random.Random(3)
    real = _person(rng)
    noise = [(1_800_000_000.0 + rng.uniform(0, 14 * 86400) - (1_800_000_000.0 % 86400) + 0, f"site{rng.randint(0, 5000)}.example.com")
             for _ in range(400)]
    noise = [(t - (t % 86400) + rng.uniform(2, 8) * 3600, s) for t, s in noise]          # 2am–8am, random sites
    res = evaluate(real, sorted(noise), {})
    assert res["score"] >= 90 and res["verdict"].startswith("very easy")
    assert {t["feature"] for t in res["tells"]} & {"hour", "revisit"}


def test_too_little_data_is_reported_not_guessed():
    assert "error" in evaluate([(0.0, "a.com")] * 10, [(0.0, "b.com")] * 10, {})


def test_simulation_respects_active_hours_and_window():
    p = Profile(name="t")
    apply_overrides(p, {"extras": {"active_hours": True, "active_from": 9, "active_to": 17}})
    since = time.mktime((2026, 11, 2, 0, 0, 0, 0, 0, -1))
    ev = simulate_noise(p, DomainPool(random.Random(1), None), since, since + 3 * 86400)
    assert len(ev) > 50 and all(since <= t < since + 3 * 86400 for t, _ in ev)
    assert all(9 <= time.localtime(t).tm_hour < 17 for t, _ in ev)


def test_ml_report_has_the_numbers_a_data_scientist_expects_and_predicts():
    from legalprivacy.selftest import predict
    rng = random.Random(3)
    real = _person(rng)
    noise = sorted((t - (t % 86400) + rng.uniform(2, 8) * 3600, f"site{rng.randint(0, 5000)}.example.com")
                   for t, _ in _person(random.Random(4)))
    res = evaluate(real, noise, {})
    for k in ("auc", "ci95", "folds", "roc", "confusion", "accuracy", "precision", "recall", "f1",
              "log_loss", "giveaways", "hours", "warnings", "_model"):
        assert k in res, k
    assert len(res["folds"]) == 5 and res["ci95"][0] <= res["auc"] <= res["ci95"][1] + 0.5
    c = res["confusion"]
    assert c["tp"] + c["fp"] + c["tn"] + c["fn"] == 2 * res["compared"]
    assert res["roc"][0] == [0.0, 0.0] and res["roc"][-1] == [1.0, 1.0]
    assert sum(res["hours"]["you"]) == sum(res["hours"]["noise"]) == res["compared"]
    model = res["_model"]
    assert predict(model, "site42.example.com", 4, 2, {}) > 50 > predict(model, SITES[0], 21, 2, {})
