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
k-?means|k-?nearest|\bknn\b|logistic regression|linear regression|regression analysis|regression model|
multiple regression|anova|chi-?square|\bt-?test|p-?value|null hypothesis|hypothesis test\w*|confidence interval|
standard deviation|standard error|normal distribution|poisson distribution|binomial distribution|
bayes\w*|bayesian|markov chain|monte carlo|time series|arima|statistical significance|correlation coefficient|
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
# Things that look technical but aren't data science here.
NOT_RE = re.compile(r"\b(snake|reptile|zoo|bear|panda express|giant panda|spark plug|sparkling|keras? (stone|car))\b"
                    r"|algorithm (of|for) (life|love)|dating|horoscope", re.I)


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
        if 8 <= len(q) <= 110 and lq not in seen and STRONG_RE.search(q) and not NOT_RE.search(q):
            seen.add(lq)
            out.append(lq)
    dest = Path(__file__).resolve().parents[1] / "data" / "themes" / "data_science.txt.gz"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(dest, "wt", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    print(f"{len(out)} queries -> {dest}")


if __name__ == "__main__":
    main(sys.argv[1:])
