"""Interest personas: coherent, harmless themes for the noise (optional).

Idea from NoisyCompany and phantom-noise. Uniform randomness is easy to filter out:
a real person has a handful of steady interests. With personas on, a few themes are
active at a time (3 by default), chosen from the ones you allow, and they change every
so often (10 days by default). A share of visits and searches then follows them.

The draw is seeded from a random value saved in your settings, so it stays stable for
the period on this computer but differs from everyone else's.
"""
from __future__ import annotations

import hashlib
import random
import time

# Mainstream, safe sites and everyday searches per theme.
CATEGORIES: dict[str, dict[str, list[str]]] = {
    "Data science": {"sites": ["kaggle.com", "towardsdatascience.com", "stackoverflow.com", "stats.stackexchange.com",
                               "github.com", "pypi.org", "scikit-learn.org", "pandas.pydata.org", "numpy.org",
                               "pytorch.org", "huggingface.co", "arxiv.org", "docs.python.org", "jupyter.org",
                               "matplotlib.org", "seaborn.pydata.org", "plotly.com", "cran.r-project.org",
                               "machinelearningmastery.com", "kdnuggets.com", "statsmodels.org", "mlflow.org"],
                     "queries": ["{t}", "{t} example", "how to {t}", "{t} python", "{t} explained", "{t} pandas",
                                 "{t} stack overflow", "{t} vs", "best way to {t}"],
                     "topics": ["pandas groupby", "random forest hyperparameters", "train test split", "cross validation",
                                "logistic regression", "xgboost vs lightgbm", "pca explained", "confusion matrix",
                                "roc auc", "time series forecasting", "fine tune transformers", "sql window functions",
                                "numpy broadcasting", "matplotlib subplots", "bayesian inference", "a/b test sample size",
                                "feature engineering", "k-means clustering", "gradient descent", "jupyter shortcuts",
                                "polars vs pandas", "data leakage", "shap values", "imbalanced classes",
                                "duckdb parquet", "scikit-learn pipeline", "pytorch dataloader", "embedding similarity"]},
    "News": {"sites": ["reuters.com", "apnews.com", "bbc.com", "npr.org", "theguardian.com", "aljazeera.com",
                       "dw.com", "pbs.org"],
             "queries": ["latest {t} news", "{t} update today", "what happened with {t}", "{t} explained"],
             "topics": ["election results", "climate summit", "interest rates", "trade talks", "space launch",
                        "supreme court ruling", "wildfire", "transit strike"]},
    "Technology": {"sites": ["arstechnica.com", "theverge.com", "wired.com", "engadget.com", "cnet.com",
                             "tomshardware.com", "stackoverflow.com", "news.ycombinator.com"],
                   "queries": ["best {t} 2026", "{t} review", "how to set up {t}", "{t} vs alternatives"],
                   "topics": ["mechanical keyboard", "mesh wifi", "laptop for students", "noise cancelling headphones",
                              "home server", "password manager", "external ssd", "4k monitor"]},
    "Cooking": {"sites": ["allrecipes.com", "seriouseats.com", "bonappetit.com", "simplyrecipes.com",
                          "kingarthurbaking.com", "foodnetwork.com", "epicurious.com", "budgetbytes.com"],
                "queries": ["easy {t} recipe", "how to make {t}", "{t} without oven", "best {t} tips"],
                "topics": ["sourdough bread", "chicken curry", "banana bread", "vegetable soup", "pancakes",
                           "lasagna", "stir fry", "chocolate chip cookies"]},
    "Gardening": {"sites": ["gardeningknowhow.com", "rhs.org.uk", "almanac.com", "thespruce.com", "bhg.com",
                            "extension.umn.edu", "gardenersworld.com", "johnnyseeds.com"],
                  "queries": ["when to plant {t}", "how to grow {t}", "{t} care tips", "{t} pests"],
                  "topics": ["tomatoes", "garlic", "roses", "herbs indoors", "potatoes", "raised beds",
                             "hydrangeas", "strawberries"]},
    "Fitness": {"sites": ["runnersworld.com", "healthline.com", "verywellfit.com", "strava.com", "mayoclinic.org",
                          "menshealth.com", "womenshealthmag.com", "nerdfitness.com"],
                "queries": ["{t} for beginners", "{t} routine", "how often should i do {t}", "{t} benefits"],
                "topics": ["running", "stretching", "yoga", "cycling", "strength training", "swimming",
                           "walking", "pilates"]},
    "Travel": {"sites": ["tripadvisor.com", "lonelyplanet.com", "booking.com", "kayak.com", "skyscanner.com",
                         "rick-steves.com", "cntraveler.com", "nomadicmatt.com"],
               "queries": ["things to do in {t}", "best time to visit {t}", "{t} itinerary", "cheap flights to {t}"],
               "topics": ["lisbon", "kyoto", "vancouver", "edinburgh", "mexico city", "copenhagen",
                          "new orleans", "seoul"]},
    "Personal finance": {"sites": ["nerdwallet.com", "investopedia.com", "bankrate.com", "fool.com",
                                   "morningstar.com", "consumerfinance.gov", "marketwatch.com", "kiplinger.com"],
                         "queries": ["{t} explained", "how does {t} work", "best {t} 2026", "{t} calculator"],
                         "topics": ["index funds", "high yield savings", "credit score", "mortgage rates",
                                    "budgeting app", "roth ira", "emergency fund", "car insurance"]},
    "Science": {"sites": ["nature.com", "sciencedaily.com", "space.com", "scientificamerican.com", "phys.org",
                          "newscientist.com", "nasa.gov", "quantamagazine.org"],
                "queries": ["how does {t} work", "latest discoveries in {t}", "{t} explained simply", "what is {t}"],
                "topics": ["black holes", "crispr", "fusion energy", "exoplanets", "quantum computing",
                           "ocean currents", "volcanoes", "dinosaurs"]},
    "Movies & TV": {"sites": ["imdb.com", "rottentomatoes.com", "metacritic.com", "vulture.com", "variety.com",
                              "avclub.com", "letterboxd.com", "tvguide.com"],
                    "queries": ["{t} review", "{t} ending explained", "best {t} of the year", "{t} release date"],
                    "topics": ["sci-fi movies", "documentaries", "crime series", "animated films", "sitcoms",
                               "thrillers", "fantasy shows", "period dramas"]},
    "Sports": {"sites": ["espn.com", "cbssports.com", "bbc.com", "theathletic.com", "nba.com", "nfl.com",
                         "mlb.com", "skysports.com"],
               "queries": ["{t} scores", "{t} schedule", "{t} standings", "{t} highlights"],
               "topics": ["premier league", "nba", "nfl", "tennis", "formula 1", "mlb", "cycling tour", "rugby"]},
    "Home & DIY": {"sites": ["familyhandyman.com", "thisoldhouse.com", "homedepot.com", "lowes.com",
                             "apartmenttherapy.com", "hgtv.com", "ikea.com", "bobvila.com"],
                   "queries": ["how to fix {t}", "diy {t}", "{t} cost", "best {t} for small spaces"],
                   "topics": ["leaky faucet", "drywall hole", "shelves", "squeaky door", "paint a room",
                              "storage ideas", "window insulation", "deck stain"]},
    "Books": {"sites": ["goodreads.com", "bookshop.org", "theguardian.com", "lithub.com", "kirkusreviews.com",
                        "openlibrary.org", "penguinrandomhouse.com", "audible.com"],
              "queries": ["best {t} books", "{t} book recommendations", "books like {t}", "{t} reading list"],
              "topics": ["mystery", "historical fiction", "science fiction", "biography", "fantasy",
                         "self help", "poetry", "classic novels"]},
}


