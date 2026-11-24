"""Phase 3 analytics engine: every result is checked against an independent pandas computation."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from app.analytics.engine import QueryInvalid, compile_query, run_query, validate
from app.analytics.spec import QuerySpec
from app.blueprint.context import DatasetContext
from app.ingestion.pipeline import ingest
from app.profiling.profiler import profile_table
from tests.fixtures.make_samples import messy_call_log_xlsx


@pytest.fixture(scope="module")
def ctx():
    t = ingest(messy_call_log_xlsx()[0], "calls.xlsx").tables[0]
    return DatasetContext(dataset_id=1, version=1, version_id=1, name=t.name, columns=t.columns,
                          profile=profile_table(t), lineage=t.lineage, df=t.clean)


def q(ctx, **kw):
    return run_query(QuerySpec(dataset_id=1, **kw), ctx)


def rows(res):
    return [dict(zip([c.key for c in res.columns], r)) for r in res.rows]


COST, BRANCH, AGENT, TYPE, STATUS, DUR, DATE = "col_8", "branch", "agent", "type", "col_9", "duration", "date"


def test_totals_match_pandas(ctx):
    df = ctx.df
    res = q(ctx, measures=[{"aggregation": "sum", "column": COST}, {"aggregation": "count_rows"},
                           {"aggregation": "average", "column": DUR}, {"aggregation": "count_distinct", "column": AGENT}])
    r = rows(res)[0]
    assert r["m0"] == df[COST].sum() and r["m1"] == len(df)
    assert r["m2"] == pytest.approx(df[DUR].mean()) and r["m3"] == df[AGENT].nunique()
    assert res.matched_rows == res.total_rows == len(df)
    assert res.sql.startswith("SELECT SUM(\"col_8\") AS m0") and "?" not in res.sql
    assert [c.format for c in res.columns] == ["lak", "number", "duration", "number"]


def test_ranking_matches_pandas(ctx):
    res = q(ctx, measures=[{"aggregation": "sum", "column": COST}], dimensions=[{"column": BRANCH}], limit=3)
    exp = ctx.df.groupby(BRANCH)[COST].sum().sort_values(ascending=False)
    assert [(r["d0"], r["m0"]) for r in rows(res)] == list(exp.head(3).items())
    assert res.truncated is (len(exp) > 3) and res.columns[0].column == BRANCH
    asc = q(ctx, measures=[{"aggregation": "sum", "column": COST}], dimensions=[{"column": BRANCH}],
            sort={"by": "measure", "index": 0, "direction": "asc"}, limit=1)
    assert rows(asc)[0]["d0"] == exp.index[-1]


def test_trend_with_change_matches_pandas(ctx):
    res = q(ctx, measures=[{"aggregation": "count_rows"}], dimensions=[{"column": DATE, "grain": "month"}], analysis="change")
    exp = ctx.df.groupby(ctx.df[DATE].dt.to_period("M")).size()
    got = rows(res)
    assert [r["d0"] for r in got] == [p.start_time.date().isoformat() for p in exp.index]
    assert [r["m0"] for r in got] == list(exp.values)
    assert got[0]["change"] is None
    for prev, cur in zip(got, got[1:]):
        assert cur["change"] == cur["m0"] - prev["m0"]
        assert cur["change_pct"] == pytest.approx((cur["m0"] - prev["m0"]) / prev["m0"])


def test_share_is_of_all_groups_even_when_limited(ctx):
    res = q(ctx, measures=[{"aggregation": "count_rows"}], dimensions=[{"column": TYPE}], analysis="share", limit=2)
    counts = ctx.df[TYPE].value_counts(dropna=False)
    for r in rows(res):
        assert r["share"] == pytest.approx(counts[r["d0"]] / len(ctx.df))


def test_filters_and_filtered_measures_match_pandas(ctx):
    df = ctx.df
    res = q(ctx, measures=[{"aggregation": "average", "column": DUR},
                           {"aggregation": "share_of_rows", "filter": {"column": STATUS, "op": "eq", "values": ["ສຳເລັດ"]}},
                           {"aggregation": "sum", "column": COST, "filter": {"column": TYPE, "op": "in", "values": ["ຮ້ອງຮຽນ", "ຂາຍ"]}}],
            dimensions=[{"column": AGENT}],
            filters=[{"column": DATE, "op": "between", "values": ["2025-02-01", "2025-02-28"]},
                     {"column": DUR, "op": "gt", "values": [60]}])
    sub = df[(df[DATE] >= "2025-02-01") & (df[DATE] < "2025-03-01") & (df[DUR] > 60)]
    assert res.matched_rows == len(sub)
    for r in rows(res):
        g = sub[sub[AGENT] == r["d0"]]
        assert r["m0"] == pytest.approx(g[DUR].mean())
        assert r["m1"] == pytest.approx((g[STATUS] == "ສຳເລັດ").mean())
        exp_sum = g.loc[g[TYPE].isin(["ຮ້ອງຮຽນ", "ຂາຍ"]), COST].sum()
        assert (r["m2"] or 0) == exp_sum
    assert "TIMESTAMP '2025-03-01 00:00:00'" in res.sql  # date-only upper bound includes the whole day


def test_text_ops(ctx):
    df = ctx.df
    res = q(ctx, measures=[{"aggregation": "count_rows"}], filters=[{"column": AGENT, "op": "contains", "values": ["kham"]}])
    assert rows(res)[0]["m0"] == df[AGENT].str.contains("kham", case=False, na=False).sum()
    res = q(ctx, measures=[{"aggregation": "count_rows"}], filters=[{"column": BRANCH, "op": "not_in", "values": ["ປາກເຊ"]}])
    assert rows(res)[0]["m0"] == (df[BRANCH].fillna("") != "ປາກເຊ").sum()  # blanks are "not in" too
    res = q(ctx, measures=[{"aggregation": "count_rows"}], filters=[{"column": AGENT, "op": "is_null"}])
    assert rows(res)[0]["m0"] == df[AGENT].isna().sum()


def test_blank_values_semantics():
    df = pd.DataFrame({"b": pd.array(["a", None, "c", "a"], dtype="string")})
    c = DatasetContext(dataset_id=4, version=1, version_id=44, name="t", columns=[{"key": "b", "label": "B", "logical_type": "text"}],
                       profile={}, lineage={}, df=df)

    def count(op, values=()):
        return rows(run_query(QuerySpec(dataset_id=4, measures=[{"aggregation": "count_rows"}],
                                        filters=[{"column": "b", "op": op, "values": list(values)}]), c))[0]["m0"]

    assert count("not_in", ["a"]) == 2 and count("neq", ["a"]) == 2  # blanks are not "a"
    assert count("eq", ["a"]) == 2 and count("is_null") == 1 and count("not_null") == 3
    grouped = rows(run_query(QuerySpec(dataset_id=4, measures=[{"aggregation": "count_rows"}], dimensions=[{"column": "b"}]), c))
    assert {r["d0"]: r["m0"] for r in grouped} == {"a": 2, "c": 1, None: 1}  # blank group kept, not dropped


def test_anomaly_detects_planted_spike():
    days = pd.date_range("2025-01-01", periods=60, freq="D")
    rng = np.random.default_rng(7)
    per_day = rng.integers(8, 13, size=60)
    per_day[40] = 60  # the spike
    dates = np.repeat(days.values, per_day)
    dates = np.delete(dates, np.where(dates == days.values[20]))  # a day with no calls at all
    df = pd.DataFrame({"date": pd.to_datetime(dates), "cost": 1000})
    cols = [{"key": "date", "label": "Date", "logical_type": "date"}, {"key": "cost", "label": "Cost", "logical_type": "integer", "unit": "LAK"}]
    c = DatasetContext(dataset_id=9, version=1, version_id=99, name="t", columns=cols, profile={}, lineage={}, df=df)
    res = run_query(QuerySpec(dataset_id=9, measures=[{"aggregation": "count_rows"}], dimensions=[{"column": "date", "grain": "day"}],
                              analysis="anomaly"), c)
    got = rows(res)
    assert len(got) == 60  # the empty day was generated as a real zero
    flagged = {r["d0"] for r in got if r["anomaly"]}
    assert "2025-02-10" in flagged and "2025-01-21" in flagged  # spike and the zero-call day
    assert len(flagged) == 2
    typical = float(np.median(np.where(np.arange(60) == 20, 0, per_day)))
    assert got[0]["typical"] == pytest.approx(typical)


def test_anomaly_on_sparse_series_uses_mean_deviation(ctx):
    res = q(ctx, measures=[{"aggregation": "count_rows"}], dimensions=[{"column": DATE, "grain": "day"}], analysis="anomaly",
            filters=[{"column": BRANCH, "op": "in", "values": ["ປາກເຊ", "ວຽງຈັນ"]}])
    assert all(r["z"] is not None for r in rows(res))  # MAD is 0 here; the fallback scale applies
    assert res.matched_rows == ctx.df[BRANCH].isin(["ປາກເຊ", "ວຽງຈັນ"]).sum()


@pytest.mark.parametrize("spec,msg", [
    ({"measures": [{"aggregation": "sum", "column": AGENT}]}, "needs a number"),
    ({"measures": [{"aggregation": "count_rows"}], "dimensions": [{"column": "nope"}]}, "unknown group-by column"),
    ({"measures": [{"aggregation": "count_rows"}], "dimensions": [{"column": BRANCH, "grain": "month"}]}, "time grain needs a date"),
    ({"measures": [{"aggregation": "count_rows"}], "filters": [{"column": BRANCH, "op": "eq", "values": ["Atlantis"]}]}, "not found"),
    ({"measures": [{"aggregation": "count_rows"}], "filters": [{"column": BRANCH, "op": "gt", "values": ["a"]}]}, "needs a number, duration or date"),
    ({"measures": [{"aggregation": "count_rows"}], "filters": [{"column": DATE, "op": "gte", "values": ["soon"]}]}, "needs a date"),
    ({"measures": [{"aggregation": "count_rows"}], "dimensions": [{"column": BRANCH}], "analysis": "change"}, "needs a date column"),
    ({"measures": [{"aggregation": "share_of_rows"}]}, "needs a filter"),
    ({"measures": [{"aggregation": "sum", "column": "col_1"}]}, "is an identifier"),
    ({"measures": [{"aggregation": "count_rows"}], "sort": {"by": "measure", "index": 2}}, "does not exist"),
])
def test_validation_errors(ctx, spec, msg):
    with pytest.raises(QueryInvalid, match=msg):
        validate(QuerySpec(dataset_id=1, **spec), ctx)


def test_values_are_bound_not_interpolated():
    nasty = "x'); DROP TABLE data; --"
    df = pd.DataFrame({'we"ird': [nasty, "ok", "ok"], "n": [1, 2, 3]})
    cols = [{"key": 'we"ird', "label": "W", "logical_type": "text"}, {"key": "n", "label": "N", "logical_type": "integer"}]
    c = DatasetContext(dataset_id=5, version=1, version_id=55, name="t", columns=cols, profile={}, lineage={}, df=df)
    spec = validate(QuerySpec(dataset_id=5, measures=[{"aggregation": "sum", "column": "n"}],
                              filters=[{"column": 'we"ird', "op": "eq", "values": [nasty]}]), c)
    sql, params, disp = compile_query(spec, c)
    assert nasty not in sql and params[0] == nasty and '"we""ird"' in sql
    assert rows(run_query(spec, c))[0]["m0"] == 1
    assert "'x''); DROP TABLE data; --'" in disp  # display form is correctly quoted


def test_spec_rejects_unknown_fields():
    with pytest.raises(Exception):
        QuerySpec(dataset_id=1, measures=[{"aggregation": "count_rows"}], sql="SELECT 1")


def test_result_types_are_json_ready(ctx):
    res = q(ctx, measures=[{"aggregation": "maximum", "column": DATE}, {"aggregation": "minimum", "column": DUR}])
    r = rows(res)[0]
    assert r["m0"] == ctx.df[DATE].max().date().isoformat() and r["m1"] == ctx.df[DUR].min()
    assert isinstance(res.computed_at, dt.datetime)
