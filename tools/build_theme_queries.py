"""Maintainer tool: pull real data-science searches out of the MS MARCO and GooAQ query sets.

Usage:  python tools/build_theme_queries.py msmarco_queries.parquet gooaq_0.parquet gooaq_1.parquet
Needs pyarrow. Writes data/themes/data_science.txt.gz (one real query per line).
Parquet files: huggingface.co/datasets/BeIR/msmarco (queries) and sentence-transformers/gooaq (pair).
"""
from __future__ import annotations

import gzip
import re
import sys
from pathlib import Path

# Terms that are data science on their own.
STRONG = r"""machine learning|deep learning|neural net(work)?s?|data scien\w*|data analy(st|sis|tics)|
data engineer\w*|data set|dataset|data mining|big data|data warehous\w*|data lake|data pipeline|
artificial intelligence|\bai model|large language model|\bllms?\b|\bgpt-?\d?\b|chatgpt|transformer model|
natural language processing|\bnlp\b|computer vision|reinforcement learning|supervised learning|
unsupervised learning|random forest|decision tree|gradient boost\w*|xgboost|lightgbm|\bsvm\b|support vector|
k-?means|k-?nearest|\bknn\b|logistic regression|linear regression|multiple regression|regression analysis|regression model|
multiple regression|anova|chi-?square|\bt-?test|p-?value|null hypothesis|hypothesis test\w*|confidence interval|
standard deviation|standard error|normal distribution|poisson distribution|binomial distribution|
bayes\w*|bayesian|markov chain|monte carlo|time series|\barima\b(?=.*\b(models?|forecast\w*|regression|data|linear|machine)\b)|\b(models?|forecast\w*|regression|data)\b.*\barima\b|statistical significance|correlation coefficient|
covariance|variance of|principal component|\bpca\b|dimensionality reduction|overfitting|underfitting|
cross-?validation|confusion matrix|precision and recall|\bauc\b|roc curve|f1 score|feature engineering|
feature selection|hyperparameter|gradient descent|backpropagation|activation function|convolutional|cnn (model|layer|architecture)|
\brnn\b|\blstm\b|tensorflow|pytorch|keras|scikit|sklearn|numpy|pandas (dataframe|library|python|groupby)|
dataframe|jupyter|matplotlib|seaborn|\bggplot|tableau|power bi|\bsql\b|mysql|postgres\w*|sqlite|nosql|
mongodb|hadoop|apache spark|pyspark|\bspark sql|snowflake data|bigquery|\betl\b|kaggle|hugging ?face|
python (programming|code|script|library|function|list|dictionary|dict|string|class|loop|pandas|numpy)|
in python|python 3|r programming|r studio|rstudio|\bin r\b|statistic(s|al) (model|test|analysis|software)|
regression coefficient|r-?squared|least squares|clustering algorithm|classification algorithm|
algorithm|predictive model\w*|predictive analytics|probability distribution|sampling (method|distribution)|
sample size|descriptive statistics|inferential statistics|quantitative research|data visuali[sz]ation|
spreadsheet formula|excel (formula|function|pivot|vlookup)|pivot table|vlookup"""
STRONG_RE = re.compile(STRONG.replace("\n", ""), re.I)
# Basic statistics (intro-course material) is left out unless the search is also about something advanced.
BASIC_RE = re.compile(r"standard deviation|standard error|variance|\bmean\b|median|\bmode\b|average|\bt-?tests?\b|"
                      r"p-?values?|null hypothesis|alternative hypothesis|hypothesis test\w*|confidence interval|"
                      r"normal distribution|standard normal|binomial|poisson|chi-?squared?|anova|correlation|covariance|"
                      r"z-?scores?|sample size|sampling|descriptive statistic|inferential statistic|"
                      r"probability distribution|probability of|discrete random|random variable|r-?squared|"
                      r"least squares|statistical(ly)? significan\w*|regression coefficient|slope|percentile|"
                      r"quartile|interquartile|histogram|outlier|margin of error|degrees of freedom|"
                      r"type (i|1|ii|2) error|linear regression|multiple regression|regression analysis|regression line|"
                      r"statistic(s|al)? (test|analysis|software)|types of statistic|quantitative research", re.I)
ADVANCED_RE = re.compile(r"bayes\w*|markov|monte carlo|mcmc|time series|\barima\b(?=.*\b(models?|forecast\w*|regression|data|linear|machine)\b)|\b(models?|forecast\w*|regression|data)\b.*\barima\b|garch|principal component|\bpca\b|"
                         r"factor analysis|mixed (effects? )?model|hierarchical model|multilevel|survival analysis|"
                         r"bootstrap\w*|causal|instrumental variable|propensity|glm|generalized linear|"
                         r"multivariate|bias.variance|regulari[sz]ation|lasso|ridge regression|machine learning|"
                         r"deep learning|neural|kernel|gaussian process|stochastic|variational|latent|"
                         r"maximum likelihood|\bmle\b|em algorithm|spline|nonparametric|heteroskedastic\w*|"
                         r"heteroscedastic\w*|autocorrelation|cointegrat\w*|stationarity", re.I)


def is_basic_stats(q: str) -> bool:
    return bool(BASIC_RE.search(q)) and not ADVANCED_RE.search(q)


# Things that look technical but aren't data science here.
NOT_RE = re.compile(r"\b(snake|reptile|zoo|bear|panda express|giant panda|spark plug|sparkling|keras? (stone|car))\b"
                    r"|algorithm (of|for) (life|love)|dating|horoscope|ghoul|anime", re.I)


def queries(paths):
    import pyarrow.parquet as pq
    for p in paths:
        cols = pq.ParquetFile(p).schema_arrow.names
        col = "question" if "question" in cols else "text"
        for batch in pq.ParquetFile(p).iter_batches(columns=[col], batch_size=200_000):
            for q in batch.column(0).to_pylist():
                yield (q or "").strip().rstrip("?").strip()


def main(paths):
    seen, out = set(), []
    for q in queries(paths):
        lq = q.lower()
        if 8 <= len(q) <= 110 and lq not in seen and STRONG_RE.search(q) and not NOT_RE.search(q) \
                and not is_basic_stats(q):
            seen.add(lq)
            out.append(lq)
    dest = Path(__file__).resolve().parents[1] / "data" / "themes" / "data_science.txt.gz"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(dest, "wt", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    print(f"{len(out)} queries -> {dest}")


def prune(path: Path) -> None:
    """Re-apply the filters to an already-built list (no download needed)."""
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        qs = [ln.strip() for ln in fh if ln.strip()]
    keep = [q for q in qs if STRONG_RE.search(q) and not NOT_RE.search(q) and not is_basic_stats(q)]
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write("\n".join(keep) + "\n")
    print(f"{len(qs)} -> {len(keep)} queries in {path}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--prune"]:
        prune(Path(sys.argv[2]))
    else:
        main(sys.argv[1:])