class Interests:
    """The active themes right now, and sites and searches drawn from them."""

    def __init__(self, rng: random.Random, allowed: list[str] | None = None, active: int = 3,
                 rotate_days: float = 10, salt: str = "", clock=time.time):
        self.rng, self.clock, self.salt = rng, clock, salt or "default"
        self.allowed = [c for c in (allowed or list(CATEGORIES)) if c in CATEGORIES] or list(CATEGORIES)
        self.active_n = max(1, min(int(active), len(self.allowed)))
        self.rotate_days = max(1.0, float(rotate_days))

    def active(self) -> list[str]:
        period = int(self.clock() // (self.rotate_days * 86400))
        seed = hashlib.sha256(f"{self.salt}:{period}".encode()).hexdigest()
        return random.Random(seed).sample(sorted(self.allowed), self.active_n)

    def site(self) -> str:
        return self.rng.choice(CATEGORIES[self.rng.choice(self.active())]["sites"])

    def query(self) -> str:
        cat = CATEGORIES[self.rng.choice(self.active())]
        return self.rng.choice(cat["queries"]).format(t=self.rng.choice(cat["topics"]))


# A persona is who the noise mostly looks like: a fixed mix of themes. It's followed
# "mostly" (Settings sets how much); the rest of the noise stays varied.
PERSONAS: dict[str, dict[str, float]] = {
    "Data scientist": {"Data science": 0.65, "Technology": 0.2, "Science": 0.15},
    "Developer": {"Technology": 0.6, "Data science": 0.2, "Science": 0.1, "Books": 0.1},
    "Sports fan": {"Sports": 0.7, "Fitness": 0.15, "Movies & TV": 0.15},
    "Student": {"Science": 0.3, "Books": 0.25, "Technology": 0.2, "Movies & TV": 0.15, "Personal finance": 0.1},
    "Home cook and gardener": {"Cooking": 0.5, "Gardening": 0.35, "Home & DIY": 0.15},
    "Traveller": {"Travel": 0.6, "News": 0.15, "Cooking": 0.15, "Personal finance": 0.1},
}


class Persona:
    """Same interface as Interests (site, query, active), from a fixed weighted theme mix."""

    def __init__(self, rng: random.Random, name: str):
        self.rng, self.name = rng, name
        self.mix = PERSONAS.get(name, {})

    def active(self) -> list[str]:
        return list(self.mix)

    def _theme(self) -> dict:
        return CATEGORIES[self.rng.choices(list(self.mix), weights=list(self.mix.values()))[0]]

    def site(self) -> str:
        return self.rng.choice(self._theme()["sites"])

    def query(self) -> str:
        cat = self._theme()
        return self.rng.choice(cat["queries"]).format(t=self.rng.choice(cat["topics"]))
